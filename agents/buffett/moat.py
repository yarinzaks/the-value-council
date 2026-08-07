"""A deterministic franchise test.

Why this exists
~~~~~~~~~~~~~~~

Buffett's central question is not "is this cheap" but "is this a
business worth owning at all". The letters return to the same measure:
a consistently high return on equity, achieved without heavy debt. That
is what a durable competitive advantage looks like in the accounts —
a company earning outsized returns year after year that competition has
not been able to arbitrage away.

Nothing in production tested for it. ``is_simple_business`` checked a
SIC code against an exclusion list and deferred the real judgement to
an LLM analyzer that the runner instantiates as ``None``, so the moat
test was a comment. The quality gates that did run — an average ROE
floor, a debt ceiling, positive earnings — screen out the obviously bad
but cannot tell a franchise from a merely adequate business.

Why persistence, and not an average
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An average hides its own distribution. A commodity business with one
spectacular year inside a decade of mediocrity can average above a 15%
bar; a franchise earns 15% *every* year, and that repetition is the
evidence of the moat. Averaging the two together is exactly the
information Buffett is trying to extract.

So this measures how many of the observed years clear the bar, and
reports the worst one. A business whose floor is high has pricing power
it did not have to fight for.

Why ROE and not ROIC
~~~~~~~~~~~~~~~~~~~~

ROIC is the better measure in principle and the letters use return on
equity in practice, because Buffett pairs it with an explicit debt
constraint rather than folding leverage into the denominator: "without
heavy leverage" does the work that the invested-capital denominator
would. The existing D/E gate supplies that constraint, and equity and
net income are the two series the cache reports most reliably — 343 of
400 sampled tickers carry NetIncomeLoss. A leverage-blind ROE would be
a bad gate; a leverage-gated ROE is the doctrine as written.

Financials are handled by exclusion, not adjustment. A bank's ROE is a
function of leverage by construction, so the level means something
different there; :mod:`core.screener.business_type` routes them out
before this is reached.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from core.data.edgar_cache import EdgarCache
from core.logger import get_logger
from core.screener.business_type import is_financial

logger = get_logger("agents.buffett.moat")

_NET_INCOME_CONCEPTS: tuple[str, ...] = ("NetIncomeLoss", "ProfitLoss")
_EQUITY_CONCEPTS: tuple[str, ...] = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)

#: The return that marks a business as earning more than its cost of
#: capital by a margin competition has not closed. Buffett's own
#: commentary uses "consistently above 15%".
DEFAULT_MIN_ROE_PCT: float = 15.0

#: How many annual observations are needed before persistence means
#: anything. Three good years is a cycle, not a franchise.
DEFAULT_MIN_YEARS: int = 5

#: Fraction of observed years that must clear the bar. 0.8 tolerates one
#: bad year in five — a recession, a write-down — without tolerating a
#: business that only sometimes earns its keep.
DEFAULT_MIN_FRACTION_ABOVE: float = 0.8

#: How far back to look. Ten years spans a full cycle, which is the
#: point: a moat is a claim about surviving one.
DEFAULT_LOOKBACK_YEARS: int = 10


@dataclass(frozen=True)
class FranchiseAssessment:
    """The evidence for or against a durable advantage."""

    ticker: str
    qualifies: bool
    years_observed: int
    years_above: int
    median_roe_pct: float | None
    worst_roe_pct: float | None
    reason: str

    @property
    def fraction_above(self) -> float:
        if self.years_observed == 0:
            return 0.0
        return self.years_above / self.years_observed


def roe_history(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    years: int = DEFAULT_LOOKBACK_YEARS,
) -> list[tuple[int, float]]:
    """Annual ROE in percent, newest first, as ``(fiscal_year, roe)``.

    Years with non-positive equity are skipped rather than recorded as
    a negative ROE: the ratio is meaningless there, and a large negative
    would distort both the median and the worst-year figure.
    """
    out: list[tuple[int, float]] = []
    seen: set[int] = set()
    for i in range(years + 4):  # slack for years the cache is missing
        lookup = as_of - timedelta(days=int(365.25 * i))
        ni = None
        for concept in _NET_INCOME_CONCEPTS:
            ni = cache.latest_value_at(
                ticker, concept, lookup, forms=("10-K",), prefer_annual=True
            )
            if ni is not None:
                break
        eq = None
        for concept in _EQUITY_CONCEPTS:
            eq = cache.latest_value_at(
                ticker, concept, lookup, forms=("10-K",), prefer_annual=True
            )
            if eq is not None:
                break
        if ni is None or eq is None or ni.fiscal_year is None:
            continue
        if ni.fiscal_year in seen or eq.value <= 0:
            continue
        seen.add(int(ni.fiscal_year))
        out.append((int(ni.fiscal_year), 100.0 * float(ni.value) / float(eq.value)))
        if len(out) >= years:
            break
    out.sort(reverse=True)
    return out


def assess_franchise(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    min_roe_pct: float = DEFAULT_MIN_ROE_PCT,
    min_years: int = DEFAULT_MIN_YEARS,
    min_fraction_above: float = DEFAULT_MIN_FRACTION_ABOVE,
    lookback_years: int = DEFAULT_LOOKBACK_YEARS,
) -> FranchiseAssessment:
    """Judge whether ``ticker``'s record shows a durable advantage."""
    if is_financial(None, ticker):
        return FranchiseAssessment(
            ticker=ticker,
            qualifies=False,
            years_observed=0,
            years_above=0,
            median_roe_pct=None,
            worst_roe_pct=None,
            reason="financial — ROE is a leverage choice, not a moat",
        )

    history = roe_history(cache, ticker, as_of, years=lookback_years)
    values = [roe for _, roe in history]
    if len(values) < min_years:
        return FranchiseAssessment(
            ticker=ticker,
            qualifies=False,
            years_observed=len(values),
            years_above=0,
            median_roe_pct=median(values) if values else None,
            worst_roe_pct=min(values) if values else None,
            reason=(
                f"only {len(values)} year(s) of usable ROE; "
                f"{min_years} needed to call anything durable"
            ),
        )

    above = sum(1 for roe in values if roe >= min_roe_pct)
    fraction = above / len(values)
    qualifies = fraction >= min_fraction_above
    return FranchiseAssessment(
        ticker=ticker,
        qualifies=qualifies,
        years_observed=len(values),
        years_above=above,
        median_roe_pct=median(values),
        worst_roe_pct=min(values),
        reason=(
            f"ROE >= {min_roe_pct:.0f}% in {above}/{len(values)} years "
            f"(worst {min(values):.1f}%)"
            if qualifies
            else (
                f"ROE >= {min_roe_pct:.0f}% in only {above}/{len(values)} years "
                f"— {min_fraction_above:.0%} required"
            )
        ),
    )


__all__ = [
    "DEFAULT_LOOKBACK_YEARS",
    "DEFAULT_MIN_FRACTION_ABOVE",
    "DEFAULT_MIN_ROE_PCT",
    "DEFAULT_MIN_YEARS",
    "FranchiseAssessment",
    "assess_franchise",
    "roe_history",
]
