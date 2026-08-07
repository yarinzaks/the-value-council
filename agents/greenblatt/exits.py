"""Greenblatt's holding-period rule.

Why this exists
~~~~~~~~~~~~~~~

The Little Book is explicit that the Magic Formula is two things: a
ranking, and a discipline for sitting still. The ranking selects; the
one-year hold is what lets the selection work. Greenblatt's own
explanation is that the formula underperforms often enough, and for
long enough, that anyone trading it on a shorter leash abandons it
before it pays — and his tax argument points the same way, since
selling inside twelve months converts a long-term gain into a
short-term one.

None of that was implemented. The runner sold any position that left
today's top thirty, so a name could be bought on Monday and sold on
Thursday for slipping two ranks. The live book turned over 28% in
three trading days.

What this module does
~~~~~~~~~~~~~~~~~~~~~

``retained`` answers one question — which currently-held names must
stay, whatever today's ranking says — and ``build_targets`` merges that
answer with the fresh ranking, filling the remaining slots from the top
down.

The result is a ladder rather than a wholesale rebalance. Positions
mature and are replaced individually as each passes its anniversary,
which is exactly the staggered book Greenblatt describes building over
a first year and rolling thereafter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from core.backtest.strategy_runner import HeldPosition
from core.logger import get_logger

logger = get_logger("agents.greenblatt.exits")

#: Days a position is held before it becomes eligible for replacement.
#: 365 rather than a round 360: the tax argument turns on the one-year
#: mark specifically, and selling a day early forfeits it.
DEFAULT_HOLDING_PERIOD_DAYS: int = 365


def is_mature(position: HeldPosition, as_of: date, *, days: int) -> bool:
    """True once ``position`` has been held long enough to be replaced."""
    return position.days_held_at(as_of) >= days


def retained(
    held: Mapping[str, HeldPosition],
    as_of: date,
    *,
    holding_period_days: int = DEFAULT_HOLDING_PERIOD_DAYS,
) -> list[str]:
    """Tickers that must stay in the book regardless of today's ranking.

    Ordered oldest first, so a caller that has to break a tie for the
    last slot keeps the position closest to maturing out.
    """
    immature = [
        (pos.days_held_at(as_of), ticker)
        for ticker, pos in held.items()
        if not is_mature(pos, as_of, days=holding_period_days)
    ]
    immature.sort(reverse=True)
    return [ticker for _, ticker in immature]


def build_targets(
    ranked: Sequence[str],
    held: Mapping[str, HeldPosition] | None,
    as_of: date,
    *,
    portfolio_size: int,
    holding_period_days: int = DEFAULT_HOLDING_PERIOD_DAYS,
) -> list[str]:
    """Merge the holding-period floor with today's ranking.

    ``ranked`` is today's candidates, best first. Every immature holding
    is kept; the remaining slots go to the best-ranked names not already
    in the book. A mature holding is not sold for being mature — it
    simply stops being protected, and keeps its slot if it still ranks.

    With ``held`` absent (a backtest that does not track positions, or
    an agent's first run) this is exactly the old behaviour: the top
    ``portfolio_size`` names.
    """
    if portfolio_size <= 0:
        return []
    if not held:
        return list(ranked[:portfolio_size])

    keep = retained(held, as_of, holding_period_days=holding_period_days)
    if len(keep) >= portfolio_size:
        # More protected names than slots. Keep the ones closest to
        # maturing so the ladder keeps advancing rather than freezing.
        logger.info(
            f"{as_of}: {len(keep)} immature holdings for {portfolio_size} slots — "
            f"holding the {portfolio_size} oldest"
        )
        return keep[:portfolio_size]

    out = list(keep)
    seen = set(out)
    for ticker in ranked:
        if len(out) >= portfolio_size:
            break
        if ticker in seen:
            continue
        out.append(ticker)
        seen.add(ticker)

    replaced = [t for t in held if t not in seen]
    if replaced:
        logger.info(
            f"{as_of}: {len(replaced)} matured position(s) replaced: "
            f"{', '.join(sorted(replaced))}"
        )
    return out


__all__ = [
    "DEFAULT_HOLDING_PERIOD_DAYS",
    "build_targets",
    "is_mature",
    "retained",
]
