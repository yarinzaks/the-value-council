"""What an agent is waiting for, and how far away it is.

Why this is not part of a screen
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A buy screen answers one question — may I own this today — and discards
the near miss. But a company that clears every test a price cannot
change, and fails only on price, is the single most useful thing to say
to someone following an agent before anything is bought: the method
already approves of the business, and only the quotation is in the way.
"Graham is watching X, and will buy at Y" is a statement a screen
throws away on every run.

What it costs to compute is agent-specific, because the criteria are.
What it *means* is not, so the shape lives here and each agent's own
screen fills it in. A second agent joining the watch list is then one
producer function, not a second watch list.

Nothing here knows any criteria. A candidate carries the price its own
agent would act at and the reasons as that agent phrased them, so this
module cannot disagree with a screen about anything.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

#: How many names a watch list may carry.
#:
#: Not a doctrinal number — no investor's writing says how long a watch
#: list should be. It mirrors what the buy path already does: rank the
#: survivors and fill a fixed number of slots. A list without a cap
#: stops being something a person reads.
DEFAULT_WATCH_SLOTS: int = 10


@dataclass(frozen=True)
class WatchCandidate:
    """One company an agent would buy at a lower price.

    ``trigger_price`` is below ``price`` by construction: a candidate
    whose tests already pass is not waiting for anything, it is a
    purchase, and belongs to the screen rather than here.
    """

    ticker: str
    price: float
    trigger_price: float
    #: The criteria failing now, phrased by the agent that failed them.
    blocking: tuple[str, ...] = ()
    #: What it already clears — the reason it is worth watching at all.
    passing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError(f"{self.ticker}: price must be positive")
        if self.trigger_price <= 0:
            raise ValueError(f"{self.ticker}: trigger price must be positive")
        if self.trigger_price >= self.price:
            raise ValueError(
                f"{self.ticker}: trigger price {self.trigger_price} is not "
                f"below the price {self.price} — this is a purchase, not a watch"
            )

    @property
    def required_fall_pct(self) -> float:
        """How far the price must fall, as a positive percentage."""
        return (1.0 - self.trigger_price / self.price) * 100.0


class Watched(Protocol):
    """Anything on a watch list. All the diff needs is the name."""

    @property
    def ticker(self) -> str: ...


@dataclass(frozen=True)
class WatchlistChange[WatchedT: Watched]:
    """What moved between two runs.

    The reason the store exists. A notification on every run would
    report the same six names daily and train its reader to ignore
    them; a notification on a change says something each time.
    """

    entered: tuple[WatchedT, ...] = ()
    left: tuple[str, ...] = ()
    held: tuple[WatchedT, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        return not self.entered and not self.left


def diff_watchlist[WatchedT: Watched](
    previous: Iterable[str],
    current: Iterable[WatchedT],
) -> WatchlistChange[WatchedT]:
    """Compare a stored list of tickers against a freshly computed one.

    ``previous`` is tickers rather than candidates on purpose: the
    prices in a stored candidate are yesterday's, and nothing should be
    tempted to report them as though they were today's.

    A name that leaves is reported by ticker alone. It left because it
    stopped qualifying, so whatever it stopped qualifying on is no
    longer true of it, and the figures that described it are gone with
    the reason.
    """
    before = {t.upper() for t in previous}
    now = list(current)
    seen = {c.ticker.upper() for c in now}

    entered = tuple(c for c in now if c.ticker.upper() not in before)
    held = tuple(c for c in now if c.ticker.upper() in before)
    left = tuple(sorted(before - seen))
    return WatchlistChange(entered=entered, left=left, held=held)


__all__ = [
    "DEFAULT_WATCH_SLOTS",
    "WatchCandidate",
    "Watched",
    "WatchlistChange",
    "diff_watchlist",
]
