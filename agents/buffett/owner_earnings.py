"""Owner Earnings + DCF intrinsic value — Buffett's preferred valuation.

Per playbook §4.2-4.3:

  Owner Earnings = Net Income + D&A − Maintenance Capex
                                    − Necessary Working-Capital Changes

In practice we don't have a clean "maintenance vs growth" capex split
in XBRL data, and working-capital changes are noisy. The pragmatic
approximation Buffett's modern interpreters use is:

  Owner Earnings ≈ Operating Cash Flow − Capex   (= Free Cash Flow)

This is what we implement, with a 5-year average to smooth one-off
years (acquisition years, write-downs, etc.).

DCF (playbook §4.3):

  IV = Σ (OE × (1+g)^t / (1+r)^t  for t in 1..N)
       + (OE × (1+g)^N × terminal_multiple) / (1+r)^N

  r  = 10Y Treasury yield + 1pp risk premium  (default 5%)
  g  = capped trailing growth (default min(historical CAGR, 8%))
  N  = 10 years
  terminal_multiple = 13× (mid of Buffett's 12-15× range)

The discount rate's exposure to the current Treasury yield is
intentional and faithful to Buffett's writings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

logger = get_logger("agents.buffett.owner_earnings")


# ---- Defaults from playbook -----------------------------------------------
DEFAULT_DCF_YEARS: int = 10
DEFAULT_DISCOUNT_RATE_PCT: float = 5.0  # ~10Y Treasury + 1pp circa 2024
DEFAULT_TERMINAL_MULTIPLE: float = 13.0
DEFAULT_MAX_GROWTH_PCT: float = 8.0  # conservative cap per playbook
DEFAULT_OE_AVG_YEARS: int = 5

#: How many years of Owner Earnings history to use for the trailing
#: growth-rate estimate. Shorter than ``DEFAULT_OE_AVG_YEARS`` so we
#: capture the recent trajectory rather than the noisy decade-ago
#: starting point.
DEFAULT_GROWTH_LOOKBACK_YEARS: int = 5


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
class OwnerEarningsRecord:
    """One fiscal year's Owner Earnings."""

    fiscal_year: int
    period_end: date
    ocf: float
    capex: float  # always recorded as a POSITIVE number (cash outflow)
    owner_earnings: float


@dataclass(frozen=True)
class IntrinsicValueResult:
    """Output of :func:`intrinsic_value`."""

    ticker: str
    intrinsic_value_usd: float
    avg_owner_earnings_usd: float
    growth_rate_pct: float
    discount_rate_pct: float
    terminal_multiple: float
    years_of_history: int
    notes: tuple[str, ...] = ()  # warnings the caller may surface


# ---- Owner Earnings extraction --------------------------------------------
def _annual_value(
    cache: EdgarCache,
    ticker: str,
    concepts: tuple[str, ...],
    as_of: date,
) -> tuple[float, int, date] | None:
    """Most recent annual (10-K) value for any of ``concepts`` filed by
    ``as_of``. Returns ``(value, fiscal_year, period_end)`` or ``None``.
    """
    for concept in concepts:
        fact = cache.latest_value_at(
            ticker, concept, as_of, forms=("10-K",), prefer_annual=True
        )
        if fact is not None:
            return float(fact.value), int(fact.fiscal_year), fact.period_end
    return None


def historical_owner_earnings(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    years: int = DEFAULT_OE_AVG_YEARS,
) -> list[OwnerEarningsRecord]:
    """Pull the last ``years`` annual Owner Earnings records.

    Returns at most ``years`` records, oldest first. Years where
    either OCF or capex is missing are skipped — but if the candidate
    has fewer than ``years`` complete records, the caller should
    treat the result as insufficient history.
    """
    records: list[OwnerEarningsRecord] = []
    seen_years: set[int] = set()

    for i in range(years + 5):  # extra slack for missing years
        lookup = as_of - timedelta(days=int(365.25 * i))
        ocf = _annual_value(cache, ticker, _OCF_CONCEPTS, lookup)
        capex = _annual_value(cache, ticker, _CAPEX_CONCEPTS, lookup)
        if ocf is None or capex is None:
            continue
        ocf_value, ocf_year, ocf_end = ocf
        capex_value, capex_year, _ = capex
        if ocf_year != capex_year:
            # Mismatched fiscal years — try next iteration.
            continue
        if ocf_year in seen_years:
            continue
        seen_years.add(ocf_year)
        # Capex is reported as a positive cash outflow in most filings,
        # but some companies report it as negative. Take abs().
        capex_pos = abs(capex_value)
        oe = ocf_value - capex_pos
        records.append(
            OwnerEarningsRecord(
                fiscal_year=ocf_year,
                period_end=ocf_end,
                ocf=ocf_value,
                capex=capex_pos,
                owner_earnings=oe,
            )
        )
        if len(records) >= years:
            break

    records.sort(key=lambda r: r.fiscal_year)  # oldest first
    return records


