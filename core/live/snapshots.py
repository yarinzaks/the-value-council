"""Daily portfolio snapshots — point-in-time records of state per agent.

The dashboard's History tab and Today's-activity section both need a
deterministic record of "what did the portfolio look like at the close
of day X". Live ``portfolio.json`` only stores the *current* state, so
we persist a snapshot at the end of every daily run.

Snapshot schema (``data/snapshots/<agent>/<YYYY-MM-DD>.json``)::

    {
      "agent": "...",
      "date": "2026-05-05",
      "nav": 10120.34,
      "cash": 857.49,
      "invested": 9262.85,
      "pnl_usd": 120.34,
      "pnl_pct": 1.20,
      "position_count": 29,
      "watchlist_count": 30,
      "buys": ["AAPL", ...],
      "sells": ["XYZ", ...],
      "trade_count": 2,
      "fundamentals_newest_filed": "2026-11-12",
      "fundamentals_stale": false
    }

Backfill: ``backfill_from_decisions`` derives a snapshot from the
existing decision log + the portfolio's last_updated timestamp by
treating every BUY decision on a given day as a "trade today" entry.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path

from core.day_files import canonical_day_files
from core.live.portfolio import LivePortfolio, TradeRecord
from core.logger import get_logger
from core.paths import DATA_ROOT

logger = get_logger("core.live.snapshots")

SNAPSHOTS_DIR: Path = DATA_ROOT / "snapshots"


@dataclass
class DailySnapshot:
    """One day's portfolio state for one agent."""

    agent: str
    date: str  # ISO YYYY-MM-DD
    nav: float
    cash: float
    invested: float
    pnl_usd: float
    pnl_pct: float
    position_count: int
    watchlist_count: int
    buys: list[str]
    sells: list[str]
    trade_count: int
    #: Cash dividends received to date. NAV already includes this; the
    #: field lets a reader separate income from price appreciation, so
    #: "total return" is auditable rather than inferred.
    dividends_received_usd: float = 0.0
    #: Filing date of the newest filing the open scan read for any
    #: universe member (audit FUND-2). None: not measured — a close
    #: mark reads no filings, and files written before the field existed.
    fundamentals_newest_filed: str | None = None
    #: True when that filing was older than the freshness limit, or the
    #: scan read no fundamentals at all. The run trades regardless; this
    #: is what tells a reader it did so on old numbers. None: not measured.
    fundamentals_stale: bool | None = None
    #: True on a file dated to a day the US market was shut (audit
    #: RK-06). The runner has declined those days since the market
    #: calendar went in; the 55 written before it are kept and marked by
    #: ``scripts.tag_closed_sessions`` rather than deleted, so a reader
    #: can leave them out without the history losing them.
    #: Tri-state on the way in: None means this save did not assess the
    #: day and keeps what the file already says; False is the runner,
    #: which asked the calendar, saying the market was open — and so it
    #: clears a wrong tag rather than leaving it stuck forever.
    closed_session: bool | None = None
    #: Where a snapshot recovered after the fact came from, when it was
    #: not written by its own day's run — 2026-08-06 was filed as
    #: 2026-08-07 by a run that crossed midnight and was then
    #: overwritten, and is restored from that commit.
    restored_from: str | None = None

    def to_dict(self) -> dict:
        out = asdict(self)
        # Written only when they say something, so an ordinary day's
        # file is what it was before the two markers existed.
        if not self.closed_session:
            del out["closed_session"]
        if self.restored_from is None:
            del out["restored_from"]
        return out


def make_snapshot(
    portfolio: LivePortfolio,
    *,
    as_of: date,
    trades: Iterable[TradeRecord] = (),
    fundamentals_newest_filed: str | None = None,
    fundamentals_stale: bool | None = None,
    closed_session: bool | None = None,
) -> DailySnapshot:
    buys = [t.ticker for t in trades if t.side == "BUY"]
    sells = [t.ticker for t in trades if t.side == "SELL"]
    return DailySnapshot(
        agent=portfolio.agent,
        date=as_of.isoformat(),
        nav=round(portfolio.total_nav, 2),
        cash=round(portfolio.cash, 2),
        invested=round(portfolio.invested, 2),
        pnl_usd=round(portfolio.total_nav - portfolio.initial_cash, 2),
        pnl_pct=round(portfolio.cumulative_return_pct, 4),
        position_count=len(portfolio.positions),
        watchlist_count=len(portfolio.watchlist),
        buys=buys,
        sells=sells,
        trade_count=len(buys) + len(sells),
        dividends_received_usd=round(portfolio.cumulative_dividends, 2),
        fundamentals_newest_filed=fundamentals_newest_filed,
        fundamentals_stale=fundamentals_stale,
        closed_session=closed_session,
    )


