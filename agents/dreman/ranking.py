"""Dreman 4-metric contrarian ranking.

Population-aware: takes a batch of candidates that already passed the
quality gates and computes quintile thresholds across the *whole batch*
on each of:

* P/E, P/CF, P/B — bottom 20% qualifies (low = cheap)
* Dividend yield — top 20% qualifies (high yield = cheap)

A candidate that qualifies on **at least N** of the 4 (default 2)
makes the cut. Among those, we prefer:

1. Higher number of qualifying metrics (4 > 3 > 2)
2. Lower composite percentile rank — average of metric ranks where 0.0
   is best (e.g. cheapest P/E, highest yield).

Then the strategy takes the top ``portfolio_size`` (typically 20-30).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger

from .filters import (
    DEFAULT_MIN_QUALIFYING_METRICS,
    DEFAULT_QUINTILE,
    debt_to_equity,
    dividend_yield,
    pb_ratio,
    pcf_ratio,
    pe_ratio,
    quintile_thresholds,
)

logger = get_logger("agents.dreman.ranking")


@dataclass(frozen=True)
class DremanScore:
    """Ranking row for a contrarian candidate."""

    ticker: str
    price: float
    market_cap: float
    pe: float | None
    pcf: float | None
    pb: float | None
    div_yield: float | None
    qualifying_metrics: int  # 0..4
    qualifying_flags: tuple[bool, bool, bool, bool]  # (pe, pcf, pb, yield)
    composite_rank: float  # 0..1, lower is better
    debt_to_equity: float
    net_income: float


def _percentile_rank(value: float, sorted_values: list[float]) -> float:
    """Fraction of population strictly below ``value``. 0.0 = lowest, 1.0 = highest."""
    n = len(sorted_values)
    if n == 0:
        return 0.5
    # bisect-left for tie handling
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_values[mid] < value:
            lo = mid + 1
        else:
            hi = mid
    return lo / n


def score_candidates(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
    *,
    min_qualifying_metrics: int = DEFAULT_MIN_QUALIFYING_METRICS,
    quintile: float = DEFAULT_QUINTILE,
) -> list[DremanScore]:
    """Compute population-aware Dreman scores.

    Returns only candidates qualifying on >= ``min_qualifying_metrics``,
    sorted by (qualifying_metrics desc, composite_rank asc).
    """
    if not candidates:
        return []

    # Compute the four metric arrays. None values are dropped from the
    # threshold population so missing data doesn't skew quintile cuts.
    metrics: list[tuple[str, float, float, float | None, float | None, float | None, float | None, float, float]] = []
    pe_pop: list[float] = []
    pcf_pop: list[float] = []
    pb_pop: list[float] = []
    yld_pop: list[float] = []
    for fin, mcap, price in candidates:
        pe = pe_ratio(price, fin)
        pcf = pcf_ratio(mcap, fin)
        pb = pb_ratio(mcap, fin)
        yld = dividend_yield(mcap, fin)
        de = debt_to_equity(fin) or 0.0
        ni = fin.net_income if fin.net_income is not None else 0.0
        metrics.append((fin.ticker, price, mcap, pe, pcf, pb, yld, de, ni))
        if pe is not None:
            pe_pop.append(pe)
        if pcf is not None:
            pcf_pop.append(pcf)
        if pb is not None:
            pb_pop.append(pb)
        if yld is not None:
            yld_pop.append(yld)

    # Quintile thresholds. For P/E, P/CF, P/B: bottom quintile (cheap).
    # For yield: top quintile (high). quintile_thresholds returns
    # (low_cutoff, high_cutoff): qualifies if value <= low_cutoff (for
    # cheapness metrics) or >= high_cutoff (for yield).
    pe_low, _ = quintile_thresholds(pe_pop, quintile=quintile)
    pcf_low, _ = quintile_thresholds(pcf_pop, quintile=quintile)
    pb_low, _ = quintile_thresholds(pb_pop, quintile=quintile)
    _, yld_high = quintile_thresholds(yld_pop, quintile=quintile)

    pe_sorted = sorted(pe_pop)
    pcf_sorted = sorted(pcf_pop)
    pb_sorted = sorted(pb_pop)
    yld_sorted = sorted(yld_pop)

    out: list[DremanScore] = []
    for ticker, price, mcap, pe, pcf, pb, yld, de, ni in metrics:
        flags = (
            pe is not None and pe <= pe_low,
            pcf is not None and pcf <= pcf_low,
            pb is not None and pb <= pb_low,
            yld is not None and yld >= yld_high,
        )
        n_qual = sum(flags)
        if n_qual < min_qualifying_metrics:
            continue

        # Composite rank: average percentile where 0.0 = best for each
        # metric. Cheapness metrics use raw percentile; yield uses
        # 1 - percentile so high-yield = low rank.
        ranks: list[float] = []
        if pe is not None:
            ranks.append(_percentile_rank(pe, pe_sorted))
        if pcf is not None:
            ranks.append(_percentile_rank(pcf, pcf_sorted))
        if pb is not None:
            ranks.append(_percentile_rank(pb, pb_sorted))
        if yld is not None:
            ranks.append(1.0 - _percentile_rank(yld, yld_sorted))
        composite = sum(ranks) / len(ranks) if ranks else 1.0

        out.append(
            DremanScore(
                ticker=ticker,
                price=price,
                market_cap=mcap,
                pe=pe,
                pcf=pcf,
                pb=pb,
                div_yield=yld,
                qualifying_metrics=n_qual,
                qualifying_flags=flags,
                composite_rank=composite,
                debt_to_equity=de,
                net_income=ni,
            )
        )

    out.sort(key=lambda s: (-s.qualifying_metrics, s.composite_rank))
    return out


def select_top_n(scores: list[DremanScore], n: int) -> list[DremanScore]:
    """Return the top ``n`` contrarian candidates.

    Take all if fewer available. Raises on non-positive ``n``.
    """
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


__all__ = ["DremanScore", "score_candidates", "select_top_n"]
