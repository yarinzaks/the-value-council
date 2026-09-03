"""Every executed trade, kept — including what it actually made or lost.

``TradeRecord.realized_pnl_usd`` was computed at the moment of every
sale and then thrown away. The daily snapshot kept the ticker and
nothing else, so a book could report +24.55% while every open position
sat at a loss and no file anywhere could say which sale produced the
gain. Asked exactly that, the dashboard assistant could only answer that
it did not know, which was true and was the bug.

What reconstruction can and cannot do
-------------------------------------

The books are committed daily, so a diff of consecutive commits recovers
some of it. On Graham it recovered $1,241 of a realized $3,239 — 38%.
The rest is unattributable: a name bought and sold between two commits
leaves no trace, and a position closed and reopened the same day looks
untouched. The total is knowable from the accounting identity; the
attribution is not. That gap is the whole argument for writing the
ledger at execution rather than inferring it afterwards.

One file per agent per day, replaced rather than appended on a re-run —
the same rule the snapshot uses, and for the same reason: the morning
scan is the run that trades, and a second scan of the same day is a
correction, not more history.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from core.live.portfolio import LivePortfolio, TradeRecord
from core.logger import get_logger

logger = get_logger("core.live.trade_ledger")


@dataclass(frozen=True)
class LedgerEntry:
    """One executed trade, as it was executed.

    ``realized_pnl_usd`` is zero on a buy and is the whole point on a
    sell: it is the only place the number survives.
    """

    date: str
    agent: str
    ticker: str
    side: str
    shares: float
    price: float
    gross_value: float
    cost_paid: float
    realized_pnl_usd: float
    #: How this entry came to exist.
    #:
    #: ``executed`` was written by the runner at the moment of the
    #: trade and is exact. ``reconstructed`` was inferred afterwards
    #: by diffing committed books, and is not: a name bought and
    #: sold between two commits leaves no trace, and the exit is
    #: valued at the last mark the book carried rather than at the
    #: price the sale actually got. The two must stay distinguishable
    #: on the page as well as in the file — a reconstruction shown as
    #: a record is worse than no reconstruction.
    source: str = "executed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "agent": self.agent,
            "ticker": self.ticker,
            "side": self.side,
            "shares": round(self.shares, 6),
            "price": round(self.price, 4),
            "gross_value": round(self.gross_value, 2),
            "cost_paid": round(self.cost_paid, 4),
            "realized_pnl_usd": round(self.realized_pnl_usd, 2),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LedgerEntry:
        return cls(
            date=str(data["date"]),
            agent=str(data["agent"]),
            ticker=str(data["ticker"]).upper(),
            side=str(data["side"]).upper(),
            shares=float(data["shares"]),
            price=float(data["price"]),
            gross_value=float(data.get("gross_value", 0.0)),
            cost_paid=float(data.get("cost_paid", 0.0)),
            realized_pnl_usd=float(data.get("realized_pnl_usd", 0.0)),
            source=str(data.get("source", "executed")),
        )


def record_day(
    agent: str,
    as_of: date,
    trades: Iterable[TradeRecord],
    *,
    directory: Path,
) -> Path | None:
    """Write one day's executed trades. ``None`` when there were none.

    A day with no trades writes no file rather than an empty one, so the
    ledger's directory listing is the list of days this agent actually
    traded — which is the question most often asked of it.
    """
    rows = [
        LedgerEntry(
            date=as_of.isoformat(),
            agent=agent,
            ticker=t.ticker,
            side=t.side,
            shares=t.shares,
            price=t.price,
            gross_value=t.gross_value,
            cost_paid=t.cost_paid,
            realized_pnl_usd=t.realized_pnl_usd,
        )
        for t in trades
    ]
    if not rows:
        return None

    folder = directory / agent
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{as_of.isoformat()}.json"
    path.write_text(json.dumps([r.to_dict() for r in rows], indent=1))
    realized = sum(r.realized_pnl_usd for r in rows)
    logger.info(
        f"{as_of}: ledger wrote {len(rows)} trade(s) for {agent}, "
        f"realized {realized:+,.2f}"
    )
    return path


def read_ledger(agent: str, *, directory: Path) -> list[LedgerEntry]:
    """Every recorded trade for ``agent``, oldest first.

    An unreadable day is skipped with a warning. Losing one day of
    attribution is better than being unable to report any of it, and the
    totals a caller derives from the accounting identity are unaffected
    either way — only the naming degrades.
    """
    folder = directory / agent
    if not folder.is_dir():
        return []
    out: list[LedgerEntry] = []
    for path in sorted(folder.glob("*.json")):
        try:
            rows = json.loads(path.read_text())
            out.extend(LedgerEntry.from_dict(r) for r in rows)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(f"skipping unreadable ledger day {path.name}: {exc}")
    return out


def realized_by_ticker(entries: Sequence[LedgerEntry]) -> dict[str, float]:
    """Realized P&L per name, best first.

    Buys carry zero, so they neither add nor hide anything; a name that
    was bought and never sold simply does not appear.
    """
    totals: dict[str, float] = {}
    for e in entries:
        if e.realized_pnl_usd:
            totals[e.ticker] = totals.get(e.ticker, 0.0) + e.realized_pnl_usd
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


@dataclass(frozen=True)
class ReturnBreakdown:
    """Where a book's return came from, in parts that sum to its NAV.

    The parts are what makes a return legible. Graham stood at +24.55%
    with four of his five open positions losing money, and the two facts
    look contradictory until the return is split: the open book was down
    $704 and closed trades had made $3,239.

    ``realized`` is derived from the accounting identity rather than
    from the ledger, so it is exact whatever the ledger happens to hold.
    ``attributed`` is what the ledger can put a name to, and
    ``unattributed`` is the difference — reported rather than hidden,
    because a breakdown that quietly dropped the remainder would be
    worse than one that admits it. On a book that has run since before
    the ledger existed the remainder is large, and it shrinks to zero on
    its own as recorded days accumulate.
    """

    initial_cash: float
    realized: float
    unrealized: float
    dividends: float
    costs: float
    nav: float
    attributed: dict[str, float]
    unattributed: float

    @property
    def attributed_total(self) -> float:
        return sum(self.attributed.values())

    @property
    def return_pct(self) -> float:
        if self.initial_cash <= 0:
            return 0.0
        return (self.nav / self.initial_cash - 1.0) * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_cash": round(self.initial_cash, 2),
            "realized": round(self.realized, 2),
            "unrealized": round(self.unrealized, 2),
            "dividends": round(self.dividends, 2),
            "costs": round(self.costs, 2),
            "nav": round(self.nav, 2),
            "return_pct": round(self.return_pct, 4),
            "attributed": {k: round(v, 2) for k, v in self.attributed.items()},
            "attributed_total": round(self.attributed_total, 2),
            "unattributed": round(self.unattributed, 2),
        }


def decompose_return(
    portfolio: LivePortfolio, entries: Sequence[LedgerEntry] = ()
) -> ReturnBreakdown:
    """Split a book's return into parts that add back up to its NAV.

    The identity is::

        NAV = initial + realized + unrealized + dividends - costs

    Every term but ``realized`` is readable off the book, so realized is
    solved for rather than summed. That ordering matters: summing the
    ledger instead would make the breakdown silently wrong by exactly
    the amount of history the ledger is missing, and the parts would
    stop adding up to a NAV the reader can see for themselves.
    """
    cost_basis = sum(p.shares * p.entry_price for p in portfolio.positions)
    unrealized = portfolio.invested - cost_basis
    realized = (
        portfolio.total_nav
        - portfolio.initial_cash
        - unrealized
        - portfolio.cumulative_dividends
        + portfolio.cumulative_costs
    )
    attributed = realized_by_ticker(entries)
    return ReturnBreakdown(
        initial_cash=portfolio.initial_cash,
        realized=realized,
        unrealized=unrealized,
        dividends=portfolio.cumulative_dividends,
        costs=portfolio.cumulative_costs,
        nav=portfolio.total_nav,
        attributed=attributed,
        unattributed=realized - sum(attributed.values()),
    )
