"""Fisher 15-point quantitative subset — the quant-checkable points.

Per playbook §4.1 there are 15 questions, but only ~5 of them are
quantifiable from XBRL data alone. The remaining 10 are qualitative
("management depth", "labor relations", "communication candor",
"integrity") and are scored by the LLM scuttlebutt analyzer in live
mode.

The five quant-checkable points implemented here:

  Point 1  — Market potential (proxied by revenue 5-yr CAGR ≥ 8%)
  Point 3  — R&D effectiveness (R&D / Revenue ≥ floor)
  Point 5  — Profit margins (operating margin ≥ floor)
  Point 6  — Margin maintenance (5-yr operating-margin trend)
  Point 13 — Equity dilution risk (share count flat or declining)

Point 15 (integrity) is the non-negotiable; the backtest cannot
verify integrity, so it relies on the live LLM in live mode and a
"no recent material restatement" heuristic in backtest (proxied by
"earnings consistency" in :mod:`filters`).

A candidate's :class:`QualityScore.points_passed` is the count of
these 5 points that PASS. Tiering (in :mod:`ranking`):

  * 5/5 → Tier A
  * 4/5 → Tier B
  * ≤3/5 → reject
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

logger = get_logger("agents.fisher.quality_score")


# ---- Quantitative anchors per playbook §4.1 ------------------------------
DEFAULT_MIN_REVENUE_CAGR_PCT: float = 8.0
DEFAULT_MIN_RD_TO_REVENUE_PCT: float = 5.0  # absolute floor; not all
                                            # industries have R&D, so this
                                            # is permissive
DEFAULT_MIN_OPERATING_MARGIN_PCT: float = 12.0
DEFAULT_MARGIN_TREND_FLOOR_BPS: float = -100.0  # tolerate -100bps over 5yr
DEFAULT_MAX_SHARE_DILUTION_PCT_5YR: float = 5.0  # > 5% dilution = fail


# ---- XBRL concepts -------------------------------------------------------
_REVENUE_CONCEPTS: tuple[str, ...] = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
_OPERATING_INCOME_CONCEPTS: tuple[str, ...] = (
    "OperatingIncomeLoss",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
)
_RD_CONCEPTS: tuple[str, ...] = (
    "ResearchAndDevelopmentExpense",
    "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
)
_SHARES_CONCEPTS: tuple[str, ...] = (
    "CommonStockSharesOutstanding",
    "EntityCommonStockSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
)


@dataclass(frozen=True)
class QualityScore:
    """The five quant-checkable Fisher points + total."""

    ticker: str
    point_1_market_potential: bool
    point_3_rd_effectiveness: bool
    point_5_profit_margins: bool
    point_6_margin_maintenance: bool
    point_13_equity_dilution: bool
    points_passed: int  # 0..5
    # Raw values for transparency
    revenue_cagr_5yr_pct: float | None
    rd_to_revenue_pct: float | None
    operating_margin_pct: float | None
    margin_trend_5yr_bps: float | None
    share_count_change_5yr_pct: float | None


# ---- Concept-fetch helpers -----------------------------------------------
def _annual_value(
    cache: EdgarCache,
    ticker: str,
    concepts: tuple[str, ...],
    as_of: date,
    *,
    namespace: str = "us-gaap",
) -> tuple[float, int] | None:
    """Freshest annual (10-K) value among ``concepts`` filed by
    ``as_of``. Returns ``(value, fiscal_year)`` or None. Tolerates
    null fiscal_year on individual facts.

    Resolves the *whole* chain and keeps the newest fiscal year. It used
    to return the first concept that produced anything, which quietly
    froze a company at the year it changed its tagging. The revenue
    chain starts with ``Revenues``; filers that moved to the ASC 606
    concept in 2018 kept a stale ``Revenues`` fact on file, so the first
    match was that fact — forever.

    Measured over a 1,101-ticker sample of the live universe: 224 of 838
    resolvable revenue reads (26.7%) were stale, median lag 7 fiscal
    years. NextEra Energy read FY2012 revenue in 2026; Kennedy Wilson
    read FY2011, against a current figure 8x larger. Operating income
    was 3.8% stale — Hartford read FY2009, where the fresh figure has
    the opposite sign — and the share chain 6.9%.

    Ties go to the earlier concept in the chain, which is the canonical
    one for that quantity.
    """
    best: tuple[float, int] | None = None
    for concept in concepts:
        fact = cache.latest_value_at(
            ticker,
            concept,
            as_of,
            namespace=namespace,
            forms=("10-K",),
            prefer_annual=True,
        )
        if fact is None or fact.fiscal_year is None:
            continue
        fy = int(fact.fiscal_year)
        if best is None or fy > best[1]:
            best = (float(fact.value), fy)
    return best


#: How far apart two facts may sit and still be divided into a ratio.
#: Zero: a margin is one year's income over the same year's revenue.
#: The chains resolve independently, so without this a company can pair
#: FY2025 operating income with FY2012 revenue and report the quotient
#: as an operating margin.
_MAX_RATIO_YEAR_GAP: int = 0

#: Tolerance on the span of a "5-year" comparison. The lookback is done
#: by date, but which fiscal year comes back depends on what the filer
#: tagged, so exact 5s are not guaranteed. 3-7 years is still a
#: multi-year trend; 14 is a different question wearing the same
#: threshold.
_MIN_TREND_YEARS: int = 3
_MAX_TREND_YEARS: int = 7


def _aligned_ratio(
    numerator: tuple[float, int] | None,
    denominator: tuple[float, int] | None,
) -> float | None:
    """``100 * num / den`` when both come from the same fiscal year.

    Returns None when either is missing, when the years disagree, or
    when the denominator is not positive. Fisher scores a company on
    affirmative evidence, and two facts from different years are not
    evidence about either one.
    """
    if numerator is None or denominator is None:
        return None
    if abs(numerator[1] - denominator[1]) > _MAX_RATIO_YEAR_GAP:
        return None
    if denominator[0] <= 0:
        return None
    return 100.0 * numerator[0] / denominator[0]


def revenue_cagr_5yr_pct(
    cache: EdgarCache, ticker: str, as_of: date
) -> float | None:
    """Trailing 5-year revenue CAGR in PERCENT. None if undefined."""
    now = _annual_value(cache, ticker, _REVENUE_CONCEPTS, as_of)
    then_date = as_of - timedelta(days=int(365.25 * 5))
    then = _annual_value(cache, ticker, _REVENUE_CONCEPTS, then_date)
    if now is None or then is None:
        return None
    now_v, now_fy = now
    then_v, then_fy = then
    n = now_fy - then_fy
    if not _MIN_TREND_YEARS <= n <= _MAX_TREND_YEARS or then_v <= 0:
        # ``n`` is the span the chains actually produced, not the five
        # years the lookback asked for. A 14-year span still yields a
        # valid CAGR, but not one the 8% threshold was set against.
        return None
    if now_v <= 0:
        return -100.0
    cagr = (now_v / then_v) ** (1.0 / n) - 1.0
    return cagr * 100.0


def rd_to_revenue_pct(
    cache: EdgarCache, ticker: str, as_of: date
) -> float | None:
    """Most recent annual R&D / Revenue, in PERCENT. None when either
    side is missing — companies that don't report R&D fail the point
    by default, which is faithful to Fisher (R&D-effectiveness is
    point 3 — no R&D = no point 3).

    Also None when the two sides come from different fiscal years. The
    two chains resolve independently and can land years apart; dividing
    across that gap produces a number, not an R&D intensity.
    """
    return _aligned_ratio(
        _annual_value(cache, ticker, _RD_CONCEPTS, as_of),
        _annual_value(cache, ticker, _REVENUE_CONCEPTS, as_of),
    )


def operating_margin_pct(
    fin: PointInTimeFinancials,
) -> float | None:
    """Snapshot operating margin from PIT data.

    Uses ``operating_income`` and ``revenue`` as recorded in PIT.
    Returns None when either is missing or revenue ≤ 0.
    """
    if fin.operating_income is None or fin.revenue is None or fin.revenue <= 0:
        return None
    return 100.0 * fin.operating_income / fin.revenue


def margin_trend_5yr_bps(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
) -> float | None:
    """Operating margin change over 5 years, in basis points (current −
    5y prior). Positive = expanding margin. None when undefined.

    Each margin is built from one fiscal year's income over the same
    year's revenue, and the two endpoints must actually be a multi-year
    span apart. Both margins used to be quotients of whatever the two
    chains happened to return, then differenced — so a company could
    report a margin "trend" between two figures that were never a
    margin and never five years apart.
    """
    now_oi = _annual_value(cache, ticker, _OPERATING_INCOME_CONCEPTS, as_of)
    now_rev = _annual_value(cache, ticker, _REVENUE_CONCEPTS, as_of)
    then_date = as_of - timedelta(days=int(365.25 * 5))
    then_oi = _annual_value(
        cache, ticker, _OPERATING_INCOME_CONCEPTS, then_date
    )
    then_rev = _annual_value(cache, ticker, _REVENUE_CONCEPTS, then_date)

    if now_oi is None or then_oi is None:
        return None
    now_m = _aligned_ratio(now_oi, now_rev)
    then_m = _aligned_ratio(then_oi, then_rev)
    if now_m is None or then_m is None:
        return None
    span = now_oi[1] - then_oi[1]
    if not _MIN_TREND_YEARS <= span <= _MAX_TREND_YEARS:
        logger.debug(
            f"{ticker}@{as_of}: margin endpoints FY{then_oi[1]}-FY{now_oi[1]} "
            f"span {span}y, outside {_MIN_TREND_YEARS}-{_MAX_TREND_YEARS}"
        )
        return None
    return 100.0 * (now_m - then_m)  # convert pp → bps


def share_count_change_5yr_pct(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
) -> float | None:
    """5-year share count change in PERCENT. Negative = buybacks,
    positive = dilution. None when undefined.

    Tries us-gaap and dei namespaces because shares-outstanding lives
    in different concepts depending on the filer.

    Returns None unless the two endpoints are genuinely years apart.
    This is the point where a stale chain did real damage rather than
    merely reporting a wrong number: when both lookups resolved to the
    same frozen fiscal year the change came out 0.0%, which *passes*
    the dilution test. A company about which nothing was known scored
    as one with an unblemished buyback record — the same
    absence-reads-as-a-good-grade failure as an untagged debt figure
    scoring as zero leverage.
    """
    namespaces = ("us-gaap", "dei")
    now: tuple[float, int] | None = None
    for ns in namespaces:
        now = _annual_value(
            cache, ticker, _SHARES_CONCEPTS, as_of, namespace=ns
        )
        if now is not None:
            break
    if now is None:
        return None
    then_date = as_of - timedelta(days=int(365.25 * 5))
    then: tuple[float, int] | None = None
    for ns in namespaces:
        then = _annual_value(
            cache, ticker, _SHARES_CONCEPTS, then_date, namespace=ns
        )
        if then is not None:
            break
    if then is None:
        return None
    if then[0] <= 0:
        return None
    span = now[1] - then[1]
    if not _MIN_TREND_YEARS <= span <= _MAX_TREND_YEARS:
        logger.debug(
            f"{ticker}@{as_of}: share endpoints FY{then[1]}-FY{now[1]} span "
            f"{span}y, outside {_MIN_TREND_YEARS}-{_MAX_TREND_YEARS}"
        )
        return None
    return 100.0 * (now[0] - then[0]) / then[0]


# ---- Composite scoring ---------------------------------------------------
def score_quality(
    fin: PointInTimeFinancials,
    *,
    cache: EdgarCache,
    as_of: date,
    min_revenue_cagr_pct: float = DEFAULT_MIN_REVENUE_CAGR_PCT,
    min_rd_to_revenue_pct: float = DEFAULT_MIN_RD_TO_REVENUE_PCT,
    min_operating_margin_pct: float = DEFAULT_MIN_OPERATING_MARGIN_PCT,
    margin_trend_floor_bps: float = DEFAULT_MARGIN_TREND_FLOOR_BPS,
    max_share_dilution_pct_5yr: float = DEFAULT_MAX_SHARE_DILUTION_PCT_5YR,
) -> QualityScore:
    """Score the 5 quant-checkable Fisher points.

    Each point is binary (PASS/FAIL). Missing data is treated as FAIL
    — Fisher's framework demands AFFIRMATIVE evidence; absence of
    proof is not proof of quality.
    """
    rev_cagr = revenue_cagr_5yr_pct(cache, fin.ticker, as_of)
    rd_rev = rd_to_revenue_pct(cache, fin.ticker, as_of)
    op_m = operating_margin_pct(fin)
    margin_trend = margin_trend_5yr_bps(cache, fin.ticker, as_of)
    dilution = share_count_change_5yr_pct(cache, fin.ticker, as_of)

    p1 = rev_cagr is not None and rev_cagr >= min_revenue_cagr_pct
    p3 = rd_rev is not None and rd_rev >= min_rd_to_revenue_pct
    p5 = op_m is not None and op_m >= min_operating_margin_pct
    p6 = margin_trend is not None and margin_trend >= margin_trend_floor_bps
    p13 = dilution is not None and dilution <= max_share_dilution_pct_5yr

    return QualityScore(
        ticker=fin.ticker,
        point_1_market_potential=p1,
        point_3_rd_effectiveness=p3,
        point_5_profit_margins=p5,
        point_6_margin_maintenance=p6,
        point_13_equity_dilution=p13,
        points_passed=int(p1) + int(p3) + int(p5) + int(p6) + int(p13),
        revenue_cagr_5yr_pct=rev_cagr,
        rd_to_revenue_pct=rd_rev,
        operating_margin_pct=op_m,
        margin_trend_5yr_bps=margin_trend,
        share_count_change_5yr_pct=dilution,
    )


__all__ = [
    "DEFAULT_MARGIN_TREND_FLOOR_BPS",
    "DEFAULT_MAX_SHARE_DILUTION_PCT_5YR",
    "DEFAULT_MIN_OPERATING_MARGIN_PCT",
    "DEFAULT_MIN_RD_TO_REVENUE_PCT",
    "DEFAULT_MIN_REVENUE_CAGR_PCT",
    "QualityScore",
    "margin_trend_5yr_bps",
    "operating_margin_pct",
    "rd_to_revenue_pct",
    "revenue_cagr_5yr_pct",
    "score_quality",
    "share_count_change_5yr_pct",
]
