"""Graham ranking — Net-Net (P/NCAV) and Defensive Investor (composite)."""

from __future__ import annotations

from dataclasses import dataclass

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger
from core.scoring.graham_number import graham_number

from .filters import (
    current_ratio,
    debt_to_equity,
    ncav_per_share,
    pb_ratio,
    pe_ratio,
    price_to_ncav,
)

logger = get_logger("agents.graham.ranking")


@dataclass(frozen=True)
class GrahamScore:
    """Ranking row for a Net-Net candidate."""

    ticker: str
    price: float
    market_cap: float
    ncav_per_share: float
    p_ncav: float
    debt_to_equity: float
    net_income: float


@dataclass(frozen=True)
class DefensiveScore:
    """Ranking row for a Defensive Investor candidate.

    ``composite`` is the cheapness metric we sort on — lower is better.
    Defined as ``pe * pb``, which is the ch.14 combined test: Graham
    wanted the product not to exceed 22.5 = 15 x 1.5. Cheaper on either
    dimension gives a lower composite regardless of which one drove it.

    ``graham_number`` is a different thing and must not be confused with
    it. The composite is a dimensionless product; the Graham Number is
    sqrt(22.5 x EPS x BVPS), a price per share, and it is what the
    playbook's sell trigger refers to. The decision log used to record
    the composite under the name "graham_number", which made the trigger
    uncheckable — you cannot compare a share price to a dimensionless
    product.
    """

    ticker: str
    price: float
    market_cap: float
    pe: float
    pb: float
    current_ratio: float
    debt_to_equity: float
    net_income: float
    composite: float  # pe * pb — cheaper = lower
    #: sqrt(22.5 x EPS x BVPS), in dollars per share. None when EPS or
    #: book value is non-positive, where the formula is meaningless.
    graham_number: float | None = None
    #: Discount to the Graham Number, in percent. Negative means the
    #: price is above it.
    margin_of_safety_pct: float | None = None


def score_candidates(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
) -> list[GrahamScore]:
    """Return :class:`GrahamScore` list sorted by P/NCAV ascending.

    Ties broken by lower D/E (less leverage preferred at equal cheapness).
    """
    scores: list[GrahamScore] = []
    for fin, market_cap, price in candidates:
        ncav = ncav_per_share(fin)
        p_ncav = price_to_ncav(price, fin)
        de = debt_to_equity(fin)
        if ncav is None or p_ncav is None or de is None or fin.net_income is None:
            continue
        scores.append(
            GrahamScore(
                ticker=fin.ticker,
                price=price,
                market_cap=market_cap,
                ncav_per_share=ncav,
                p_ncav=p_ncav,
                debt_to_equity=de,
                net_income=fin.net_income,
            )
        )
    scores.sort(key=lambda s: (s.p_ncav, s.debt_to_equity))
    return scores


def score_defensive_candidates(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
) -> list[DefensiveScore]:
    """Return :class:`DefensiveScore` list sorted by ``pe * pb`` ascending.

    Graham's Number rule of thumb: the product of P/E and P/B should
    not exceed 22.5 — the same product makes a useful relative ranking
    among survivors of the Defensive Investor screen.
    """
    scores: list[DefensiveScore] = []
    for fin, market_cap, price in candidates:
        pe = pe_ratio(price, fin)
        pb = pb_ratio(market_cap, fin)
        cr = current_ratio(fin)
        de = debt_to_equity(fin)
        if (
            pe is None
            or pb is None
            or cr is None
            or de is None
            or fin.net_income is None
        ):
            continue

        # The real Graham Number, in dollars per share, alongside the
        # composite. Ch.20 makes margin of safety the central concept
        # and this is the only figure in the Defensive path that
        # expresses it; without it the playbook's sell trigger has
        # nothing to compare against.
        eps = fin.eps_diluted if fin.eps_diluted is not None else fin.eps_basic
        bvps = (
            fin.total_equity / fin.shares_outstanding
            if fin.total_equity is not None
            and fin.shares_outstanding
            and fin.shares_outstanding > 0
            else None
        )
        gn = (
            graham_number(eps, bvps)
            if eps is not None and bvps is not None
            else None
        )
        scores.append(
            DefensiveScore(
                ticker=fin.ticker,
                price=price,
                market_cap=market_cap,
                pe=pe,
                pb=pb,
                current_ratio=cr,
                debt_to_equity=de,
                net_income=fin.net_income,
                composite=pe * pb,
                graham_number=gn,
                margin_of_safety_pct=(
                    (gn - price) / gn * 100.0 if gn and gn > 0 else None
                ),
            )
        )
    scores.sort(key=lambda s: (s.composite, s.debt_to_equity))
    return scores


def select_top_n(scores: list, n: int) -> list:
    """Return the top ``n`` (by sort order). Take all if fewer available.

    Generic over GrahamScore and DefensiveScore.
    """
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


__all__ = [
    "DefensiveScore",
    "GrahamScore",
    "score_candidates",
    "score_defensive_candidates",
    "select_top_n",
]
