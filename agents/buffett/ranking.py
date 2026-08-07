"""Margin-of-safety ranking for Buffett candidates.

Inputs are candidates that have already passed the six Berkshire
Acquisition Criteria (see ``filters.py``). For each, we compute
DCF intrinsic value via Owner Earnings (see ``owner_earnings.py``)
and rank by margin of safety:

    MoS = (Intrinsic Value − Market Cap) / Intrinsic Value

The default MoS floor is 15% per playbook §12.1 step 8.

For the BACKTEST path (no LLM), this is the entire ranking. Top N
by MoS are equal-weighted.

For the LIVE path, the LLM moat analyzer (``moat_analyzer.py``)
intervenes between this ranking and the final selection — it can
reject a quantitatively-cheap candidate whose moat is weak.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

from .filters import avg_roe_5yr, debt_to_equity
from .moat import DEFAULT_MIN_ROE_PCT, FranchiseAssessment, assess_franchise
from .owner_earnings import (
    DEFAULT_DCF_YEARS,
    DEFAULT_DISCOUNT_RATE_PCT,
    DEFAULT_OE_AVG_YEARS,
    DEFAULT_TERMINAL_MULTIPLE,
    IntrinsicValueResult,
    intrinsic_value,
    margin_of_safety_pct,
)

logger = get_logger("agents.buffett.ranking")

#: Minimum margin-of-safety to qualify for selection. 15% per playbook
#: §12.1 step 8 — Buffett's MoS bar is lower than Graham's 33% because
#: the quality bar is higher.
DEFAULT_MIN_MARGIN_OF_SAFETY_PCT: float = 15.0


@dataclass(frozen=True)
class BuffettScore:
    """A candidate that passed quality gates AND has a usable IV."""

    ticker: str
    price: float
    market_cap: float
    intrinsic_value_usd: float  # total company value
    intrinsic_value_per_share: float
    margin_of_safety_pct: float
    avg_owner_earnings_usd: float
    growth_rate_pct: float
    discount_rate_pct: float
    avg_roe_5yr_pct: float
    debt_to_equity: float
    net_income: float
    valuation_notes: tuple[str, ...] = ()
    #: Evidence for a durable advantage. ``None`` only when the gate was
    #: disabled; a candidate that failed it never reaches this list.
    franchise: FranchiseAssessment | None = None


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
    history_years: int = DEFAULT_OE_AVG_YEARS,
    min_mos_pct: float = DEFAULT_MIN_MARGIN_OF_SAFETY_PCT,
    require_franchise: bool = True,
    min_roe_pct: float = DEFAULT_MIN_ROE_PCT,
) -> list[BuffettScore]:
    """Score candidates, franchise first and price second.

    Two changes from sorting on margin of safety alone. A candidate must
    now show a durable advantage — see :mod:`agents.buffett.moat` — and
    the survivors are ordered by the strength of that advantage, with
    cheapness breaking ties.

    Sorting on discount alone meant the agent bought the *cheapest*
    survivor of a quality floor rather than the *best* business
    available at a fair price, which is the opposite of the position
    Buffett spent the 1980s arguing for: "a wonderful company at a fair
    price" over "a fair company at a wonderful price".
    """
    if not candidates:
        return []

    out: list[BuffettScore] = []
    rejected_no_moat = 0
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

        assessment = assess_franchise(
            edgar_cache, fin.ticker, as_of, min_roe_pct=min_roe_pct
        )
        if require_franchise and not assessment.qualifies:
            rejected_no_moat += 1
            continue
        ips = _per_share(iv.intrinsic_value_usd, fin)
        if ips is None:
            continue
        avg_roe = avg_roe_5yr(edgar_cache, fin.ticker, as_of)
        de = debt_to_equity(fin)
        out.append(
            BuffettScore(
                ticker=fin.ticker,
                price=price,
                market_cap=mcap,
                intrinsic_value_usd=iv.intrinsic_value_usd,
                intrinsic_value_per_share=ips,
                margin_of_safety_pct=mos,
                avg_owner_earnings_usd=iv.avg_owner_earnings_usd,
                growth_rate_pct=iv.growth_rate_pct,
                discount_rate_pct=iv.discount_rate_pct,
                avg_roe_5yr_pct=avg_roe if avg_roe is not None else 0.0,
                debt_to_equity=de if de is not None else 0.0,
                net_income=fin.net_income or 0.0,
                valuation_notes=iv.notes,
                franchise=assessment,
            )
        )

    if rejected_no_moat:
        logger.info(
            f"{as_of}: {rejected_no_moat} candidate(s) cheap enough but with no "
            f"durable advantage on the record"
        )

    # Franchise first, price second. The primary key is how many of the
    # observed years cleared the return bar, then the worst of them —
    # a business whose floor is high has pricing power it did not have
    # to fight for. Margin of safety only separates equals.
    def _key(s: BuffettScore) -> tuple[float, float, float]:
        f = s.franchise
        return (
            -(f.fraction_above if f else 0.0),
            -(f.worst_roe_pct if f and f.worst_roe_pct is not None else -1e9),
            -s.margin_of_safety_pct,
        )

    out.sort(key=_key)
    return out


def select_top_n(scores: list[BuffettScore], n: int) -> list[BuffettScore]:
    """Take the top ``n`` by MoS. Take all if fewer."""
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


__all__ = [
    "DEFAULT_MIN_MARGIN_OF_SAFETY_PCT",
    "BuffettScore",
    "score_candidates",
    "select_top_n",
]
