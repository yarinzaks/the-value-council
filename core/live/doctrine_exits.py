"""Each doctrine's own keep-or-sell verdict on the names it holds.

Why this exists (audit SF-X1)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every playbook has an exit section — Graham sells at the Graham
Number, Schloss at a ~50% gain, Buffett almost never — and none of it
ran. The one sale the engine made was "the name left today's target
list" after the 30-day floor, so all 823 EXIT rows in the journal gave
that reason and none carried an ``exit_trigger``.

This module is the shared vocabulary for fixing that, one agent at a
time:

* a strategy says, for each held name, keep or sell and *why* —
  :class:`ExitVerdict`, published on the strategy as
  ``last_exit_decisions`` after ``select``;
* the runner journals every verdict beside what it actually did, and
  writes the doctrine's reason onto the EXIT row when there is one;
* a per-agent :data:`ExitMode` decides whether the verdict only
  watches (``"shadow"``) or governs the held name (``"enforce"``).

Shadow is the default for every agent, and in shadow trading is
unchanged to the share. A doctrine's exit rule has never traded; the
first weeks of its verdicts are evidence for the owner to read against
what the engine did, not instructions to a $10,000 book.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, cast

from core.logger import get_logger

logger = get_logger("core.live.doctrine_exits")

ExitMode = Literal["shadow", "enforce"]

#: Every mode there is. Anything else is refused rather than read as
#: one of these.
EXIT_MODES: tuple[str, ...] = ("shadow", "enforce")

DEFAULT_EXIT_MODE: ExitMode = "shadow"

#: Agents whose doctrine exits govern the book, keyed by agent slug.
#:
#: Empty on purpose: every agent runs in shadow until the owner has
#: read its verdicts beside its trades and flips it here, by name, one
#: agent at a time — e.g. ``{"peter_lynch": "enforce"}``.
DOCTRINE_EXIT_MODES: Mapping[str, ExitMode] = MappingProxyType({})


@dataclass(frozen=True)
class ExitVerdict:
    """What a doctrine wants done with one name it holds, and why.

    ``reason`` is the doctrine's own rule in its own words — "P/E 21.0
    above the market's 18.0", "no longer passes the quality gates" —
    because that string is what reaches the journal. A verdict with no
    reason is refused: the whole point is that a sale can say why.
    """

    ticker: str
    sell: bool
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.ticker, str) or not self.ticker.strip():
            raise ValueError("an exit verdict needs a ticker")
        if not isinstance(self.sell, bool):
            raise TypeError(
                f"exit verdict for {self.ticker}: sell must be a bool, "
                f"got {type(self.sell).__name__}"
            )
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError(f"exit verdict for {self.ticker} needs a reason")


def parse_exit_mode(value: str) -> ExitMode:
    """``value`` as an :data:`ExitMode`, or ``ValueError`` naming the valid ones."""
    if value not in EXIT_MODES:
        raise ValueError(
            f"unknown exit mode {value!r}; expected one of {', '.join(EXIT_MODES)}"
        )
    return cast(ExitMode, value)


def exit_mode_for(agent: str) -> ExitMode:
    """The mode an agent runs its doctrine exits in."""
    return parse_exit_mode(DOCTRINE_EXIT_MODES.get(agent, DEFAULT_EXIT_MODE))


def guarded_verdicts(
    agent: str, compute: Callable[[], list[ExitVerdict]]
) -> list[ExitVerdict]:
    """``compute()``, unless it raises while ``agent``'s exits are in shadow.

    Each strategy computes its verdicts inside ``select``, so an
    exception there would take the whole day's selection with it. In
    shadow the verdicts only journal, so the error is logged at ERROR
    with the agent and the traceback, the day gets no verdicts, and
    trading proceeds. An enforcing agent re-raises: there the verdict
    trades, and a day without it is not a day to trade on.

    The mode is the configured one (:func:`exit_mode_for`); a strategy
    does not see an adapter constructed with an explicit override.
    """
    try:
        return compute()
    except Exception:
        if exit_mode_for(agent) == "enforce":
            raise
        logger.opt(exception=True).error(
            f"{agent}: shadow exit verdicts failed; none journaled today, "
            "trading proceeds"
        )
        return []


__all__ = [
    "DEFAULT_EXIT_MODE",
    "DOCTRINE_EXIT_MODES",
    "EXIT_MODES",
    "ExitMode",
    "ExitVerdict",
    "exit_mode_for",
    "guarded_verdicts",
    "parse_exit_mode",
]
