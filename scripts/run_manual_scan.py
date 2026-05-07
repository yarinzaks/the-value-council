"""One-shot manual scan across all 10 council agents.

Runs the live ``DailyRunner`` once for the as-of date supplied (default:
today), then prints a per-agent summary of the buys executed today.
This is the same code path the cron job uses — only the entry point
differs.

Usage::

    .venv/bin/python -m scripts.run_manual_scan          # today
    .venv/bin/python -m scripts.run_manual_scan 2026-05-07
"""

from __future__ import annotations

import sys
from datetime import date, datetime

from core.live.runner import DailyRunner


def _parse_as_of(argv: list[str]) -> date | None:
    if len(argv) <= 1:
        return None
    try:
        return datetime.strptime(argv[1], "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"bad date {argv[1]!r}; expected YYYY-MM-DD: {exc}")


def main(argv: list[str] | None = None) -> None:
    argv = argv or sys.argv
    as_of = _parse_as_of(argv)
    runner = DailyRunner(market="US")
    results = runner.run(as_of=as_of)

    print()
    print("=" * 80)
    print(f"  Manual scan — {as_of or date.today()} — {len(results)} agents")
    print("=" * 80)
    for r in results:
        print(f"\n## {r.agent}")
        if r.error:
            print(f"  error: {r.error}")
            continue
        bought = [t for t in r.trades if t.side == "BUY"]
        sold = [t for t in r.trades if t.side == "SELL"]
        nav = r.portfolio.total_nav
        cash = r.portfolio.cash
        print(
            f"  NAV ${nav:,.2f}  cash ${cash:,.2f}  "
            f"positions={len(r.portfolio.positions)}  "
            f"buys={len(bought)}  sells={len(sold)}  "
            f"watchlist={len(r.watchlist)}"
        )
        if bought:
            tickers = ", ".join(sorted(t.ticker for t in bought))
            print(f"  bought today: {tickers}")
        if sold:
            tickers = ", ".join(sorted(t.ticker for t in sold))
            print(f"  sold today:   {tickers}")
    print()


if __name__ == "__main__":
    main()
