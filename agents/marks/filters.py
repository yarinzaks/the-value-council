"""Quality gates for the Marks agent.

Marks's framework is "less quantitative" by design (playbook §4) —
the heavy lifting happens in :mod:`temperature` (cycle position) and
:mod:`second_level` (LLM second-level memo).

The gates here are deliberately light. We exclude obvious anti-
patterns from playbook §10 that we can prove from XBRL data:

  * No share-class / preferred tickers
  * Non-trivial market cap (avoid micro-cap noise)
  * Positive book equity
  * D/E ≤ 1.0 (looser than other agents — Marks accepts more
    leverage than Buffett because cycle-positioning, not single-
    name analysis, is the safety net)
  * Positive trailing net income (we operate in equities; deeply
    distressed names with negative earnings need debt-side analysis
    Marks would do, but our backtest framework doesn't support that)

Posture-aware tightening is applied in :mod:`ranking`, not here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger
from core.scoring.leverage import debt_to_equity as _shared_debt_to_equity

logger = get_logger("agents.marks.filters")


DEFAULT_MIN_MARKET_CAP_USD: float = 500_000_000.0
DEFAULT_MAX_DE: float = 1.0


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
    """Run the light Marks quality gates."""
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
            f"{as_of}: Marks quality filter dropped {sum(rejected.values())} ({summary})"
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
