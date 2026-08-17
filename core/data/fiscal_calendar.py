"""When a company is next expected to file, from what it has filed before.

The weekly cache refresh re-pulls every active filer whether or not
anything was filed. That is late for the few that did — a Tuesday filing
is read from a cache up to six days stale — and wasted on the thousands
that did not.

A company's rhythm is legible in its own history. The fiscal year end is
the period its last 10-K covered; the gap between a period ending and
the filing that reports it is stable enough per company to predict the
next one within days. Both come from the parquet already on disk, so
none of this needs a new network call.

What is knowable, and what is not
---------------------------------

There is no list published at the start of the year saying when every
company will report. Companies do not commit to 10-Q dates that far
ahead. What *is* fixed in advance is the fiscal year end and the
statutory deadline it implies; what is predictable is the company's own
habit; what is certain is only what has already been filed.

So every answer here is a window with a deadline, never a date, and the
actual filing is confirmed by observing it. :func:`due_for_refresh` is
built on that shape: it opens a window, and closes it when the period
turns up in the cache.

The calendar is an optimisation, never a gate. A company it cannot model
returns ``None`` from :func:`build_profile` and is then treated as
always due — the old behaviour, unchanged. Nothing is ever dropped from
the refresh because the calendar failed to understand it.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final

from core.logger import get_logger

logger = get_logger("core.data.fiscal_calendar")

#: Outer statutory bound for a 10-Q, in days after the period ends.
#: 40 for a large accelerated filer, 45 otherwise; filer status is not
#: in the data, so the looser one is used. It bounds "overdue" only —
#: "expected" comes from the company's own median lag — and erring long
#: means a slow-but-legal filer is not reported as late.
QUARTERLY_DEADLINE_DAYS: Final[int] = 45

#: Same, for a 10-K: 60 / 75 / 90 by filer status, so 90.
ANNUAL_DEADLINE_DAYS: Final[int] = 90

#: Forms with a predictable period. An 8-K reports an event and has no
#: schedule, which is why it is absent — asking when the next one is due
#: is not a question with an answer.
SCHEDULED_FORMS: Final[tuple[str, ...]] = ("10-Q", "10-K")

_DEADLINE_DAYS: Final[dict[str, int]] = {
    "10-Q": QUARTERLY_DEADLINE_DAYS,
    "10-K": ANNUAL_DEADLINE_DAYS,
}


@dataclass(frozen=True)
class Filing:
    """One filing, reduced to the three fields the calendar reads."""

    form: str
    period_end: date
    filed: date

    @property
    def lag_days(self) -> int:
        return (self.filed - self.period_end).days


@dataclass(frozen=True)
class ExpectedFiling:
    """A window, not a date. See the module docstring on why."""

    form: str
    period_end: date
    #: What this company usually does: period end plus its median lag.
    expected_filing: date
    #: The statutory outer bound. Past this, the filing is late.
    deadline: date


@dataclass(frozen=True)
class FiscalProfile:
    """One company's reporting rhythm, derived from its own filings."""

    ticker: str
    #: (month, day) of the fiscal year end, from the last annual report.
    #: Not always December: a January year end is ordinary in retail,
    #: and reading it as calendar quarters would put every expectation
    #: a month out.
    fiscal_year_end: tuple[int, int]
    #: Form -> median days between period end and filing.
    median_lag_days: dict[str, int]
    #: Form -> the most recent period already reported. This is what
    #: closes the refresh window: once the period is in the cache, the
    #: company is not due again until the next one.
    last_period_end: dict[str, date]

    def to_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "fiscal_year_end": list(self.fiscal_year_end),
            "median_lag_days": dict(sorted(self.median_lag_days.items())),
            "last_period_end": {
                k: v.isoformat() for k, v in sorted(self.last_period_end.items())
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FiscalProfile:
        month, day = (int(x) for x in data["fiscal_year_end"])
        return cls(
            ticker=str(data["ticker"]),
            fiscal_year_end=(month, day),
            median_lag_days={
                str(k): int(v) for k, v in (data.get("median_lag_days") or {}).items()
            },
            last_period_end={
                str(k): date.fromisoformat(str(v))
                for k, v in (data.get("last_period_end") or {}).items()
            },
        )


def build_profile(ticker: str, filings: Sequence[Filing]) -> FiscalProfile | None:
    """Derive ``ticker``'s rhythm, or ``None`` when it cannot be read.

    ``None`` is returned rather than a default whenever there is no
    annual report to anchor the fiscal year end on. A guessed December
    year end would put every expectation for a January filer a month
    wrong, and a wrong date is worse than no date: the caller treats
    ``None`` as "always refresh", which is exactly the old behaviour.
    """
    annual = [f for f in filings if f.form == "10-K"]
    if not annual:
        logger.debug(f"{ticker}: no 10-K in history, no fiscal year end to anchor on")
        return None

    latest_annual = max(annual, key=lambda f: f.period_end)
    year_end = (latest_annual.period_end.month, latest_annual.period_end.day)

    lags: dict[str, int] = {}
    last_period: dict[str, date] = {}
    for form in SCHEDULED_FORMS:
        of_form = [f for f in filings if f.form == form]
        if not of_form:
            continue
        # Median, not mean: one restatement filed 200 days late is an
        # incident, not a rhythm, and a mean would carry it forward into
        # every future expectation.
        lags[form] = int(statistics.median(f.lag_days for f in of_form))
        last_period[form] = max(f.period_end for f in of_form)

    return FiscalProfile(
        ticker=ticker.upper(),
        fiscal_year_end=year_end,
        median_lag_days=lags,
        last_period_end=last_period,
    )


def _year_ends(year_end: tuple[int, int], around: date) -> list[date]:
    """The fiscal year ends near ``around`` — the periods a 10-K covers.

    Separate from :func:`_quarter_ends` because an annual report covers
    a year. Feeding it quarter ends made a December filer look due for a
    10-K every March, which then made every company permanently due and
    turned the calendar into a no-op wearing a schedule.
    """
    month, day = year_end
    out: list[date] = []
    for year in (around.year - 1, around.year, around.year + 1):
        try:
            out.append(date(year, month, day))
        except ValueError:  # 29 February in a non-leap year
            out.append(date(year, month, 28))
    return sorted(out)


def _quarter_ends(year_end: tuple[int, int], around: date) -> list[date]:
    """The four period ends of the fiscal year containing ``around``.

    Built by stepping back in three-month strides from the year end so a
    January or June year end produces its own quarters rather than the
    calendar's.
    """
    month, day = year_end
    out: list[date] = []
    for year in (around.year - 1, around.year, around.year + 1):
        try:
            anchor = date(year, month, day)
        except ValueError:  # 29 February in a non-leap year
            anchor = date(year, month, 28)
        for step in range(4):
            m = anchor.month - 3 * step
            y = anchor.year
            while m <= 0:
                m += 12
                y -= 1
            # Last day of that month, which is what a period end is.
            nxt = date(y + (m == 12), (m % 12) + 1, 1)
            out.append(nxt - timedelta(days=1))
    return sorted(set(out))


def next_expected(
    profile: FiscalProfile, form: str, *, after: date
) -> ExpectedFiling | None:
    """The earliest ``form`` period not yet reported, or ``None``.

    "Next" means the first period after the last one in the cache —
    which may already be overdue. That is deliberate and it is the whole
    point: an earlier version skipped any period whose expected date had
    passed and returned the one after it, so a company that was late
    read as "nothing due for three months", which is the exact opposite
    of the truth and the only case where refreshing actually matters.

    An 8-K reports an event, so there is no next one to predict; only
    the forms in :data:`SCHEDULED_FORMS` have an answer.
    """
    if form not in _DEADLINE_DAYS:
        return None
    lag = profile.median_lag_days.get(form)
    if lag is None:
        return None

    last = profile.last_period_end.get(form)
    horizon = (
        _year_ends(profile.fiscal_year_end, after)
        if form == "10-K"
        else _quarter_ends(profile.fiscal_year_end, after)
    )
    candidates = [
        d
        for d in horizon
        if d > (last or date.min) and d >= after - timedelta(days=400)
    ]
    if not candidates:
        return None

    period_end = min(candidates)
    return ExpectedFiling(
        form=form,
        period_end=period_end,
        expected_filing=period_end + timedelta(days=lag),
        deadline=period_end + timedelta(days=_DEADLINE_DAYS[form]),
    )


def due_for_refresh(profile: FiscalProfile | None, *, as_of: date) -> bool:
    """Whether ``profile``'s company is worth re-pulling on ``as_of``.

    ``None`` is always due. A company the calendar could not model must
    keep being fetched on the old schedule — the calendar exists to
    spend effort better, never to decide that something stops being
    watched.

    Otherwise: due once the expected filing date has arrived for a
    period not already in the cache, and not before. The second half is
    what stops a company being re-fetched every day for the rest of the
    quarter once its window has opened — the period turning up in
    ``last_period_end`` closes it.
    """
    if profile is None:
        return True
    for form in SCHEDULED_FORMS:
        expected = next_expected(profile, form, after=as_of)
        if expected is None:
            continue
        if as_of >= expected.expected_filing:
            return True
    return False


def refresh_order(
    profiles: Iterable[tuple[str, FiscalProfile | None]], *, as_of: date
) -> list[str]:
    """Tickers worth re-pulling on ``as_of``, most overdue first.

    A refresh that runs out of time or rate limit should have spent it
    on the companies most likely to have filed, which is what the order
    is for.
    """
    scored: list[tuple[int, str]] = []
    for ticker, profile in profiles:
        if not due_for_refresh(profile, as_of=as_of):
            continue
        if profile is None:
            scored.append((0, ticker))
            continue
        overdue = 0
        for form in SCHEDULED_FORMS:
            expected = next_expected(profile, form, after=as_of)
            if expected is not None and as_of >= expected.expected_filing:
                overdue = max(overdue, (as_of - expected.expected_filing).days)
        scored.append((-overdue, ticker))
    return [t for _, t in sorted(scored)]
