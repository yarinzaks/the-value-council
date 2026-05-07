"""Tier-based ranking for Fisher candidates.

After quality gates, this module:

  1. Computes the quant 5-point :class:`QualityScore` (:mod:`quality_score`).
  2. Classifies each candidate into a Fisher tier:
       * 5/5 quant points  → Tier A
       * 4/5 quant points  → Tier B
       * ≤3/5             → reject (note: in live mode the LLM can
                            still upgrade or reject)
  3. Applies a P/E sanity-check ceiling (playbook §4.3):
       * Tier A: P/E ≤ 35 (only highest-conviction tolerates more)
       * Tier B: P/E ≤ 25
  4. Ranks within tier by quant points (5 first, 4 second), then by
     P/E ascending — within a tier, cheaper wins.

The strategy class consumes this output and applies tier-specific
position sizing per playbook §5.2:

  * Tier A: 8-15% per position (we use 12% default)
  * Tier B: 4-7%  per position (we use 6% default)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

from .filters import debt_to_equity
from .quality_score import (
    DEFAULT_MARGIN_TREND_FLOOR_BPS,
    DEFAULT_MAX_SHARE_DILUTION_PCT_5YR,
    DEFAULT_MIN_OPERATING_MARGIN_PCT,
    DEFAULT_MIN_RD_TO_REVENUE_PCT,
    DEFAULT_MIN_REVENUE_CAGR_PCT,
    QualityScore,
    score_quality,
)

logger = get_logger("agents.fisher.ranking")


Tier = Literal["A", "B"]


# ---- Per-tier P/E ceilings (playbook §4.3) -------------------------------
TIER_A_MAX_PE: float = 35.0
TIER_B_MAX_PE: float = 25.0

# ---- Per-tier position-size targets (playbook §5.2 / §6.2) ---------------
TIER_A_POSITION_PCT: float = 12.0
TIER_B_POSITION_PCT: float = 6.0


@dataclass(frozen=True)
class FisherScore:
    """A scored candidate that cleared a Fisher tier."""

    ticker: str
    price: float
    market_cap: float
    pe: float
    quality_points: int
    tier: Tier
    suggested_position_size_pct: float
    debt_to_equity: float
    net_income: float
    quality_score: QualityScore  # carries the per-point breakdown


def _pe(price: float, fin: PointInTimeFinancials) -> float | None:
    eps = fin.eps_diluted if fin.eps_diluted is not None else fin.eps_basic
    if eps is None or eps <= 0 or price <= 0:
        return None
    return price / eps


def score_candidates(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
    *,
    as_of: date,
    edgar_cache: EdgarCache,
    min_revenue_cagr_pct: float = DEFAULT_MIN_REVENUE_CAGR_PCT,
    min_rd_to_revenue_pct: float = DEFAULT_MIN_RD_TO_REVENUE_PCT,
    min_operating_margin_pct: float = DEFAULT_MIN_OPERATING_MARGIN_PCT,
    margin_trend_floor_bps: float = DEFAULT_MARGIN_TREND_FLOOR_BPS,
    max_share_dilution_pct_5yr: float = DEFAULT_MAX_SHARE_DILUTION_PCT_5YR,
    tier_a_max_pe: float = TIER_A_MAX_PE,
    tier_b_max_pe: float = TIER_B_MAX_PE,
) -> list[FisherScore]:
    """Score by 5-point quant + classify into Tier A / Tier B."""
    if not candidates:
        return []

    out: list[FisherScore] = []
    for fin, mcap, price in candidates:
        pe = _pe(price, fin)
        if pe is None:
            continue
        qs = score_quality(
            fin,
            cache=edgar_cache,
            as_of=as_of,
            min_revenue_cagr_pct=min_revenue_cagr_pct,
            min_rd_to_revenue_pct=min_rd_to_revenue_pct,
            min_operating_margin_pct=min_operating_margin_pct,
            margin_trend_floor_bps=margin_trend_floor_bps,
            max_share_dilution_pct_5yr=max_share_dilution_pct_5yr,
        )

        if qs.points_passed >= 5:
            tier: Tier = "A"
            max_pe = tier_a_max_pe
            size = TIER_A_POSITION_PCT
        elif qs.points_passed == 4:
            tier = "B"
            max_pe = tier_b_max_pe
            size = TIER_B_POSITION_PCT
        else:
            # < 4 points: not a Fisher candidate at all.
            continue

        if pe > max_pe:
            # Quality is real but multiple is excessive — Fisher waits
            # for a better entry rather than overpaying.
            continue

        de = debt_to_equity(fin)
        out.append(
            FisherScore(
                ticker=fin.ticker,
                price=price,
                market_cap=mcap,
                pe=pe,
                quality_points=qs.points_passed,
                tier=tier,
                suggested_position_size_pct=size,
                debt_to_equity=de if de is not None else 0.0,
                net_income=fin.net_income or 0.0,
                quality_score=qs,
            )
        )

    # Tier A first (5/5), then Tier B (4/5); within each tier, lower
    # P/E wins (Fisher: pay full price for quality, but cheaper is
    # still better when quality is held constant).
    out.sort(key=lambda s: (0 if s.tier == "A" else 1, s.pe))
    return out


def select_top_n(scores: list[FisherScore], n: int) -> list[FisherScore]:
    """Take the top ``n`` by tier+P/E ordering. Take all if fewer."""
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


__all__ = [
    "FisherScore",
    "TIER_A_MAX_PE",
    "TIER_A_POSITION_PCT",
    "TIER_B_MAX_PE",
    "TIER_B_POSITION_PCT",
    "Tier",
    "score_candidates",
    "select_top_n",
]