def average_owner_earnings(
    records: list[OwnerEarningsRecord],
) -> float | None:
    """Simple mean of OE across records. None on empty input."""
    if not records:
        return None
    return sum(r.owner_earnings for r in records) / len(records)


def trailing_growth_pct(
    records: list[OwnerEarningsRecord],
    *,
    lookback: int = DEFAULT_GROWTH_LOOKBACK_YEARS,
) -> float | None:
    """CAGR (in %) of Owner Earnings over the most recent ``lookback``
    years.

    Returns None when:
      - fewer than 2 records in window
      - base period OE ≤ 0 (CAGR undefined)
      - end period OE ≤ 0 (collapse to negative — caller decides)
    """
    if len(records) < 2:
        return None
    window = records[-lookback:] if len(records) > lookback else records
    if len(window) < 2:
        return None
    base = window[0].owner_earnings
    end = window[-1].owner_earnings
    n_years = window[-1].fiscal_year - window[0].fiscal_year
    if n_years <= 0 or base <= 0:
        return None
    if end <= 0:
        return -100.0
    cagr = (end / base) ** (1.0 / n_years) - 1.0
    return cagr * 100.0


# ---- DCF intrinsic value --------------------------------------------------
def _dcf_present_value(
    *,
    base_oe: float,
    growth_pct: float,
    discount_pct: float,
    terminal_multiple: float,
    years: int,
) -> float:
    """Pure-math DCF on Owner Earnings.

    Forecasts ``base_oe`` growing at ``growth_pct`` for ``years`` years,
    discounts each year at ``discount_pct``, and adds a terminal value
    of (Year-N OE × terminal_multiple) discounted to present.
    """
    g = growth_pct / 100.0
    r = discount_pct / 100.0
    if r <= 0:
        # Degenerate — pretend infinite present value would be wrong;
        # cap at terminal value as a defensive fallback.
        return base_oe * terminal_multiple

    pv = 0.0
    oe_t = base_oe
    for t in range(1, years + 1):
        oe_t = oe_t * (1.0 + g)
        pv += oe_t / ((1.0 + r) ** t)
    terminal_oe = base_oe * (1.0 + g) ** years
    terminal_value = terminal_oe * terminal_multiple
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
    history_years: int = DEFAULT_OE_AVG_YEARS,
    growth_lookback_years: int = DEFAULT_GROWTH_LOOKBACK_YEARS,
) -> IntrinsicValueResult | None:
    """Compute Buffett-style DCF intrinsic value (TOTAL company value
    in USD — divide by shares to compare to price).

    Returns ``None`` when:
      - fewer than 3 years of OE history
      - average OE ≤ 0
    The caller should treat None as "cannot value — reject".
    """
    records = historical_owner_earnings(
        cache, ticker, as_of, years=history_years
    )
    if len(records) < 3:
        logger.debug(
            f"{ticker}@{as_of}: only {len(records)} years of OE history; skip"
        )
        return None

    avg_oe = average_owner_earnings(records)
    if avg_oe is None or avg_oe <= 0:
        logger.debug(f"{ticker}@{as_of}: avg OE non-positive; skip")
        return None

    # Growth rate: trailing CAGR, capped, floored at 0 (no negative
    # growth assumption — Buffett picks businesses he expects to grow).
    raw_g = trailing_growth_pct(records, lookback=growth_lookback_years)
    if raw_g is None:
        # Fall back to a flat (no-growth) projection — conservative.
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
        base_oe=avg_oe,
        growth_pct=applied_g,
        discount_pct=discount_rate_pct,
        terminal_multiple=terminal_multiple,
        years=years,
    )

    return IntrinsicValueResult(
        ticker=ticker,
        intrinsic_value_usd=iv,
        avg_owner_earnings_usd=avg_oe,
        growth_rate_pct=applied_g,
        discount_rate_pct=discount_rate_pct,
        terminal_multiple=terminal_multiple,
        years_of_history=len(records),
        notes=notes,
    )


def margin_of_safety_pct(
    intrinsic_value_usd: float, market_cap_usd: float
) -> float | None:
    """(IV − Mcap) / IV in PERCENT. None if IV ≤ 0."""
    if intrinsic_value_usd <= 0:
        return None
    return 100.0 * (intrinsic_value_usd - market_cap_usd) / intrinsic_value_usd


__all__ = [
    "DEFAULT_DCF_YEARS",
    "DEFAULT_DISCOUNT_RATE_PCT",
    "DEFAULT_GROWTH_LOOKBACK_YEARS",
    "DEFAULT_MAX_GROWTH_PCT",
    "DEFAULT_OE_AVG_YEARS",
    "DEFAULT_TERMINAL_MULTIPLE",
    "IntrinsicValueResult",
    "OwnerEarningsRecord",
    "_dcf_present_value",
    "average_owner_earnings",
    "historical_owner_earnings",
    "intrinsic_value",
    "margin_of_safety_pct",
    "trailing_growth_pct",
]
