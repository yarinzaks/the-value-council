"""Daily paper-trading entry point.

Two run modes (selected via ``--mode``):

``open`` (default — fires at market open, 09:35 ET / 16:35 IL)
    Full strategy scan: discover candidates, generate target weights,
    execute the diff vs. current portfolio (BUYs of new picks, SELLs
    of names that fell out of the target list), refresh the watchlist.

``close`` (fires at market close, 16:00 ET / 23:00 IL)
    Mark every open position to the day's closing price, refresh
    NAV / P&L / weights, write a fresh snapshot. No new BUYs are
    executed at close — the strategies are designed to act on the
    fundamentals available at open. SELLs from rotation are also
    deferred to next morning's run for tax-lot consistency.

Both modes write:

* ``data/portfolios/<agent>.json`` (updated state)
* ``data/decisions/<agent>/<YYYY-MM-DD>.json`` (BUY/SELL entries — open only)
* ``data/snapshots/<agent>/<YYYY-MM-DD>.json`` (NAV/cash/trades that day)

Usage::

    .venv/bin/python -m scripts.run_daily_paper_trading --mode open
    .venv/bin/python -m scripts.run_daily_paper_trading --mode close
    .venv/bin/python -m scripts.run_daily_paper_trading --as-of 2026-04-29
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from core.live.runner import AgentRunResult, DailyRunner
from core.logger import get_logger

logger = get_logger("scripts.run_daily_paper_trading")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run one day of paper trading.")
    p.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="Override the run date (ISO YYYY-MM-DD). Defaults to today.",
    )
    p.add_argument(
        "--mode",
        choices=("open", "close"),
        default="open",
        help=(
            "open  = full scan + buys/sells + watchlist refresh (default)."
            " close = mark-to-market + snapshot only."
        ),
    )
    return p.parse_args(argv)


def print_report(results: list[AgentRunResult]) -> None:
    print()
    print("=" * 72)
    print("VALUE COUNCIL — DAILY PAPER-TRADING REPORT")
    print("=" * 72)

    council_nav = sum(r.portfolio.total_nav for r in results)
    council_cash = sum(r.portfolio.cash for r in results)
    council_initial = sum(r.portfolio.initial_cash for r in results)
    council_return = (
        (council_nav / council_initial - 1.0) * 100.0 if council_initial > 0 else 0.0
    )
    print(
        f"Council NAV ${council_nav:,.2f}  •  cash ${council_cash:,.2f}  "
        f"•  return {council_return:+.2f}%  •  {len(results)} agents"
    )
    print("-" * 72)

    for r in results:
        p = r.portfolio
        if r.error:
            print(f"\n{r.agent.upper()} — ERROR: {r.error}")
            continue
        print(f"\n{r.agent.upper()}")
        print(
            f"  NAV ${p.total_nav:,.2f}  cash ${p.cash:,.2f}  "
            f"invested ${p.invested:,.2f}  "
            f"({p.cumulative_return_pct:+.2f}% since seed)"
        )
        print(
            f"  positions: {len(p.positions)}  "
            f"watchlist: {len(p.watchlist)}  "
            f"trades today: {len(r.trades)}  "
            f"universe: {r.universe_size}"
        )

        buys = [t for t in r.trades if t.side == "BUY"]
        sells = [t for t in r.trades if t.side == "SELL"]
        if buys:
            print(f"  BUYs ({len(buys)}):")
            for t in buys[:10]:
                print(
                    f"    +{t.shares:.0f} {t.ticker} @ ${t.price:.2f} "
                    f"= ${t.gross_value:,.2f} (cost ${t.cost_paid:.2f})"
                )
            if len(buys) > 10:
                print(f"    … {len(buys) - 10} more")
        if sells:
            print(f"  SELLs ({len(sells)}):")
            for t in sells[:10]:
                pnl = t.realized_pnl_usd
                pnl_pct = (pnl / (t.gross_value - pnl) * 100.0) if t.gross_value - pnl > 0 else 0.0
                print(
                    f"    −{t.shares:.0f} {t.ticker} @ ${t.price:.2f}  "
                    f"realized {pnl:+,.2f} ({pnl_pct:+.2f}%)"
                )
            if len(sells) > 10:
                print(f"    … {len(sells) - 10} more")

        if p.positions:
            top = sorted(p.positions, key=lambda x: -x.weight_pct)[:5]
            print("  Top holdings (weight, P&L):")
            for pos in top:
                print(
                    f"    {pos.ticker}  {pos.shares:.0f} sh × "
                    f"${pos.current_price:.2f} = ${pos.market_value:,.2f}  "
                    f"{pos.weight_pct:.1f}%  "
                    f"P&L {pos.pnl_pct:+.2f}%"
                )
    print()
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runner = DailyRunner()
    if args.mode == "close":
        results = runner.run_mark_to_market(as_of=args.as_of)
    else:
        results = runner.run(as_of=args.as_of)
    print(f"\n[mode={args.mode}]")
    print_report(results)
    return 0 if all(r.error is None for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
