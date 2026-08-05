"""Dreman 4-metric contrarian filters.

Per the playbook (Section 4): rank candidates in the **bottom 20%** of
the universe on at least **2 of 4 metrics**:

* P/E (price / EPS)
* P/CF (price / operating cash flow per share)
* P/B (price / book value per share)
* Dividend yield (high yield = bottom on Price/Dividend)

Dreman's empirical research showed contrarian quintile portfolios
outperformed the market across 70 years of data. We implement the
quintile filter at universe scan time — a candidate's metric values
are compared against the population distribution at the rebalance
date, and only those in the bottom (or top, for yield) 20% qualify.

Beyond the quintile gate, Dreman also requires basic quality:

* Positive trailing net income (no money-losers; filters value traps)
* Manageable debt (D/E ≤ 1.0)
* Adequate market cap ($500M floor per priority spec)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger
from core.scoring.leverage import debt_to_equity as _shared_debt_to_equity

logger = get_logger("agents.dreman.filters")


DEFAULT_QUINTILE: float = 0.20  # bottom 20% (or top 20% for yield)
DEFAULT_MIN_QUALIFYING_METRICS: int = 2
DEFAULT_MAX_DE: float = 1.0
DEFAULT_MIN_MARKET_CAP_USD: float = 500_000_000.0


@dataclass(frozen=True)
class FilterResult:
    ticker: str
    passed: bool
    rejection_reason: str | None = None


def pe_ratio(price: float | None, fin: PointInTimeFinancials | None) -> float | None:
    """Trailing P/E using diluted EPS when available, else basic."""
    if fin is None or price is None or price <= 0:
        return None
    eps = fin.eps_diluted if fin.eps_diluted is not None else fin.eps_basic
    if eps is None or eps <= 0:
        return None
    return price / eps


def pcf_ratio(
    market_cap: float | None, fin: PointInTimeFinancials | None
) -> float | None:
    """Price / operating-cash-flow."""
    if fin is None or market_cap is None or market_cap <= 0:
        return None
    ocf = fin.operating_cash_flow
    if ocf is None or ocf <= 0:
        return None
    return market_cap / ocf


def pb_ratio(
    market_cap: float | None, fin: PointInTimeFinancials | None
) -> float | None:
    """Price / book (market cap / total equity)."""
    if fin is None or market_cap is None or market_cap <= 0:
        return None
    eq = fin.total_equity
    if eq is None or eq <= 0:
        return None
    return market_cap / eq


def dividend_yield(
    market_cap: float | None, fin: PointInTimeFinancials | None
) -> float | None:
    """Trailing dividend yield = abs(dividends_paid) / market_cap.

    SEC tags ``PaymentsOfDividends`` as a positive cash outflow value
    in some filings and as a negative number in others. We use the
    absolute value to be robust to either convention.
    """
    if fin is None or market_cap is None or market_cap <= 0:
        return None
    div = fin.dividends_paid
    if div is None:
        return 0.0
    return abs(div) / market_cap


def debt_to_equity(fin: PointInTimeFinancials | None) -> float | None:
    """D/E, or None when the ratio cannot be honestly established.

    Delegates to :func:`core.scoring.leverage.debt_to_equity`. This used
    to return 0.0 when no debt concept was tagged, which is the best
    possible score on every leverage gate — 37% of the judgeable
    universe passed on no evidence. See that module for why plain None
    is also wrong and what distinguishes the two cases.
    """
    return _shared_debt_to_equity(fin)


def quintile_thresholds(
    values: list[float], *, quintile: float = DEFAULT_QUINTILE
) -> tuple[float, float]:
    """Return (low_cutoff, high_cutoff) for the bottom and top quintiles.

    A value v is in the bottom quintile iff v <= low_cutoff.
    A value v is in the top quintile iff v >= high_cutoff.
    """
    if not values:
        return (float("inf"), float("-inf"))
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    # 0-indexed; bottom 20% means index [0, n*0.2) — use the value at n*0.2 as upper bound
    low_idx = max(0, int(n * quintile) - 1)
    high_idx = min(n - 1, int(n * (1.0 - quintile)))
    return (sorted_vals[low_idx], sorted_vals[high_idx])


def passes_quality_gates(
    fin: PointInTimeFinancials | None,
    market_cap_usd: float | None,
    *,
    max_de: float = DEFAULT_MAX_DE,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
) -> FilterResult:
    """Stage-1 filter: quality gates that don't depend on the population.

    Stage-2 (the quintile screen) needs the full universe of metric
    values to compute thresholds — that runs in the strategy.
    """
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
        return FilterResult(ticker, False, "D/E undefined (no positive equity, or no debt reported on a sparse balance sheet)")
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
            f"{as_of}: quality filter dropped {sum(rejected.values())} ({summary})"
        )
    return passed


__all__ = [
    "DEFAULT_MAX_DE",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_MIN_QUALIFYING_METRICS",
    "DEFAULT_QUINTILE",
    "FilterResult",
    "apply_quality_gates",
    "debt_to_equity",
    "dividend_yield",
    "passes_quality_gates",
    "pb_ratio",
    "pcf_ratio",
    "pe_ratio",
    "quintile_thresholds",
]
