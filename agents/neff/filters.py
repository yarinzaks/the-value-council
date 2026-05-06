"""John Neff "Total Return / PE" filters.

Per the playbook (Section 4.1) — 7-criteria screen, all of which
must pass:

  1. P/E in the 40–60% of market range (deep discount but not deepest)
  2. EPS growth 7–20% (sustainable; not deep value, not speculative)
  3. Dividend yield ≥ market avg + 2pp (income on top of cheapness)
  4. **Total-Return / PE ratio ≥ 2× market**  (the SIGNATURE metric)
  5. Sales growth ≥ 50% of EPS growth (no buyback-only juicing)
  6. Quarterly earnings persistence (deferred — see ``persistence_flag``)
  7. ROE ≥ industry avg (proxied here: ROE ≥ 15%)

Plus the Council non-negotiables: positive trailing earnings,
manageable debt (D/E ≤ 1.0), market-cap floor.

Note on "growth" inputs: Neff specifically used FORWARD analyst
estimates (consensus). We don't have those — instead we use the
4-year trailing CAGR from the EDGAR cache. Honest backtest practice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

logger = get_logger("agents.neff.filters")


# ---- Defaults --------------------------------------------------------------
DEFAULT_MIN_GROWTH_PCT: float = 7.0
DEFAULT_MAX_GROWTH_PCT: float = 20.0
#: Absolute ROE sanity floor. Per the playbook the real bar is "above
#: industry average" (criterion 7) and 15% is "strictly preferred" but
#: not required. The industry-relative comparison happens in
#: ``ranking.py``; this floor is a low backstop that catches industries
#: where every constituent has near-zero ROE (e.g. pre-revenue biotech).
DEFAULT_MIN_ROE_PCT: float = 5.0
#: Documentation only — Neff's "strictly preferred" bar.
PREFERRED_ROE_PCT: float = 15.0
DEFAULT_TR_PE_MARKET_MULTIPLE: float = 2.0
DEFAULT_PE_MAX_FRAC_OF_MARKET: float = 0.60
DEFAULT_PE_MIN_FRAC_OF_MARKET: float = 0.40
DEFAULT_YIELD_PCT_OVER_MARKET: float = 2.0
DEFAULT_SALES_GROWTH_FLOOR_FRAC: float = 0.50
DEFAULT_MAX_DE: float = 1.0
DEFAULT_MIN_MARKET_CAP_USD: float = 500_000_000.0


# Concepts on EDGAR with stable naming. Revenue has multiple aliases
# across the SEC taxonomy — we try in priority order.
_REVENUE_CONCEPTS: tuple[str, ...] = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)


@dataclass(frozen=True)
class FilterResult:
    ticker: str
    passed: bool
    rejection_reason: str | None = None


# ---- Per-stock metric helpers ---------------------------------------------
def pe_ratio(price: float | None, fin: PointInTimeFinancials | None) -> float | None:
    """Trailing P/E using diluted EPS when available, else basic."""
    if fin is None or price is None or price <= 0:
        return None
    eps = fin.eps_diluted if fin.eps_diluted is not None else fin.eps_basic
    if eps is None or eps <= 0:
        return None
    return price / eps


def dividend_yield(
    market_cap: float | None, fin: PointInTimeFinancials | None
) -> float | None:
    """Dividend yield = abs(dividends paid) / market cap.

    SEC reports ``PaymentsOfDividends`` as a positive cash outflow in
    some filings and as a negative number in others — we take ``abs``
    to be robust.
    """
    if fin is None or market_cap is None or market_cap <= 0:
        return None
    div = fin.dividends_paid
    if div is None:
        return 0.0
    return abs(div) / market_cap


def roe(fin: PointInTimeFinancials | None) -> float | None:
    """Return on equity = net income / total equity."""
    if fin is None or fin.total_equity is None or fin.total_equity <= 0:
        return None
    if fin.net_income is None:
        return None
    return fin.net_income / fin.total_equity


def debt_to_equity(fin: PointInTimeFinancials | None) -> float | None:
    if fin is None or fin.total_equity is None or fin.total_equity <= 0:
        return None
    debt = fin.total_debt if fin.total_debt is not None else fin.long_term_debt
    if debt is None:
        return 0.0
    return debt / fin.total_equity


# ---- Historical-from-cache helpers ----------------------------------------
def _latest_revenue(cache: EdgarCache, ticker: str, as_of: date) -> float | None:
    """Most recently reported annual revenue at or before ``as_of``."""
    for concept in _REVENUE_CONCEPTS:
        fact = cache.latest_value_at(
            ticker, concept, as_of, forms=("10-K",), prefer_annual=True
        )
        if fact is not None:
            return float(fact.value)
    return None


def _latest_eps(cache: EdgarCache, ticker: str, as_of: date) -> float | None:
    """Most recently reported diluted EPS at or before ``as_of``."""
    fact = cache.latest_value_at(
        ticker,
        "EarningsPerShareDiluted",
        as_of,
        forms=("10-K",),
        prefer_annual=True,
    )
    if fact is not None:
        return float(fact.value)
    fact = cache.latest_value_at(
        ticker,
        "EarningsPerShareBasic",
        as_of,
        forms=("10-K",),
        prefer_annual=True,
    )
    return float(fact.value) if fact is not None else None


def trailing_growth_pct(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    metric: str,
    years: int = 4,
) -> float | None:
    """4-year trailing CAGR (in %) of ``metric``.

    ``metric`` is one of:
      * ``"revenue"`` — uses any of ``_REVENUE_CONCEPTS``
      * ``"eps"`` — uses ``EarningsPerShareDiluted`` / Basic

    Returns None if we can't establish a "now" or "then" value.
    Negative or zero base values disqualify (CAGR undefined).
    """
    then_date = as_of - timedelta(days=int(365.25 * years))
    if metric == "revenue":
        now_v = _latest_revenue(cache, ticker, as_of)
        then_v = _latest_revenue(cache, ticker, then_date)
    elif metric == "eps":
        now_v = _latest_eps(cache, ticker, as_of)
        then_v = _latest_eps(cache, ticker, then_date)
    else:
        raise ValueError(f"unknown metric: {metric}")
    if now_v is None or then_v is None:
        return None
    if then_v <= 0:
        # Base is non-positive — CAGR is undefined. Returning None
        # rather than a fabricated number is the honest move.
        return None
    if now_v <= 0:
        return -100.0  # collapsed to loss
    cagr = (now_v / then_v) ** (1.0 / years) - 1.0
    return cagr * 100.0


def total_return_to_pe(
    eps_growth_pct: float | None,
    div_yield_pct: float | None,
    pe: float | None,
) -> float | None:
    """Neff's signature metric: (EPS growth + yield) / P/E.

    All inputs in PERCENTAGE points (e.g. 12.5 for 12.5%, not 0.125).
    """
    if eps_growth_pct is None or div_yield_pct is None or pe is None:
        return None
    if pe <= 0:
        return None
    return (eps_growth_pct + div_yield_pct) / pe


# ---- Quality gates --------------------------------------------------------
def passes_quality_gates(
    fin: PointInTimeFinancials | None,
    market_cap_usd: float | None,
    *,
    max_de: float = DEFAULT_MAX_DE,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
) -> FilterResult:
    """Stage-1 filter: per-stock quality gates, before any market-aware
    comparisons. Cheap to compute so we apply it broadly first."""
    ticker = fin.ticker if fin else "<unknown>"
    if fin is None:
        return FilterResult(ticker, False, "no point-in-time financials available")
    if "-" in ticker:
        return FilterResult(ticker, False, "share class or preferred")
    if market_cap_usd is None or market_cap_usd < min_market_cap:
        return FilterResult(
            ticker,
            False,
            f"market cap ${market_cap_usd or 0:,.0f} below ${min_market_cap:,.0f}",
        )
    if fin.net_income is None or fin.net_income <= 0:
        return FilterResult(ticker, False, "non-positive trailing net income")
    de = debt_to_equity(fin)
    if de is None:
        return FilterResult(ticker, False, "D/E undefined")
    if de > max_de:
        return FilterResult(ticker, False, f"D/E {de:.2f} > {max_de}")
    return FilterResult(ticker, True)


def apply_quality_gates(
    candidates: Iterable[
        tuple[PointInTimeFinancials | None, float | None, float | None]
    ],
    *,
    as_of: date,
    max_de: float = DEFAULT_MAX_DE,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
) -> list[tuple[PointInTimeFinancials, float, float]]:
    """Run :func:`passes_quality_gates` over a batch."""
    passed: list[tuple[PointInTimeFinancials, float, float]] = []
    rejected: dict[str, int] = {}
    for fin, mcap, price in candidates:
        r = passes_quality_gates(
            fin, mcap, max_de=max_de, min_market_cap=min_market_cap
        )
        if r.passed and fin is not None and mcap is not None and price is not None:
            passed.append((fin, mcap, price))
        else:
            cat = (r.rejection_reason or "unknown").split(" (")[0]
            rejected[cat] = rejected.get(cat, 0) + 1
    if rejected:
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(rejected.items()))
        logger.info(
            f"{as_of}: Neff quality filter dropped {sum(rejected.values())} ({summary})"
        )
    return passed


# ---- Universe-aware market averages ----------------------------------------
def median(values: list[float]) -> float | None:
    """Robust median; None on empty input."""
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


__all__ = [
    "DEFAULT_MAX_DE",
    "DEFAULT_MIN_GROWTH_PCT",
    "DEFAULT_MAX_GROWTH_PCT",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_MIN_ROE_PCT",
    "DEFAULT_PE_MAX_FRAC_OF_MARKET",
    "DEFAULT_PE_MIN_FRAC_OF_MARKET",
    "DEFAULT_SALES_GROWTH_FLOOR_FRAC",
    "DEFAULT_TR_PE_MARKET_MULTIPLE",
    "DEFAULT_YIELD_PCT_OVER_MARKET",
    "FilterResult",
    "apply_quality_gates",
    "debt_to_equity",
    "dividend_yield",
    "median",
    "passes_quality_gates",
    "pe_ratio",
    "roe",
    "total_return_to_pe",
    "trailing_growth_pct",
]
