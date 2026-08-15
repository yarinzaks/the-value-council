"""The Council as a Strategy, so it trades on the same rails as the rest.

``selection.propose`` decides; this hands that decision to the runner in
the shape every other agent uses. Going through ``Strategy`` rather than
a bespoke path in scripts/run_council.py is the whole point: the same
execution, the same cost model, the same marks, the same snapshots and
the same rebalancing band as the eleven. An agent with its own execution
path would be incomparable to them no matter what the dashboard showed.

Where the inputs come from
--------------------------

``Strategy.select`` is handed a universe, prices and fundamentals — not
the other agents' books, which is what this one needs. It reads them off
disk, from the portfolios the runner has already published. That means
it acts on the roster as of the previous close rather than the current
one, which is deliberate: depending on within-run ordering would make
the Council's decision a function of which agent happened to run first.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from agents.council.selection import Proposal, propose
from core.backtest.strategy_runner import HeldPosition, Strategy
from core.logger import get_logger
from core.paths import portfolios_dir

logger = get_logger("agents.council.strategy")

AGENT_SLUG = "mohnish_pabrai"

#: Entries already spent, derived from the journal rather than stored.
PUNCH_CARD_TOTAL = 20


def read_books(directory: Path | None = None) -> dict[str, list[str]]:
    """Every agent's holdings, keyed by slug.

    A portfolio that cannot be read is skipped rather than raised on: a
    corrupt or half-written file should cost that agent its vote, not
    stop the Council from running.
    """
    directory = directory or portfolios_dir()
    books: dict[str, list[str]] = {}
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError as exc:
        logger.warning(f"cannot list {directory} — {exc}")
        return books

    for path in paths:
        try:
            data = json.loads(path.read_text())
            books[data.get("agent", path.stem)] = [
                p["ticker"] for p in data.get("positions", [])
            ]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(f"skipping {path.name} — {type(exc).__name__}: {exc}")
    return books


class MohnishPabrai(Strategy):
    """Buys what its members agree on, subject to its own vetoes."""

    name = AGENT_SLUG

    def __init__(
        self,
        *,
        news_service: Any | None = None,
        regime_reader=None,
        filings_reader=None,
        books_reader=read_books,
        entries_used: int = 0,
    ) -> None:
        """
        Args:
            news_service: Anything with ``news_for(ticker, as_of)``.
                ``None`` skips the news gate rather than blocking every
                entry — an unconfigured feed must not look like a veto.
            regime_reader: ``(as_of) -> Regime``. Injected so tests and
                offline runs need no FRED call.
            filings_reader: ``(tickers, as_of) -> {ticker: reason}``.
            books_reader: ``() -> {slug: [ticker]}``.
            entries_used: Punches already spent in this agent's life.
        """
        self.news_service = news_service
        self.regime_reader = regime_reader
        self.filings_reader = filings_reader
        self.books_reader = books_reader
        self.entries_used = entries_used
        self.last_proposal: Proposal | None = None

    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: Any,
        fundamentals: Any,
        *,
        held: Mapping[str, HeldPosition] | None = None,
    ) -> dict[str, float]:
        books = self.books_reader()
        holding = sorted(held or {})

        risk_on = None
        if self.regime_reader is not None:
            try:
                risk_on = self.regime_reader(as_of).risk_on_count
            except Exception as exc:
                # Left as None, which blocks entries. An unreadable
                # regime is not a green one.
                logger.warning(f"regime unreadable — {type(exc).__name__}: {exc}")

        flagged: dict[str, str] = {}
        if self.filings_reader is not None:
            watched = sorted(set(holding) | set(books.get("__candidates__", [])))
            try:
                flagged = self.filings_reader(watched or holding, as_of)
            except Exception as exc:
                logger.warning(f"filings unreadable — {type(exc).__name__}: {exc}")

        news_for = None
        if self.news_service is not None:
            def news_for(ticker: str, when: date):
                try:
                    return self.news_service.news_for(ticker, when)
                except Exception as exc:
                    # A source failure must not read as "no bad news".
                    # It reads as no news, which is what an unconfigured
                    # feed reads as too — the gate is a veto, not a
                    # requirement, and this is stated in the run log.
                    logger.warning(f"news unavailable for {ticker} — {exc}")
                    return []

        proposal = propose(
            as_of=as_of,
            books=books,
            held=holding,
            risk_on_dials=risk_on,
            entries_remaining=max(0, PUNCH_CARD_TOTAL - self.entries_used),
            filings_flagged=flagged,
            news_for=news_for,
        )
        self.last_proposal = proposal
        # Universe is respected even though agreement already implies
        # it: a name every agent held yesterday can be suspended today,
        # and the roster is the runner's answer on what is tradeable.
        tradeable = {t.upper() for t in universe}
        return {
            t: w
            for t, w in proposal.weights.items()
            if not tradeable or t.upper() in tradeable
        }
