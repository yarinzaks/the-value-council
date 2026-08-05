"""Dreman's financial-strength battery — telling a bargain from a wreck.

Playbook §4.2 opens with the reason this exists:

    Once a stock passes the contrarian screen, the agent applies
    fundamental tests to **distinguish overreaction-driven cheapness
    from outright deteriorating businesses**.

The cheapest quintile on P/E, P/B, P/CF and yield contains both, and
nothing downstream of the quintile screen could tell them apart. Two of
Dreman's six tests were implemented — the D/E ceiling (Test 4) and the
market-cap floor (Test 6), both already gates in :mod:`.filters` — plus
a check that the latest net income is positive, which is a fragment of
Test 5. Tests 1, 2, 3 and the actual multi-year shape of 5 were not.

Why this is Dreman's battery and not Piotroski's
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``core.scoring`` ships a Piotroski F-Score, an Altman Z and a Beneish M,
all correct and none of them wired to anything. It is tempting to bolt
the F-Score on here — Piotroski designed it for exactly this population,
high book-to-market names where the winners need separating from the
losers.

It would still be the wrong move. Dreman published his own tests, this
agent exists to be Dreman, and no playbook in the project names any of
those three scores; they appear once, in an architecture diagram. An
agent that screens on someone else's 2000 paper is a less faithful
Dreman, not a smarter one.

Grading, not gating
~~~~~~~~~~~~~~~~~~~

The playbook states each test in three bands — "strong", "acceptable",
"liquidity concern" — and marks the industry comparisons as *preferred*
rather than required. That is a graded judgement, so this returns a
score rather than a veto.

Missing data is the trap. Measured over 548 tickers with point-in-time
financials at 2026-04-01, the current ratio is computable for 58.6%,
ROE for 69.5% and margin for 43.4%. Failing a company for a figure its
filer never tagged would reject two of every five names on no evidence;
passing it would hand a clean bill of health to a company nobody can
read, which is the failure already fixed in the leverage helper, in
Fisher's dilution point and in Marks's temperature.

So a test that cannot be computed is neither passed nor failed — it is
**unjudgeable**, and a company has to be judgeable on
:data:`DEFAULT_MIN_JUDGEABLE` of the four before any verdict is
returned. Below that the answer is "cannot assess", which for a
contrarian screen means pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

logger = get_logger("agents.dreman.strength")


#: Test 1. The playbook's "acceptable" band; 1.5 is "strong" and below
#: 1.0 is named a liquidity concern outright.
DEFAULT_MIN_CURRENT_RATIO: float = 1.0

#: Test 2. "ROE above 12% (acceptable for contrarian candidates)."
DEFAULT_MIN_ROE_PCT: float = 12.0

#: Test 3. "Stable or expanding margins over 3-5 years." A little slack,
#: because a contrarian candidate is by definition having a bad spell
#: and Dreman is screening out collapse, not softness.
DEFAULT_MAX_MARGIN_EROSION_PP: float = 3.0

#: Test 5. Earnings must not be shrinking over the multi-year window.
#: The playbook asks for growth outpacing the S&P 500; without an index
#: earnings series in the cache this checks the weaker, checkable half —
#: that the trend is not down.
DEFAULT_MIN_EARNINGS_CAGR_PCT: float = 0.0

#: How many of the four must be computable before a verdict is issued.
DEFAULT_MIN_JUDGEABLE: int = 3

#: How many judgeable tests may fail. One bad reading is a rough patch,
#: which is what a contrarian is buying; two is a pattern.
DEFAULT_MAX_FAILURES: int = 1

#: Window for the trend tests, in years.
DEFAULT_TREND_YEARS: int = 5

_PRETAX_CONCEPTS: tuple[str, ...] = (
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    "OperatingIncomeLoss",
)
_REVENUE_CONCEPTS: tuple[str, ...] = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
)
_NET_INCOME_CONCEPTS: tuple[str, ...] = ("NetIncomeLoss", "ProfitLoss")


@dataclass(frozen=True)
class StrengthAssessment:
    """Per-test verdicts. ``None`` means the test could not be computed."""

    ticker: str
    current_ratio: bool | None
    return_on_equity: bool | None
    margin_trend: bool | None
    earnings_trend: bool | None
    reason: str

    @property
    def _verdicts(self) -> tuple[bool | None, ...]:
        return (
            self.current_ratio,
            self.return_on_equity,
            self.margin_trend,
            self.earnings_trend,
        )

    @property
    def judgeable(self) -> int:
        return sum(1 for v in self._verdicts if v is not None)

    @property
    def passed(self) -> int:
        return sum(1 for v in self._verdicts if v is True)

    @property
    def failed(self) -> int:
        return sum(1 for v in self._verdicts if v is False)


def current_ratio(fin: PointInTimeFinancials) -> float | None:
    """Test 1. None when either side is missing or liabilities are zero."""
    if fin.current_assets is None or not fin.current_liabilities:
        return None
    if fin.current_liabilities <= 0:
        return None
    return fin.current_assets / fin.current_liabilities


def return_on_equity_pct(fin: PointInTimeFinancials) -> float | None:
    """Test 2. None on missing figures or non-positive book."""
    if fin.net_income is None or fin.total_equity is None:
        return None
    if fin.total_equity <= 0:
        return None
    return 100.0 * fin.net_income / fin.total_equity


def _annual(
    cache: EdgarCache,
    ticker: str,
    concepts: tuple[str, ...],
    as_of: date,
) -> tuple[float, int] | None:
    """Freshest annual value across the chain, as ``(value, fiscal_year)``.

    Resolves the whole chain rather than taking the first hit — the same
    correction made in :mod:`agents.fisher.quality_score`, where the
    first-match version was serving revenue a median seven fiscal years
    stale on a quarter of the universe.
    """
    best: tuple[float, int] | None = None
    for concept in concepts:
        fact = cache.latest_value_at(
            ticker, concept, as_of, forms=("10-K",), prefer_annual=True
        )
        if fact is None or fact.fiscal_year is None:
            continue
        fy = int(fact.fiscal_year)
        if best is None or fy > best[1]:
            best = (float(fact.value), fy)
    return best


def margin_erosion_pp(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    years: int = DEFAULT_TREND_YEARS,
) -> float | None:
    """Test 3. Percentage points of pre-tax margin lost over the window.

    Positive means the margin shrank. Both endpoints are built from one
    fiscal year's income over the same year's revenue, and None is
    returned when they disagree — a quotient across two different years
    is not a margin.
    """
    now_i = _annual(cache, ticker, _PRETAX_CONCEPTS, as_of)
    now_r = _annual(cache, ticker, _REVENUE_CONCEPTS, as_of)
    then_date = as_of - timedelta(days=int(365.25 * years))
    then_i = _annual(cache, ticker, _PRETAX_CONCEPTS, then_date)
    then_r = _annual(cache, ticker, _REVENUE_CONCEPTS, then_date)
    if now_i is None or now_r is None or then_i is None or then_r is None:
        return None
    if now_i[1] != now_r[1] or then_i[1] != then_r[1]:
        return None
    if now_r[0] <= 0 or then_r[0] <= 0 or now_i[1] <= then_i[1]:
        return None
    now_m = 100.0 * now_i[0] / now_r[0]
    then_m = 100.0 * then_i[0] / then_r[0]
    return then_m - now_m


def earnings_cagr_pct(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    years: int = DEFAULT_TREND_YEARS,
) -> float | None:
    """Test 5. Net-income CAGR over the window.

    Returns -100.0 when earnings went negative — a collapse, not a rate.
    None when either endpoint is missing or the base is not positive,
    since a CAGR off a loss is meaningless.
    """
    now = _annual(cache, ticker, _NET_INCOME_CONCEPTS, as_of)
    then = _annual(cache, ticker, _NET_INCOME_CONCEPTS, as_of - timedelta(days=int(365.25 * years)))
    if now is None or then is None:
        return None
    n = now[1] - then[1]
    if n <= 0 or then[0] <= 0:
        return None
    if now[0] <= 0:
        return -100.0
    # float() because ** widens to Any for mypy — the base is positive
    # here, so the result is real.
    return 100.0 * (float((now[0] / then[0]) ** (1.0 / n)) - 1.0)


def assess_strength(
    fin: PointInTimeFinancials,
    cache: EdgarCache,
    as_of: date,
    *,
    min_current_ratio: float = DEFAULT_MIN_CURRENT_RATIO,
    min_roe_pct: float = DEFAULT_MIN_ROE_PCT,
    max_margin_erosion_pp: float = DEFAULT_MAX_MARGIN_EROSION_PP,
    min_earnings_cagr_pct: float = DEFAULT_MIN_EARNINGS_CAGR_PCT,
    trend_years: int = DEFAULT_TREND_YEARS,
) -> StrengthAssessment:
    """Run tests 1, 2, 3 and 5 over one candidate.

    Tests 4 (D/E) and 6 (market cap) are gates in :mod:`.filters` and
    are not repeated here.
    """
    cr = current_ratio(fin)
    roe = return_on_equity_pct(fin)
    erosion = margin_erosion_pp(cache, fin.ticker, as_of, years=trend_years)
    cagr = earnings_cagr_pct(cache, fin.ticker, as_of, years=trend_years)

    v_cr = None if cr is None else cr >= min_current_ratio
    v_roe = None if roe is None else roe >= min_roe_pct
    v_margin = None if erosion is None else erosion <= max_margin_erosion_pp
    v_earn = None if cagr is None else cagr >= min_earnings_cagr_pct

    parts = []
    if cr is not None:
        parts.append(f"CR {cr:.2f}")
    if roe is not None:
        parts.append(f"ROE {roe:.1f}%")
    if erosion is not None:
        parts.append(f"margin {-erosion:+.1f}pp")
    if cagr is not None:
        parts.append(f"EPS CAGR {cagr:+.1f}%")
    return StrengthAssessment(
        ticker=fin.ticker,
        current_ratio=v_cr,
        return_on_equity=v_roe,
        margin_trend=v_margin,
        earnings_trend=v_earn,
        reason="; ".join(parts) if parts else "no test computable",
    )


def is_deteriorating(
    assessment: StrengthAssessment,
    *,
    min_judgeable: int = DEFAULT_MIN_JUDGEABLE,
    max_failures: int = DEFAULT_MAX_FAILURES,
) -> bool:
    """True when the evidence says deterioration rather than overreaction.

    False when the company looks merely out of favour **and** when too
    few tests could be computed to say anything. Those are different
    situations that both mean "do not reject here", and conflating them
    would either throw out two names in five for missing tags or hand a
    clean grade to a company nobody can read.
    """
    if assessment.judgeable < min_judgeable:
        logger.debug(
            f"{assessment.ticker}: only {assessment.judgeable} of 4 tests "
            f"computable — strength not assessed"
        )
        return False
    return assessment.failed > max_failures


__all__ = [
    "DEFAULT_MAX_FAILURES",
    "DEFAULT_MAX_MARGIN_EROSION_PP",
    "DEFAULT_MIN_CURRENT_RATIO",
    "DEFAULT_MIN_EARNINGS_CAGR_PCT",
    "DEFAULT_MIN_JUDGEABLE",
    "DEFAULT_MIN_ROE_PCT",
    "DEFAULT_TREND_YEARS",
    "StrengthAssessment",
    "assess_strength",
    "current_ratio",
    "earnings_cagr_pct",
    "is_deteriorating",
    "margin_erosion_pp",
    "return_on_equity_pct",
]
