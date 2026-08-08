"""Credit the dividends the live books earned but were never paid.

Why this exists
~~~~~~~~~~~~~~~

Settlement drew its lower bound from ``last_open_run``, which the
previous run had already advanced to the current date — so it asked for
dividends over an empty interval. Sixty-eight consecutive daily runs
credited $0.00 while eleven ex-dates passed on Neff's book alone. The
fix is in :meth:`core.live.runner.DailyRunner._settle_dividends`; this
script recovers what was missed before it landed.

What it can and cannot recover
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Only dividends on positions **still held**, bounded by each position's
entry date. Income on a line already closed is gone: the portfolio
keeps current holdings, not a historical share ledger, so there is no
way to know how many shares of a sold name were held on a past ex-date.
The recovered figure is therefore a floor, not the full amount, and
this script says so rather than implying completeness.

The correction lands as cash on the day it is run. Earlier snapshots
are left alone — they recorded what NAV was believed to be at the time,
and rewriting sixty-eight days of history to make a chart smoother is
the opposite of what this codebase is for.

Usage::

    .venv/bin/python -m scripts.backfill_dividends            # report only
    .venv/bin/python -m scripts.backfill_dividends --apply    # write
"""

from __future__ import annotations

import sys
from datetime import date

from core.backtest.data_loader import PriceDataLoader
from core.live.portfolio import LivePortfolio, LivePortfolioError
from core.live.snapshots import SNAPSHOTS_DIR
from core.logger import get_logger
from core.paths import portfolios_dir

logger = get_logger("scripts.backfill_dividends")


def _inception_of(agent: str) -> str | None:
    """The book's first snapshot date — when it started earning.

    A portfolio cannot collect income from before it existed, and the
    snapshot series is the only record of when that was.
    """
    d = SNAPSHOTS_DIR / agent
    if not d.is_dir():
        return None
    stems = sorted(p.stem for p in d.glob("*.json"))
    return stems[0] if stems else None


def main() -> int:
    apply = "--apply" in sys.argv
    loader = PriceDataLoader()
    today = date.today()

    grand_total = 0.0
    grand_count = 0
    print(f"{'agent':26} {'credited':>10} {'payments':>9}  inception")
    print("-" * 62)

    for path in sorted(portfolios_dir().glob("*.json")):
        agent = path.stem
        portfolio = LivePortfolio.load_or_seed(agent, directory=portfolios_dir())

        inception = portfolio.inception_date[:10] or _inception_of(agent)
        if inception is None:
            print(f"{agent:26} {'—':>10} {'—':>9}  no snapshots; skipped")
            continue
        if not portfolio.inception_date:
            portfolio.inception_date = inception

        credited = 0.0
        payments = 0
        for pos in list(portfolio.positions):
            since = max(pos.entry_date[:10], inception)
            for ex_date, amount in loader.dividends_between(
                pos.ticker, since, today
            ):
                try:
                    cash = portfolio.credit_dividend(
                        pos.ticker,
                        amount_per_share=amount,
                        ex_date=ex_date.isoformat(),
                    )
                except LivePortfolioError as exc:
                    logger.warning(f"{agent}: {pos.ticker} {ex_date}: {exc}")
                    continue
                if cash:
                    credited += cash
                    payments += 1

        grand_total += credited
        grand_count += payments
        print(f"{agent:26} {credited:>10.2f} {payments:>9}  {inception}")

        if apply:
            portfolio.save(directory=portfolios_dir())

    print("-" * 62)
    print(f"{'TOTAL':26} {grand_total:>10.2f} {grand_count:>9}")
    print()
    if apply:
        print("Written. Positions already closed are not recoverable — this")
        print("is a floor on what was missed, not the full amount.")
    else:
        print("Report only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
