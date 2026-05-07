"""Conservative DCF + Margin of Safety — Klarman's quant valuation core.

Per playbook §4.1-§4.2, Klarman explicitly REJECTS single-metric
valuation. The right yardstick depends on the asset:

  * Cash flow generators → DCF on free cash flow
  * Asset-rich companies → NAV
  * Breakup candidates → sum-of-parts
  * Distressed debt → recovery analysis
  * Bankrupt claims → capital-structure waterfall
  * Uncertain outcomes → scenario-weighted probability

This backtest module implements only the DCF-on-FCF path, which is
the right yardstick for the public-equity universe we trade in the
quant pipeline. NAV / sum-of-parts / recovery analyses live in the
LLM "downside" memo for live mode (playbook §4.1 routing).

Conservative DCF defaults (vs Buffett's owner-earnings DCF):

  * Free cash flow ≈ OCF − capex; 5-year average to smooth.
  * Growth: trailing CAGR capped at **5%** (vs Buffett's 8%) — Klarman
    uses pessimistic, not optimistic, growth assumptions.
  * Discount rate: **8%** (vs Buffett's 5%) — higher hurdle.
  * Terminal multiple: **10×** (vs Buffett's 13×) — punitive on
    long-tail assumptions.
  * Projection horizon: 10 years.

The conservatism is deliberate. Klarman demands a 30-40% margin of
safety AT a conservative intrinsic value — not at a generous one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

logger = get_logger("agents.klarman.valuation")


DEFAULT_DCF_YEARS: int = 10
DEFAULT_DISCOUNT_RATE_PCT: float = 8.0
DEFAULT_TERMINAL_MULTIPLE: float = 10.0
DEFAULT_MAX_GROWTH_PCT: float = 5.0
DEFAULT_FCF_AVG_YEARS: int = 5
DEFAULT_GROWTH_LOOKBACK_YEARS: int = 5

#: Minimum margin of safety to qualify, per playbook §4.2:
#:   * 30% for large/mid-cap public equities (well-understood)
#:   * 40% for small-cap / complex / distressed
#: The backtest uses 30% as the universal floor (we don't know which
#: bucket a candidate falls into without LLM judgment); the LLM in
#: live mode tightens to 40% for complex situations.
DEFAULT_MIN_MARGIN_OF_SAFETY_PCT: float = 30.0


# ---- XBRL concept aliases -------------------------------------------------
_OCF_CONCEPTS: tuple[str, ...] = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
_CAPEX_CONCEPTS: tuple[str, ...] = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
)


@dataclass(frozen=True)
class FreeCashFlowRecord:
    """One fiscal year's FCF."""

    fiscal_year: int
    period_end: date
    ocf: float
    capex: float  # always positive (cash outflow)
    fcf: float


@dataclass(frozen=True)
class IntrinsicValueResult:
    ticker: str
    intrinsic_value_usd: float
    avg_fcf_usd: float
    growth_rate_pct: float
    discount_rate_pct: float
    terminal_multiple: float
    years_of_history: int
    notes: tuple[str, ...] = ()


# ---- FCF extraction -------------------------------------------------------
def _annual_value(
    cache: EdgarCache,
    ticker: str,
    concepts: tuple[str, ...],
    as_of: date,
) -> tuple[float, int, date] | None:
    """Most recent annual (10-K) value among ``concepts`` filed by
    ``as_of``. Returns ``(value, fiscal_year, period_end)`` or None.

    Real-world XBRL data is messy — some filings have null
    ``fiscal_year`` or ``period_end``. We treat those as missing and
    keep searching the alias list rather than crashing.
    """
    for concept in concepts:
        fact = cache.latest_value_at(
            ticker, concept, as_of, forms=("10-K",), prefer_annual=True
        )
        if fact is None:
            continue
        if fact.fiscal_year is None or fact.period_end is None:
            continue
        return float(fact.value), int(fact.fiscal_year), fact.period_end
    return None


def historical_fcf(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    years: int = DEFAULT_FCF_AVG_YEARS,
) -> list[FreeCashFlowRecord]:
    """Pull the last ``years`` annual FCF records, oldest first."""
    records: list[FreeCashFlowRecord] = []
    seen_years: set[int] = set()

    for i in range(years + 5):
        lookup = as_of - timedelta(days=int(365.25 * i))
        ocf = _annual_value(cache, ticker, _OCF_CONCEPTS, lookup)
        capex = _annual_value(cache, ticker, _CAPEX_CONCEPTS, lookup)
        if ocf is None or capex is None:
            continue
        ocf_value, ocf_year, ocf_end = ocf
        capex_value, capex_year, _ = capex
        if ocf_year != capex_year or ocf_year in seen_years:
            continue
        seen_years.add(ocf_year)
        capex_pos = abs(capex_value)
        records.append(
            FreeCashFlowRecord(
                fiscal_year=ocf_year,
                period_end=ocf_end,
                ocf=ocf_value,
                capex=capex_pos,
                fcf=ocf_value - capex_pos,
            )
        )
        if len(records) >= years:
            break

    records.sort(key=lambda r: r.fiscal_year)
    return records


def average_fcf(records: list[FreeCashFlowRecord]) -> float | None:
    """Simple mean of FCF across records. None on empty input."""
    if not records:
        return None
    return sum(r.fcf for r in records) / len(records)