def save_snapshot(snap: DailySnapshot, *, root: Path = SNAPSHOTS_DIR) -> Path:
    """Write one day's snapshot, preserving trades already recorded.

    Two runs write the same file each day. The morning scan executes
    the rotations and passes them in; the close-of-day mark re-values
    the book and passes ``trades=[]`` because it does not trade. This
    used to be an unconditional overwrite, so the close run erased the
    morning's record every single day — 68 of 68 snapshots held zero
    trades for a portfolio that plainly had been traded, and the
    dashboard had no transaction history to show at all.

    So a save that carries no trades keeps whatever the day already
    had. Valuation fields still update, which is the close run's actual
    job. A save that does carry trades replaces them, so a re-run of
    the morning scan corrects rather than appends.

    The fundamentals-freshness fields follow the same rule: only the
    open scan reads filings, so a save that did not measure them
    (``fundamentals_stale is None``) keeps what the day already had.

    The ``closed_session`` and ``restored_from`` markers describe the
    day, not the run; ``to_dict`` drops them when unset. A save whose
    ``closed_session`` is None did not assess the day and keeps a True
    from the prior file — unless it carries trades, which a shut market
    cannot have produced, so the tag is dropped with a warning. An
    explicit False (the runner, which consulted the calendar) clears
    it. A string ``restored_from`` is kept; anything else is not one.

    A prior file that is not a JSON object is warned about and
    overwritten, as an unreadable one is.
    """
    agent_dir = root / snap.agent
    agent_dir.mkdir(parents=True, exist_ok=True)
    path = agent_dir / f"{snap.date}.json"

    keeps_trades = not snap.trade_count
    # This save's own trades, before any are carried over from the file:
    # a tagged holiday whose old trades are kept is not a new contradiction.
    traded_now = snap.trade_count
    keeps_freshness = snap.fundamentals_stale is None
    if path.exists():
        try:
            prior = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"{snap.agent}@{snap.date}: unreadable snapshot: {exc}")
            prior = None
        if prior is not None and not isinstance(prior, dict):
            logger.warning(
                f"{snap.agent}@{snap.date}: prior snapshot is not a JSON "
                f"object ({type(prior).__name__}); overwriting it"
            )
            prior = None
        if isinstance(prior, dict):
            if keeps_trades and prior.get("trade_count"):
                snap = replace(
                    snap,
                    buys=list(prior.get("buys") or []),
                    sells=list(prior.get("sells") or []),
                    trade_count=int(prior["trade_count"]),
                )
            if keeps_freshness and prior.get("fundamentals_stale") is not None:
                snap = replace(
                    snap,
                    fundamentals_newest_filed=prior.get("fundamentals_newest_filed"),
                    fundamentals_stale=bool(prior["fundamentals_stale"]),
                )
            if prior.get("closed_session") is True and snap.closed_session is None:
                if traded_now:
                    logger.warning(
                        f"{snap.agent}@{snap.date}: not carrying closed_session "
                        f"onto a save with {traded_now} trade(s) — a shut "
                        f"market cannot have produced them"
                    )
                else:
                    snap = replace(snap, closed_session=True)
            restored = prior.get("restored_from")
            if isinstance(restored, str) and snap.restored_from is None:
                snap = replace(snap, restored_from=restored)

    path.write_text(json.dumps(snap.to_dict(), indent=2))
    return path


def load_snapshots(
    agent: str, *, root: Path = SNAPSHOTS_DIR
) -> list[DailySnapshot]:
    """Load all snapshots for an agent, sorted by date ascending."""
    agent_dir = root / agent
    if not agent_dir.exists():
        return []
    out: list[DailySnapshot] = []
    # One file per day: a "<day> 2.json" sync copy is not a second day.
    for f in canonical_day_files(agent_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            snap = DailySnapshot(**data)
            # A file without the key was never tagged: read it as False,
            # as before the field became tri-state for writers.
            if snap.closed_session is None:
                snap = replace(snap, closed_session=False)
            out.append(snap)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning(f"failed to load snapshot {f}: {exc}")
    return out


def latest_snapshot(
    agent: str, *, root: Path = SNAPSHOTS_DIR
) -> DailySnapshot | None:
    snaps = load_snapshots(agent, root=root)
    return snaps[-1] if snaps else None


__all__ = [
    "SNAPSHOTS_DIR",
    "DailySnapshot",
    "latest_snapshot",
    "load_snapshots",
    "make_snapshot",
    "save_snapshot",
]
