"""Schloss ranking — sort by P/B ascending, take the cheapest N.

Schloss did not use a "magic" multi-metric ranking. He bought any
stock that met his criteria and held it until ~50% gain, with new
purchases concentrated in the deepest discounts. For a backtest at
N positions equal-weight, the natural rule is "the N cheapest by P/B
that pass the filters."
"""

from __future__ import annotations

from dataclasses import dataclass

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger

from .filters import debt_to_equity, price_to_book

logger = get_logger("agents.schloss.ranking")


@dataclass(frozen=True)
class SchlossScore:
    """Ranking row for one candidate."""

    ticker: str
    price: float
    market_cap: float
    book_value_per_share: float
    pb_ratio: float
    debt_to_equity: float
    net_income: float | None


def score_candidates(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
) -> list[SchlossScore]:
    """Sort candidates by P/B ascending (cheapest first).

    Args:
        candidates: List of ``(financials, market_cap, price)`` tuples
            already filtered to passing names.

    Returns:
        :class:`SchlossScore` list sorted by P/B ascending. Ties broken
        by D/E ascending (lower leverage preferred when equally cheap).
    """
    scores: list[SchlossScore] = []
    for fin, market_cap, price in candidates:
        pb = price_to_book(price, fin)
        de = debt_to_equity(fin)
        if pb is None or de is None or fin.total_equity is None or fin.shares_outstanding is None:
            # Defensive — should already be filtered out
            continue
        scores.append(
            SchlossScore(
                ticker=fin.ticker,
                price=price,
                market_cap=market_cap,
                book_value_per_share=fin.total_equity / fin.shares_outstanding,
                pb_ratio=pb,
                debt_to_equity=de,
                net_income=fin.net_income,
            )
        )
    # Cheapest first; ties broken by lower leverage
    scores.sort(key=lambda s: (s.pb_ratio, s.debt_to_equity))
    return scores


def select_top_n(
    scores: list[SchlossScore], n: int
) -> list[SchlossScore]:
    """Return the cheapest ``n`` scores. If fewer are available,
    return all — Schloss's framework explicitly takes whatever the
    universe offers rather than forcing artificial picks."""
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


__all__ = ["SchlossScore", "score_candidates", "select_top_n"]