def trailing_growth_pct(
    records: list[FreeCashFlowRecord],
    *,
    lookback: int = DEFAULT_GROWTH_LOOKBACK_YEARS,
) -> float | None:
    """CAGR (in %) of FCF over the most recent ``lookback`` years.

    Returns None when:
      * fewer than 2 records in window
      * base period FCF ≤ 0
      * end period FCF ≤ 0 (returns -100% to flag collapse)
    """
    if len(records) < 2:
        return None
    window = records[-lookback:] if len(records) > lookback else records
    if len(window) < 2:
        return None
    base = window[0].fcf
    end = window[-1].fcf
    n_years = window[-1].fiscal_year - window[0].fiscal_year
    if n_years <= 0 or base <= 0:
        return None
    if end <= 0:
        return -100.0
    cagr = (end / base) ** (1.0 / n_years) - 1.0
    return cagr * 100.0


# ---- DCF math -------------------------------------------------------------
def _dcf_present_value(
    *,
    base_fcf: float,
    growth_pct: float,
    discount_pct: float,
    terminal_multiple: float,
    years: int,
) -> float:
    """Pure-math DCF on FCF.

    Forecasts ``base_fcf`` growing at ``growth_pct`` for ``years``,
    discounts at ``discount_pct``, adds a terminal value of (Year-N
    FCF × terminal_multiple) discounted to present.
    """
    g = growth_pct / 100.0
    r = discount_pct / 100.0
    if r <= 0:
        # Degenerate — fall back to terminal value as a cap.
        return base_fcf * terminal_multiple

    pv = 0.0
    fcf_t = base_fcf
    for t in range(1, years + 1):
        fcf_t = fcf_t * (1.0 + g)
        pv += fcf_t / ((1.0 + r) ** t)
    terminal_fcf = base_fcf * (1.0 + g) ** years
    terminal_value = terminal_fcf * terminal_multiple
    pv += terminal_value / ((1.0 + r) ** years)
    return pv


def intrinsic_value(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    discount_rate_pct: float = DEFAULT_DISCOUNT_RATE_PCT,
    terminal_multiple: float = DEFAULT_TERMINAL_MULTIPLE,
    years: int = DEFAULT_DCF_YEARS,
    max_growth_pct: float = DEFAULT_MAX_GROWTH_PCT,
    history_years: int = DEFAULT_FCF_AVG_YEARS,
    growth_lookback_years: int = DEFAULT_GROWTH_LOOKBACK_YEARS,
) -> IntrinsicValueResult | None:
    """Conservative Klarman-style DCF intrinsic value (TOTAL company
    value in USD — divide by shares to compare to price).

    Returns ``None`` when:
      * fewer than 3 years of FCF history
      * average FCF ≤ 0
    The caller treats None as "cannot value — reject".
    """
    records = historical_fcf(cache, ticker, as_of, years=history_years)
    if len(records) < 3:
        logger.debug(
            f"{ticker}@{as_of}: only {len(records)} years of FCF; skip"
        )
        return None

    avg = average_fcf(records)
    if avg is None or avg <= 0:
        logger.debug(f"{ticker}@{as_of}: avg FCF non-positive; skip")
        return None

    raw_g = trailing_growth_pct(records, lookback=growth_lookback_years)
    if raw_g is None:
        applied_g = 0.0
        notes = ("growth rate undefined; assumed 0%",)
    else:
        applied_g = max(0.0, min(raw_g, max_growth_pct))
        notes = ()
        if raw_g > max_growth_pct:
            notes = (
                f"trailing CAGR {raw_g:.1f}% capped at {max_growth_pct}%",
            )

    iv = _dcf_present_value(
        base_fcf=avg,
        growth_pct=applied_g,
        discount_pct=discount_rate_pct,
        terminal_multiple=terminal_multiple,
        years=years,
    )

    return IntrinsicValueResult(
        ticker=ticker,
        intrinsic_value_usd=iv,
        avg_fcf_usd=avg,
        growth_rate_pct=applied_g,
        discount_rate_pct=discount_rate_pct,
        terminal_multiple=terminal_multiple,
        years_of_history=len(records),
        notes=notes,
    )


def margin_of_safety_pct(
    intrinsic_value_usd: float, market_cap_usd: float
) -> float | None:
    """(IV − Mcap) / IV in PERCENT. None when IV ≤ 0."""
    if intrinsic_value_usd <= 0:
        return None
    return 100.0 * (intrinsic_value_usd - market_cap_usd) / intrinsic_value_usd


__all__ = [
    "DEFAULT_DCF_YEARS",
    "DEFAULT_DISCOUNT_RATE_PCT",
    "DEFAULT_FCF_AVG_YEARS",
    "DEFAULT_GROWTH_LOOKBACK_YEARS",
    "DEFAULT_MAX_GROWTH_PCT",
    "DEFAULT_MIN_MARGIN_OF_SAFETY_PCT",
    "DEFAULT_TERMINAL_MULTIPLE",
    "FreeCashFlowRecord",
    "IntrinsicValueResult",
    "_dcf_present_value",
    "average_fcf",
    "historical_fcf",
    "intrinsic_value",
    "margin_of_safety_pct",
    "trailing_growth_pct",
]
