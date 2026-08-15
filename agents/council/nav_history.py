"""How far the book is from its own high-water mark.

E1's input. The circuit breaker is a rule about *this* agent's equity
curve, and until now nothing computed it on the path that trades: the
number existed only inside a heartbeat run that never opens or closes
anything, so an agent could sit thirty per cent below its peak and keep
buying.

Where the peak comes from
-------------------------

The daily snapshots, which record one NAV per agent per day and are
written by the same runs that mark the book. The peak is the highest of
those plus the portfolio's current NAV, so a book that made a new high
this morning is measured against this morning rather than against
yesterday.

Why an unreadable history blocks
--------------------------------

Because the alternative is to assume no drawdown, and an agent that
cannot read its own equity curve is exactly the one that should not be
opening positions. This is the same reasoning the regime dial already
uses: an unreadable signal tightens the book rather than loosening it.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.live.snapshots import SNAPSHOTS_DIR
from core.logger import get_logger
from core.paths import portfolios_dir

logger = get_logger("agents.council.nav_history")


def current_nav(agent: str, *, directory: Path | None = None) -> float | None:
    """The agent's NAV as its portfolio file last recorded it."""
    path = (directory or portfolios_dir()) / f"{agent}.json"
    try:
        value = json.loads(path.read_text()).get("total_nav")
    except (OSError, ValueError) as exc:
        logger.warning(f"{agent}: portfolio unreadable — {exc}")
        return None
    return None if value is None else float(value)


def peak_nav(
    agent: str,
    *,
    snapshots: Path | None = None,
    include: float | None = None,
) -> float | None:
    """The highest NAV this agent has recorded, or ``None``.

    Args:
        agent: Slug, which is also the snapshot subdirectory name.
        snapshots: Root of the snapshot tree.
        include: An additional NAV to consider — today's, which has not
            been snapshotted yet. Passing it is what keeps a book that
            just made a new high from measuring itself against
            yesterday and reporting a drawdown it does not have.

    A snapshot that cannot be parsed is skipped rather than raised on:
    one corrupt day should not blind the breaker to twenty good ones.
    """
    directory = (snapshots or SNAPSHOTS_DIR) / agent
    best: float | None = include
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError as exc:
        logger.warning(f"{agent}: cannot list snapshots — {exc}")
        paths = []

    for path in paths:
        try:
            value = json.loads(path.read_text()).get("nav")
        except (OSError, ValueError) as exc:
            logger.debug(f"{agent}: skipping {path.name} — {exc}")
            continue
        if value is None:
            continue
        nav = float(value)
        if best is None or nav > best:
            best = nav
    return best


def drawdown_from_peak(
    agent: str,
    *,
    portfolios: Path | None = None,
    snapshots: Path | None = None,
) -> float | None:
    """Fractional drawdown, negative for a loss, or ``None``.

    ``None`` means the equity curve could not be read, which
    :func:`agents.council.exits.entries_blocked` treats as a reason to
    stop opening positions.
    """
    nav = current_nav(agent, directory=portfolios)
    if nav is None:
        return None
    peak = peak_nav(agent, snapshots=snapshots, include=nav)
    if peak is None or peak <= 0:
        return None
    return (nav / peak) - 1.0


__all__ = ["current_nav", "drawdown_from_peak", "peak_nav"]
