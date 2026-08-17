"""Build the fiscal calendar from the EDGAR cache already on disk.

Usage::

    .venv/bin/python -m scripts.build_fiscal_calendar
    .venv/bin/python -m scripts.build_fiscal_calendar --max-tickers 50

One profile per company: its fiscal year end, its median filing lag per
form, and the latest period each form has already reported. The refresh
reads it to decide who is worth re-pulling — see
:mod:`core.data.fiscal_calendar`.

Why this is a separate job, and an annual one
---------------------------------------------

Nothing here needs the network: every input is in the parquet the weekly
refresh already writes. What it does need is *all* of it, so it is a
full pass over several thousand files, and there is no reason to repeat
that weekly. A fiscal year end changes when a company reorganises,
which is rare enough that once a year is generous, and the profile's
``last_period_end`` — the part that moves every quarter — is not read
from the calendar at all. It is re-derived from the cache at refresh
time, so a calendar built in January is still correct in October about
everything it is asked.

An out-of-date profile costs nothing dangerous either way: an unknown or
mis-modelled company is treated as always due, which is the schedule
that ran before any of this existed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from core.data.edgar_cache import EdgarCache
from core.data.fiscal_calendar import (
    build_profile,
    filings_from_facts,
    save_calendar,
)
from core.logger import get_logger
from core.paths import fiscal_calendar_path

logger = get_logger("scripts.build_fiscal_calendar")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=None,
        help="Cap how many tickers are profiled (for a smoke run)",
    )
    args = parser.parse_args(argv)

    cache = EdgarCache()
    tickers = cache.tickers()
    if args.max_tickers is not None:
        tickers = tickers[: args.max_tickers]
    if not tickers:
        logger.error("no cached tickers — nothing to profile")
        return 1

    profiles = {}
    unmodellable = 0
    for i, ticker in enumerate(tickers, 1):
        try:
            filings = filings_from_facts(ticker, cache.load_dataframe(ticker))
            profile = build_profile(ticker, filings)
        except Exception as exc:
            # One unreadable parquet must not cost the whole calendar.
            logger.debug(f"{ticker}: skipped — {exc}")
            unmodellable += 1
            continue
        if profile is None:
            unmodellable += 1
            continue
        profiles[ticker] = profile
        if i % 500 == 0:
            logger.info(f"profiled {i}/{len(tickers)}")

    path = save_calendar(profiles, path=fiscal_calendar_path(), built_on=date.today())

    print(f"tickers scanned : {len(tickers)}")
    print(f"profiles built  : {len(profiles)}")
    print(f"unmodellable    : {unmodellable}  (always refreshed, never dropped)")
    print(f"written to      : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
