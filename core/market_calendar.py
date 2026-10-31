"""When the US equity market is open, and when the books must not move.

Why this exists
~~~~~~~~~~~~~~~

The schedule ran Monday to Friday and knew nothing else, so every US
market holiday was an ordinary trading day to it. Across four of them —
Memorial Day, Juneteenth, Independence Day and Labor Day 2026 — the
agents wrote 1,770 journal rows and executed 21 trades at prices that
were the previous session's close, because that is what a quote feed
returns when there is no session. Graham sold FDP at $27.63 on 3 July,
a day the exchange never opened.

Nothing about that looks wrong from outside. The NAV is not even
distorted: the prices were real closes. What is wrong is the date on
them, and a paper-trading record whose dates cannot be trusted is not a
record of anything.

What is here
~~~~~~~~~~~~

The NYSE's own rules, which have been stable for decades: ten holidays,
the observation rule for those landing on a weekend, and the three
recurring early closes. Everything is derived rather than listed, so
the answers run as far forward as anyone needs rather than to the end
of a table somebody has to remember to extend.

What is not here
~~~~~~~~~~~~~~~~

One-off closures: a national day of mourning for a former president,
Hurricane Sandy in 2012, 11 September 2001. They are unpredictable by
construction and no calendar computed from rules can hold them. A run
on such a day would mark against a stale quote exactly as before, which
is the behaviour this module exists to stop — the difference is that it
would then be one day a decade rather than nine a year.

Half days are reported but deliberately do not stop a run. The
exchange's closing auction is simply earlier; a mark struck after 13:00
ET reads the real official close, which is the number wanted anyway.

Extended hours do not change any of this. From 6 December 2026 Nasdaq
and NYSE Arca trade 23 hours a day, five days a week, but the core
session stays 09:30–16:00 ET and the official closing price still comes
from the 16:00 auction. Holidays stay holidays.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

#: Weekday numbers, for readability at the call sites below.
MONDAY, THURSDAY, SATURDAY = 0, 3, 5

#: When the exchange's early closes end, in New York local time.
EARLY_CLOSE_HOUR = 13

#: The usual bell, unchanged by the 2026 extension to 23-hour trading:
#: the core session and its closing auction keep these hours.
REGULAR_OPEN = (9, 30)
REGULAR_CLOSE = (16, 0)


def easter_sunday(year: int) -> date:
    """Easter, by the anonymous Gregorian algorithm.

    Needed only for Good Friday, which is the one NYSE holiday with no
    fixed date and no "nth weekday of the month" rule.
    """
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = ((h + el - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` of a month — "third Monday in January"."""
    first = date(year, month, 1)
    first += timedelta(days=(weekday - first.weekday()) % 7)
    return first + timedelta(weeks=n - 1)


def last_weekday(year: int, month: int, weekday: int) -> date:
    """The last ``weekday`` of a month — "last Monday in May"."""
    last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def observed(holiday: date) -> date | None:
    """Where a holiday is actually taken, or ``None`` when it is not.

    NYSE Rule 7.2: a holiday on a Saturday closes the preceding Friday
    and one on a Sunday closes the following Monday — except where
    "unusual business conditions exist, such as the ending of a monthly
    or yearly accounting period".

    New Year's Day is the only holiday that ever meets the exception,
    and it meets it every time: the Friday before a Saturday 1 January
    is 31 December, the year-end accounting session. So the exchange
    trades that Friday and does not move the holiday to the Monday
    either — it simply is not observed. 2028 has nine closures for this
    reason, and Friday 31 December 2027 is an ordinary full session.
    """
    if holiday.weekday() == SATURDAY:
        if (holiday.month, holiday.day) == (1, 1):
            return None
        return holiday - timedelta(days=1)
    if holiday.weekday() == SATURDAY + 1:
        return holiday + timedelta(days=1)
    return holiday


@lru_cache(maxsize=64)
def closures(year: int) -> dict[date, str]:
    """Every date in ``year`` the NYSE is shut, by its observed date.

    Keyed on the observed date rather than the nominal one, because the
    observed date is the one a run has to be stopped on.
    """
    nominal = [
        (date(year, 1, 1), "New Year's Day"),
        (nth_weekday(year, 1, MONDAY, 3), "Martin Luther King Jr. Day"),
        (nth_weekday(year, 2, MONDAY, 3), "Washington's Birthday"),
        (easter_sunday(year) - timedelta(days=2), "Good Friday"),
        (last_weekday(year, 5, MONDAY), "Memorial Day"),
        (date(year, 6, 19), "Juneteenth"),
        (date(year, 7, 4), "Independence Day"),
        (nth_weekday(year, 9, MONDAY, 1), "Labor Day"),
        (nth_weekday(year, 11, THURSDAY, 4), "Thanksgiving"),
        (date(year, 12, 25), "Christmas Day"),
    ]
    out: dict[date, str] = {}
    for holiday, name in nominal:
        day = observed(holiday)
        if day is not None and day.weekday() < SATURDAY:
            out[day] = name
    return out


@lru_cache(maxsize=64)
def half_days(year: int) -> dict[date, str]:
    """Dates the NYSE closes at 13:00 ET instead of 16:00.

    A date the exchange is shut outright is never also a half day. When
    25 December falls on a Saturday the holiday is observed on the 24th,
    so that year has a Christmas Eve closure and no Christmas Eve early
    close — which is why 2027 has one half day and its neighbours two.
    """
    shut = closures(year)
    out: dict[date, str] = {}

    independence = date(year, 7, 4)
    if independence.weekday() < SATURDAY:
        eve = independence - timedelta(days=1)
        if eve.weekday() < SATURDAY:
            out[eve] = "day before Independence Day"

    out[nth_weekday(year, 11, THURSDAY, 4) + timedelta(days=1)] = (
        "day after Thanksgiving"
    )

    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() < SATURDAY:
        out[christmas_eve] = "Christmas Eve"

    return {d: n for d, n in out.items() if d.weekday() < SATURDAY and d not in shut}


def is_trading_day(day: date) -> bool:
    """Whether the US equity market holds a session on ``day``."""
    return day.weekday() < SATURDAY and day not in closures(day.year)


def why_closed(day: date) -> str | None:
    """Why the market is shut on ``day``, or ``None`` when it is open.

    A sentence rather than a flag, because the only caller worth having
    is one that writes the reason into a log somebody later reads to
    find out why a day is missing from the record.
    """
    if day.weekday() >= SATURDAY:
        return "weekend"
    return closures(day.year).get(day)


def closes_early(day: date) -> str | None:
    """Why ``day`` ends at 13:00 ET, or ``None`` when it runs to 16:00.

    Reported, not acted on. An early close moves the closing auction
    forward; a mark struck after it still reads the real official close.
    """
    return half_days(day.year).get(day)


def previous_trading_day(day: date) -> date:
    """The last session on or before ``day``."""
    probe = day
    # Four days covers a weekend with holidays on both sides; the loop
    # is bounded anyway so a rule change cannot hang a run.
    for _ in range(14):
        if is_trading_day(probe):
            return probe
        probe -= timedelta(days=1)
    raise ValueError(f"no trading day in the fortnight before {day}")


__all__ = [
    "EARLY_CLOSE_HOUR",
    "REGULAR_CLOSE",
    "REGULAR_OPEN",
    "closes_early",
    "closures",
    "easter_sunday",
    "half_days",
    "is_trading_day",
    "last_weekday",
    "nth_weekday",
    "observed",
    "previous_trading_day",
    "why_closed",
]
