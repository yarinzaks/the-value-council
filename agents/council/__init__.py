"""The Council — the deterministic half of a doctrine-driven agent.

This is not an eleventh screen. The other agents in this project answer
to a formula: rank the market, take the top N, weight them, rebalance.
The Council answers to a written doctrine whose central claims are that
the edge is in reading what nobody reads, that concentration is where
the returns are, and that doing nothing for long stretches is not the
cost of the strategy but the strategy itself.

What is here, and what is deliberately not
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Part 7 of the doctrine splits the agent's work into run types, and what
it may do depends on which one it is in. Two of them need no model:

    regime    the four-series risk dial, from FRED, no key
    limits    Part 4's hard limits, checked every run
    journal   theses written before the outcome, and the punch card
    events    8-K item codes and delisting forms on what is held
    runs      heartbeat and close, wired from the above

The reading and council runs are the other half. They require a model to
read a 10-K, diff its risk factors against three years earlier, and
argue six seats against each other. They are not implemented here, and
pretending otherwise by stubbing them would be worse than their absence.

The run types above do not trade — heartbeat and close are forbidden
from opening a position by the doctrine's own cadence table. The agent
itself does: :class:`~agents.council.strategy.MohnishPabrai` is
registered as the twelfth live adapter and executes on the same rails as
the other eleven, autonomously.

That is not a departure from the doctrine, it is its scoping. Part 10
puts a human in front of **real money**; this is a paper book, and the
journal rather than an approval step is what makes the record auditable.
"""

from agents.council.events import Event, Severity, scan
from agents.council.journal import (
    PUNCH_CARD_TOTAL,
    Classification,
    Journal,
    JournalError,
    KillCriterion,
    Outcome,
    Thesis,
    calibrate,
    shrink,
)
from agents.council.limits import LimitCheck, LimitState, check_all
from agents.council.regime import Regime, Signal, Stance, read_regime
from agents.council.runs import AGENT_SLUG, Book, close, heartbeat

__all__ = [
    "AGENT_SLUG",
    "PUNCH_CARD_TOTAL",
    "Book",
    "Classification",
    "Event",
    "Journal",
    "JournalError",
    "KillCriterion",
    "LimitCheck",
    "LimitState",
    "Outcome",
    "Regime",
    "Severity",
    "Signal",
    "Stance",
    "Thesis",
    "calibrate",
    "check_all",
    "close",
    "heartbeat",
    "read_regime",
    "scan",
    "shrink",
]
