"""Quality gates for the Fisher agent.

Per playbook §10, Fisher refuses to buy companies with weak R&D
in R&D-dependent industries, accounting controls red flags,
integrity questions, or excessive leverage. The integrity check is
qualitative (LLM-only); the rest map to data we have:

  * Share-class / preferred tickers (Council non-negotiable)
  * Market-cap floor (Fisher held mid- to large-cap names)
  * Positive book equity
  * Positive trailing net income (Point 15 / 10 proxy — companies
    in real distress fail integrity-by-association in a backtest)
  * D/E ≤ 0.6 (anti-pattern §10.12)
  * No accounting red flag — proxied by 10-year earnings consistency
    in :mod:`quality_growth` (we don't enforce here to keep the gate
    cheap; the strategy reads the cache once and re-uses)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger
from core.scoring.leverage import debt_to_equity as _shared_debt_to_equity

logger = get_logger("agents.fisher.filters")


DEFAULT_MIN_MARKET_CAP_USD: float = 1_000_000_000.0
DEFAULT_MAX_DE: float = 0.6


@dataclass(frozen=True)
class FilterResult:
    ticker: str
    passed: bool
    rejection_reason: str | None = None


def debt_to_equity(fin: PointInTimeFinancials | None) -> float | None:
    """D/E, or None when the ratio cannot be honestly established.

    Delegates to :func:`core.scoring.leverage.debt_to_equity`. This used
    to return 0.0 when no debt concept was tagged, which is the best
    possible score on every leverage gate — 37% of the judgeable
    universe passed on no evidence. See that module for why plain None
    is also wrong and what distinguishes the two cases.
    """
    return _shared_debt_to_equity(fin)


def passes_quality_gates(
    fin: PointInTimeFinancials | None,
    market_cap_usd: float | None,
    *,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
    max_de: float = DEFAULT_MAX_DE,
) -> FilterResult:
    ticker = fin.ticker if fin else "<unknown>"
    if fin is None:
        return FilterResult(ticker, False, "no point-in-time financials")
    if "-" in ticker:
        return FilterResult(ticker, False, "share class or preferred")
    if market_cap_usd is None or market_cap_usd < min_market_cap:
        return FilterResult(
            ticker,
            False,
            f"market cap ${market_cap_usd or 0:,.0f} below ${min_market_cap:,.0f}",
        )
    if fin.total_equity is None or fin.total_equity <= 0:
        return FilterResult(ticker, False, "non-positive book equity")
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
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
    max_de: float = DEFAULT_MAX_DE,
) -> list[tuple[PointInTimeFinancials, float, float]]:
    """Run :func:`passes_quality_gates` over a batch."""
    passed: list[tuple[PointInTimeFinancials, float, float]] = []
    rejected: dict[str, int] = {}
    for fin, mcap, price in candidates:
        r = passes_quality_gates(
            fin, mcap, min_market_cap=min_market_cap, max_de=max_de
        )
        if r.passed and fin is not None and mcap is not None and price is not None:
            passed.append((fin, mcap, price))
        else:
            cat = (r.rejection_reason or "unknown").split(" (")[0].split(":")[0]
            rejected[cat] = rejected.get(cat, 0) + 1
    if rejected:
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(rejected.items()))
        logger.info(
            f"{as_of}: Fisher quality filter dropped {sum(rejected.values())} ({summary})"
        )
    return passed


__all__ = [
    "DEFAULT_MAX_DE",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "FilterResult",
    "apply_quality_gates",
    "debt_to_equity",
    "passes_quality_gates",
]
