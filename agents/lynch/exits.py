"""When Lynch sells — and, more importantly, when he does not.

The problem this fixes
~~~~~~~~~~~~~~~~~~~~~~

``PeterLynch.select`` re-ranked the whole universe by PEG every
rebalance and kept the best N. Anything that fell out of the top N was
sold, and the runner had no way to know a position was already owned:
``select`` accepted a ``held`` mapping and never read it.

That produces the single behaviour Lynch names most often as the
amateur's cardinal error. A stock that goes up gets a higher P/E, a
higher PEG, and a worse rank — so success itself is the sell trigger.
The better a position does, the faster the strategy disposes of it.

    "Selling your winners and holding your losers is like cutting the
    flowers and watering the weeds."

A tenbagger requires holding through roughly ten doublings of the
multiple's numerator. Under rank-slippage exits it cannot survive the
first one.

What replaces it
~~~~~~~~~~~~~~~~

Two rules, both from the playbook, and no invented thresholds.

**Retention is asymmetric with entry.** Entry is PEG ≤ 1.0 per
category (``ranking._max_peg_for``); exit is PEG > 2.0, the top of
:data:`agents.lynch.peg.PEG_HOLD`'s band. A name bought at 0.8 that
runs to 1.3 is expensive for a *new* purchase and is not a reason to
sell one you already own. Buying and holding are different decisions
and Lynch's own zones already say so — the code just never used the
hold zone for anything.

**Stalwarts are the exception, and Lynch states it as a number.** He
does not hold them for tenbaggers; he takes 30-50% and rotates into
another stalwart that has not moved yet. So a stalwart up
:data:`STALWART_PROFIT_TAKE_PCT` is sold on purpose, which is not the
same thing as being sold for slipping in a ranking.

Everything else — a cyclical's position in its cycle, whether a
turnaround has actually turned, whether an asset play's asset has been
recognised — is a judgement the quant pipeline cannot make. Those keep
the universal PEG ceiling here, and the live LLM classifier
(:mod:`agents.lynch.category_classifier`) can still issue an explicit
SELL, which it always could.

A held name that no longer appears in the scored set at all has failed
the quality gates outright. That is a thesis break rather than a
ranking wobble, and it is sold.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from core.backtest.strategy_runner import HeldPosition
from core.logger import get_logger

from .peg import PEG_HOLD
from .ranking import LynchScore

logger = get_logger("agents.lynch.exits")


#: PEG above which an existing position is let go. The top of Lynch's
#: "hold" band — above it the zone function already reads "avoid", and
#: an avoid is a sell for something you own.
DEFAULT_MAX_HELD_PEG: float = PEG_HOLD

#: Gain at which a Stalwart is sold and the proceeds rotated. Lynch's
#: stated band is 30-50%; the low end is used because the alternative
#: to taking it is not holding for a tenbagger — Stalwarts do not
#: produce them — but waiting for a smaller further gain at full risk.
STALWART_PROFIT_TAKE_PCT: float = 30.0


@dataclass(frozen=True)
class ExitDecision:
    """Why one held position was kept or let go."""

    ticker: str
    retained: bool
    reason: str


def _relevant_peg(score: LynchScore) -> float:
    """PEGY for Slow Growers, PEG for everyone else — the same choice
    :func:`agents.lynch.ranking.score_candidates` makes when sorting."""
    return score.pegy if score.lynch_category == "Slow Grower" else score.peg


def decide(
    score: LynchScore | None,
    position: HeldPosition,
    *,
    max_held_peg: float = DEFAULT_MAX_HELD_PEG,
    stalwart_profit_take_pct: float = STALWART_PROFIT_TAKE_PCT,
) -> ExitDecision:
    """Keep or sell one position already owned.

    ``score`` is ``None`` when the name no longer survives the quality
    gates — sold, because that is the thesis breaking rather than the
    multiple moving.
    """
    if score is None:
        return ExitDecision(
            position.ticker,
            False,
            "no longer passes the quality gates",
        )

    if score.lynch_category == "Stalwart":
        ret = position.return_pct
        if ret is not None and ret >= stalwart_profit_take_pct:
            return ExitDecision(
                position.ticker,
                False,
                f"Stalwart up {ret:.0f}% — take the gain and rotate",
            )

    peg = _relevant_peg(score)
    if peg > max_held_peg:
        return ExitDecision(
            position.ticker,
            False,
            f"PEG {peg:.2f} above the {max_held_peg:.1f} hold ceiling",
        )

    return ExitDecision(
        position.ticker,
        True,
        f"{score.lynch_category} at PEG {peg:.2f} — still within the story",
    )


def retained(
    scores: list[LynchScore],
    held: Mapping[str, HeldPosition] | None,
    *,
    max_held_peg: float = DEFAULT_MAX_HELD_PEG,
    stalwart_profit_take_pct: float = STALWART_PROFIT_TAKE_PCT,
) -> tuple[list[LynchScore], list[ExitDecision]]:
    """Positions worth keeping, plus the reasoning for every held name.

    Returns the retained names as scores so the caller can weight them
    the same way it weights new buys.
    """
    if not held:
        return [], []

    by_ticker = {s.ticker: s for s in scores}
    keep: list[LynchScore] = []
    decisions: list[ExitDecision] = []
    for ticker, position in held.items():
        score = by_ticker.get(ticker)
        d = decide(
            score,
            position,
            max_held_peg=max_held_peg,
            stalwart_profit_take_pct=stalwart_profit_take_pct,
        )
        decisions.append(d)
        if d.retained and score is not None:
            keep.append(score)

    if decisions:
        sold = [d for d in decisions if not d.retained]
        logger.info(
            f"held {len(decisions)}: retained {len(keep)}, "
            f"exiting {len(sold)}"
        )
        for d in sold:
            logger.debug(f"  exit {d.ticker}: {d.reason}")
    return keep, decisions


__all__ = [
    "DEFAULT_MAX_HELD_PEG",
    "STALWART_PROFIT_TAKE_PCT",
    "ExitDecision",
    "decide",
    "retained",
]
