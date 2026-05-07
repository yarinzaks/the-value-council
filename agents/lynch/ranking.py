"""PEG-based ranking for Lynch candidates.

Per playbook §5: rank by PEG ascending (lower = cheaper relative to
growth). PEG > 0 required (Lynch doesn't buy negative growth).

Category-aware: each Lynch category has its own PEG ceiling, position
size, and acceptable growth band. We enforce them here so the
strategy layer's responsibility is just orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

from .category_classifier import (
    LynchCategory,
    heuristic_classify,
)
from .filters import debt_to_equity, dividend_yield_pct
from .peg import (
    PEG_BUY,
    PEG_FLOOR,
    PEG_HOLD,
    acceleration_pct,
    peg_buy_zone,
    peg_for,
    trailing_eps_cagr_pct,
)

logger = get_logger("agents.lynch.ranking")


# ---- Per-category PEG ceilings (playbook §4 + §5) -------------------------
#: Slow Growers — Lynch's tightest PEG bar, but PEGY (with dividend
#: included) is the more relevant metric for them.
SLOW_GROWER_MAX_PEG: float = PEG_BUY

#: Stalwarts — entry at PEG ≤ 1.0 per §4.2.
STALWART_MAX_PEG: float = PEG_BUY

#: Fast Growers — Lynch's sweet spot; he prefers ≤ 0.5 but ≤ 1.0 ok.
FAST_GROWER_MAX_PEG: float = PEG_BUY

#: Universal hard ceiling — anti-pattern §10.10 ("PEG above 2.0").
UNIVERSAL_MAX_PEG: float = PEG_HOLD


@dataclass(frozen=True)
class LynchScore:
    """A scored candidate that passed filters AND has a usable PEG."""

    ticker: str
    price: float
    market_cap: float
    pe: float
    growth_rate_5yr_pct: float
    growth_rate_3yr_pct: float | None
    growth_acceleration_pct: float | None
    dividend_yield_pct: float
    peg: float
    pegy: float
    debt_to_equity: float
    net_income: float
    lynch_category: LynchCategory
    peg_zone: str  # "strong_buy" | "buy" | "hold" | "avoid"
    suggested_position_size_pct: float


# ---- Per-category position sizing (playbook §6 + §12.1 step 7) ------------
def _position_size_for(category: LynchCategory) -> float:
    """Lynch's per-category position-size cap, in PERCENT of NAV."""
    return {
        "Slow Grower": 3.0,
        "Stalwart": 5.0,
        "Fast Grower": 5.0,
        "Cyclical": 4.0,
        "Turnaround": 3.0,
        "Asset Play": 4.0,
    }[category]


def _max_peg_for(category: LynchCategory) -> float:
    """Per-category PEG ceiling. Cyclical/Turnaround/Asset Play
    use the universal 2.0 ceiling — those candidates aren't ranked
    on PEG alone (LLM does the work in live mode).
    """
    return {
        "Slow Grower": SLOW_GROWER_MAX_PEG,
        "Stalwart": STALWART_MAX_PEG,
        "Fast Grower": FAST_GROWER_MAX_PEG,
        "Cyclical": UNIVERSAL_MAX_PEG,
        "Turnaround": UNIVERSAL_MAX_PEG,
        "Asset Play": UNIVERSAL_MAX_PEG,
    }[category]


def _pe(price: float, fin: PointInTimeFinancials) -> float | None:
    eps = fin.eps_diluted if fin.eps_diluted is not None else fin.eps_basic
    if eps is None or eps <= 0:
        return None
    return price / eps


def score_candidates(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
    *,
    as_of: date,
    edgar_cache: EdgarCache,
) -> list[LynchScore]:
    """Compute PEG, classify into category (heuristic), apply
    category-specific PEG ceiling, return survivors sorted by PEG asc.
    """
    if not candidates:
        return []

    out: list[LynchScore] = []
    for fin, mcap, price in candidates:
        pe = _pe(price, fin)
        if pe is None:
            continue
        g5 = trailing_eps_cagr_pct(edgar_cache, fin.ticker, as_of, years=5)
        if g5 is None or g5 <= PEG_FLOOR:
            continue
        g3 = trailing_eps_cagr_pct(edgar_cache, fin.ticker, as_of, years=3)
        accel = acceleration_pct(edgar_cache, fin.ticker, as_of)
        yld = dividend_yield_pct(mcap, fin) or 0.0
        peg = peg_for(
            pe=pe,
            growth_rate_pct=g5,
            dividend_yield_pct=yld,
        )
        if peg is None:
            continue

        category = heuristic_classify(
            growth_rate_pct=g5,
            dividend_yield_pct=yld,
            market_cap_usd=mcap,
        )
        if category is None:
            # No clean quant fit (e.g., 5-10% grower with no dividend) —
            # not a Lynch candidate via the heuristic path. The LLM
            # could still classify them in live mode; the backtest
            # skips them.
            continue

        max_peg = _max_peg_for(category)
        # Slow Growers use PEGY (yield-adjusted) per §5.2; everyone
        # else uses raw PEG.
        relevant_peg = peg.pegy if category == "Slow Grower" else peg.peg
        if relevant_peg > max_peg:
            continue

        de = debt_to_equity(fin)
        out.append(
            LynchScore(
                ticker=fin.ticker,
                price=price,
                market_cap=mcap,
                pe=pe,
                growth_rate_5yr_pct=g5,
                growth_rate_3yr_pct=g3,
                growth_acceleration_pct=accel,
                dividend_yield_pct=yld,
                peg=peg.peg,
                pegy=peg.pegy,
                debt_to_equity=de if de is not None else 0.0,
                net_income=fin.net_income or 0.0,
                lynch_category=category,
                peg_zone=peg_buy_zone(relevant_peg),
                suggested_position_size_pct=_position_size_for(category),
            )
        )

    # Sort by relevant PEG asc — lowest PEG = best Lynch candidate.
    out.sort(
        key=lambda s: s.pegy if s.lynch_category == "Slow Grower" else s.peg
    )
    return out


def select_top_n(scores: list[LynchScore], n: int) -> list[LynchScore]:
    """Take the top ``n`` by PEG. Take all if fewer."""
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


__all__ = [
    "FAST_GROWER_MAX_PEG",
    "LynchScore",
    "SLOW_GROWER_MAX_PEG",
    "STALWART_MAX_PEG",
    "UNIVERSAL_MAX_PEG",
    "score_candidates",
    "select_top_n",
]
