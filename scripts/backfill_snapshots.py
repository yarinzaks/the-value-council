"""Backfill daily snapshots from the existing decision logs.

For every day a decision file exists, derive a snapshot:

* ``buys`` = unique tickers with decision==BUY that day
* ``sells`` = unique tickers with decision==SELL that day
* ``trade_count`` = buys + sells
* For NAV / cash / position_count we use the *current* portfolio state
  for the most recent date (true historical NAV per day requires
  walking trade history; that's a future enhancement). For older
  dates we approximate with the seed amount + zero P&L. This is
  honest and clearly documented in the snapshot file.

Usage::

    .venv/bin/python -m scripts.backfill_snapshots
"""

from __future__ import annotations

import json
import sys
from datetime import date

from core.live.portfolio import LivePortfolio
from core.live.snapshots import DailySnapshot, save_snapshot
from core.paths import decisions_dir, portfolios_dir


def main() -> int:
    dec_root = decisions_dir()
    if not dec_root.exists():
        print(f"no decisions dir at {dec_root}; nothing to backfill")
        return 0

    written = 0
    for agent_dir in sorted(dec_root.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent = agent_dir.name
        portfolio = LivePortfolio.load_or_seed(agent, directory=portfolios_dir())

        # Walk every per-day decision file for this agent.
        for f in sorted(agent_dir.glob("*.json")):
            day_str = f.stem
            try:
                day = date.fromisoformat(day_str)
            except ValueError:
                continue

            try:
                rows = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue

            buys: list[str] = []
            sells: list[str] = []
            for r in rows:
                t = r.get("ticker")
                d = r.get("decision")
                if not t:
                    continue
                if d == "BUY" and t not in buys:
                    buys.append(t)
                elif d == "SELL" and t not in sells:
                    sells.append(t)

            # For the most recent day (the portfolio's last_updated date),
            # use the actual current state. Older days fall back to the
            # seed amount and zero-pnl — clearly an approximation.
            last_updated_day: date | None = None
            if portfolio.last_updated:
                try:
                    last_updated_day = date.fromisoformat(
                        portfolio.last_updated.split("T", 1)[0]
                    )
                except ValueError:
                    last_updated_day = None

            if last_updated_day is not None and day == last_updated_day:
                snap = DailySnapshot(
                    agent=agent,
                    date=day_str,
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
                )
            else:
                snap = DailySnapshot(
                    agent=agent,
                    date=day_str,
                    nav=portfolio.initial_cash,
                    cash=portfolio.initial_cash,
                    invested=0.0,
                    pnl_usd=0.0,
                    pnl_pct=0.0,
                    position_count=0,
                    watchlist_count=0,
                    buys=buys,
                    sells=sells,
                    trade_count=len(buys) + len(sells),
                )

            save_snapshot(snap)
            written += 1

    print(f"backfilled {written} snapshots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
