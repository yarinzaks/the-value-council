"""Margin-of-safety ranking for Klarman candidates.

After quality gates (:mod:`filters`), this module:

  1. Computes conservative DCF intrinsic value via FCF (:mod:`valuation`).
  2. Computes margin of safety = (IV − Mcap) / IV.
  3. Filters out anything below ``min_mos_pct`` (default 30%, the
     playbook §4.2 floor for public equities).
  4. Returns survivors sorted by MoS descending.

The cash-as-residual sizing logic lives in the strategy class — it
needs the FULL output of this ranker (the count of qualifying
candidates) to decide deployment intensity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

from .filters import debt_to_equity
from .valuation import (
    DEFAULT_DCF_YEARS,
    DEFAULT_DISCOUNT_RATE_PCT,
    DEFAULT_FCF_AVG_YEARS,
    DEFAULT_MIN_MARGIN_OF_SAFETY_PCT,
    DEFAULT_TERMINAL_MULTIPLE,
    IntrinsicValueResult,
    intrinsic_value,
    margin_of_safety_pct,
)

logger = get_logger("agents.klarman.ranking")


@dataclass(frozen=True)
class KlarmanScore:
    """A scored candidate that cleared MoS floor."""

    ticker: str
    price: float
    market_cap: float
    intrinsic_value_usd: float
    intrinsic_value_per_share: float
    margin_of_safety_pct: float
    avg_fcf_usd: float
    growth_rate_pct: float
    discount_rate_pct: float
    debt_to_equity: float
    net_income: float
    valuation_notes: tuple[str, ...] = ()


def _per_share(iv_total: float, fin: PointInTimeFinancials) -> float | None:
    if fin.shares_outstanding is None or fin.shares_outstanding <= 0:
        return None
    return iv_total / fin.shares_outstanding


def score_candidates(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
    *,
    as_of: date,
    edgar_cache: EdgarCache,
    discount_rate_pct: float = DEFAULT_DISCOUNT_RATE_PCT,
    terminal_multiple: float = DEFAULT_TERMINAL_MULTIPLE,
    dcf_years: int = DEFAULT_DCF_YEARS,
    history_years: int = DEFAULT_FCF_AVG_YEARS,
    min_mos_pct: float = DEFAULT_MIN_MARGIN_OF_SAFETY_PCT,
) -> list[KlarmanScore]:
    """Score by MoS. Drop below ``min_mos_pct`` or with no IV."""
    if not candidates:
        return []

    out: list[KlarmanScore] = []
    for fin, mcap, price in candidates:
        iv: IntrinsicValueResult | None = intrinsic_value(
            edgar_cache,
            fin.ticker,
            as_of,
            discount_rate_pct=discount_rate_pct,
            terminal_multiple=terminal_multiple,
            years=dcf_years,
            history_years=history_years,
        )
        if iv is None:
            continue
        mos = margin_of_safety_pct(iv.intrinsic_value_usd, mcap)
        if mos is None or mos < min_mos_pct:
            continue
        ips = _per_share(iv.intrinsic_value_usd, fin)
        if ips is None:
            continue
        de = debt_to_equity(fin)
        out.append(
            KlarmanScore(
                ticker=fin.ticker,
                price=price,
                market_cap=mcap,
                intrinsic_value_usd=iv.intrinsic_value_usd,
                intrinsic_value_per_share=ips,
                margin_of_safety_pct=mos,
                avg_fcf_usd=iv.avg_fcf_usd,
                growth_rate_pct=iv.growth_rate_pct,
                discount_rate_pct=iv.discount_rate_pct,
                debt_to_equity=de if de is not None else 0.0,
                net_income=fin.net_income or 0.0,
                valuation_notes=iv.notes,
            )
        )

    out.sort(key=lambda s: -s.margin_of_safety_pct)
    return out


def select_top_n(scores: list[KlarmanScore], n: int) -> list[KlarmanScore]:
    """Take the top ``n`` by MoS. Take all if fewer."""
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


__all__ = [
    "KlarmanScore",
    "score_candidates",
    "select_top_n",
]
