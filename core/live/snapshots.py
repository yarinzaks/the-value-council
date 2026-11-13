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

    def to_dict(self) -> dict:
        return asdict(self)


def make_snapshot(
    portfolio: LivePortfolio,
    *,
    as_of: date,
    trades: Iterable[TradeRecord] = (),
    fundamentals_newest_filed: str | None = None,
    fundamentals_stale: bool | None = None,
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
    """
    agent_dir = root / snap.agent
    agent_dir.mkdir(parents=True, exist_ok=True)
    path = agent_dir / f"{snap.date}.json"

    keeps_trades = not snap.trade_count
    keeps_freshness = snap.fundamentals_stale is None
    if (keeps_trades or keeps_freshness) and path.exists():
        try:
            prior = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"{snap.agent}@{snap.date}: unreadable snapshot: {exc}")
        else:
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
            out.append(DailySnapshot(**data))
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
