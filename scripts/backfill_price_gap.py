"""Refill a hole in ``prices.sqlite``.

Why
~~~

The cache is assembled by whatever asked for prices, whenever it asked,
so its coverage is a record of past queries rather than of the market.
Counting distinct tickers per month exposes what that leaves behind::

    2024-12   5,215 tickers
    2025-01        0
    ...            0        <- seven months, nothing at all
    2025-07        0
    2025-08      201
    2025-12      204
    2026-01    4,385

January to July 2025 is a total blackout and the rest of the year holds
201-204 names. Nothing about the market changed; the backtests that
populated the cache ran to 2024-12-31, and the live scanner started
filling 2026 when it was switched on.

A gap like this does not announce itself in a backtest. Returns are
computed from whatever bars exist, so a strategy simply skips the
missing year and reports a number for a period it never traded.

Usage::

    .venv/bin/python -m scripts.backfill_price_gap --start 2025-01-01 --end 2025-12-31
    .venv/bin/python -m scripts.backfill_price_gap --start 2025-01-01 --end 2025-12-31 --apply

Dry-run by default: it reports how many tickers are missing bars in the
window and stops. ``--apply`` performs the fetches.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import date, datetime

from core.backtest.data_loader import PriceDataLoader
from core.logger import get_logger
from core.paths import DATA_ROOT

logger = get_logger("scripts.backfill_price_gap")

#: Pause between fetches. yfinance throttles aggressively when a client
#: runs flat out, and a throttled response is cached as "no data" by
#: :meth:`PriceDataLoader.get_history` — which would burn the gap in
#: permanently instead of filling it.
FETCH_PAUSE_SECONDS = 0.25

#: Report progress every N tickers.
PROGRESS_EVERY = 100


def _prices_db() -> sqlite3.Connection:
    return sqlite3.connect(DATA_ROOT / "cache" / "prices.sqlite")


def tickers_missing_window(start: date, end: date) -> list[str]:
    """Tickers with bars outside the window but none (or few) inside it.

    A ticker that never traded in the window — it listed later, or was
    acquired earlier — is not missing anything, so the query only asks
    about symbols whose series straddles the hole.
    """
    conn = _prices_db()
    try:
        rows = conn.execute(
            """
            SELECT ticker,
                   MIN(trade_date) AS first_bar,
                   MAX(trade_date) AS last_bar,
                   SUM(CASE WHEN trade_date BETWEEN ? AND ? THEN 1 ELSE 0 END)
                       AS bars_in_window
            FROM prices
            GROUP BY ticker
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    # Roughly 21 trading days a month; ask for at least half of what a
    # fully-covered ticker would have before calling it present.
    months = max((end - start).days / 30.44, 1.0)
    expected = 21 * months
    threshold = expected * 0.5

    missing: list[str] = []
    for ticker, first_bar, last_bar, in_window in rows:
        if first_bar > start.isoformat() or last_bar < end.isoformat():
            # Series does not straddle the window — nothing to refill.
            continue
        if in_window < threshold:
            missing.append(ticker)
    return sorted(missing)


#: A US trading year holds about 252 sessions. A year inside a ticker's
#: span with fewer than this many bars is missing data rather than
#: reflecting a quiet market — the thinnest legitimate cases are the
#: first and last years of a listing, and those are at the edges of the
#: span, which this check excludes by construction.
MIN_BARS_PER_FULL_YEAR = 200


def tickers_with_interior_gaps() -> list[str]:
    """Tickers with a thin or blank year between two well-covered ones.

    This is the more honest definition of a hole. :func:`tickers_missing_window`
    needs a window named up front and only catches symbols whose series
    straddles it; a name that listed in 2012 and lost 2015 slips past it.
    A year with no bars, flanked on both sides by years with bars, cannot
    be explained by a listing or a delisting.

    Counting *thin* years rather than only empty ones matters: a ticker
    missing March to August still prices every rebalance in the months
    it kept, so it never looks absent — it just quietly stops being
    measurable for half a year, and a momentum window spanning the hole
    compares two prices with a gap in between.

    It finds the damage a single-year sweep misses entirely — including
    AMZN, AAPL, PANW and BLK, the largest winners of the decade, absent
    from the window a strategy would otherwise be designed on.
    """
    conn = _prices_db()
    try:
        rows = conn.execute(
            """
            SELECT ticker, CAST(SUBSTR(trade_date, 1, 4) AS INTEGER) AS yr,
                   COUNT(*) AS n
            FROM prices GROUP BY ticker, yr
            """
        ).fetchall()
    finally:
        conn.close()

    bars: dict[str, dict[int, int]] = {}
    for ticker, yr, n in rows:
        bars.setdefault(ticker, {})[yr] = n
    return holed_tickers(bars)


def holed_tickers(bars: dict[str, dict[int, int]]) -> list[str]:
    """The decision rule, separated from the query so it can be tested.

    ``bars`` maps ticker to ``{year: bar_count}``.
    """
    holed: list[str] = []
    for ticker, by_year in bars.items():
        if len(by_year) < 2:
            continue
        first, last = min(by_year), max(by_year)
        # Interior years only: the first and last year of a listing are
        # legitimately partial.
        interior = range(first + 1, last)
        if any(by_year.get(y, 0) < MIN_BARS_PER_FULL_YEAR for y in interior):
            holed.append(ticker)
    return sorted(holed)


def all_tickers() -> list[str]:
    """Every symbol the database has ever held a bar for.

    The blunt option, and the one worth reaching for once a cache has
    lost trust. Detecting exactly which tickers are damaged takes
    judgement about what a hole looks like; refetching all of them takes
    about an hour and needs no judgement at all.
    """
    conn = _prices_db()
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM prices").fetchall()
    finally:
        conn.close()
    return sorted(r[0] for r in rows)


def backfill(tickers: list[str], start: date, end: date) -> tuple[int, int, int]:
    """Fetch ``start..end`` for each ticker. Returns (ok, empty, failed)."""
    loader = PriceDataLoader()
    ok = empty = failed = 0
    total = len(tickers)
    began = time.monotonic()

    for i, ticker in enumerate(tickers, start=1):
        try:
            # force_refresh is required, not an optimisation.
            # PriceDataLoader.get_history treats "the cached span brackets
            # the request" as "the request is covered" (data_loader.py:382),
            # so for a ticker with bars in 2024 and 2026 it returns the
            # empty middle from cache and never asks the vendor. That is
            # how this hole became permanent, and it is why refilling it
            # has to say explicitly that the cache is not to be trusted.
            bars = loader.get_history(ticker, start, end, force_refresh=True)
            if not bars.empty:
                ok += 1
            else:
                empty += 1
        # One bad symbol must not end a multi-hour run. The count is
        # reported at the end so a sweep that quietly lost a thousand
        # tickers cannot be mistaken for a clean one.
        except Exception as exc:
            failed += 1
            logger.debug(f"{ticker}: {exc}")

        if i % PROGRESS_EVERY == 0 or i == total:
            elapsed = time.monotonic() - began
            rate = i / elapsed if elapsed else 0.0
            remaining = (total - i) / rate if rate else 0.0
            logger.info(
                f"{i}/{total} — {ok} filled, {empty} empty, {failed} failed "
                f"({rate:.1f}/s, ~{remaining / 60:.0f} min left)"
            )
        time.sleep(FETCH_PAUSE_SECONDS)

    return ok, empty, failed


def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=_parse_date)
    parser.add_argument("--end", required=True, type=_parse_date)
    parser.add_argument(
        "--mode",
        choices=("window", "interior", "all"),
        default="window",
        help=(
            "window: refill tickers whose series straddles --start/--end "
            "but is empty inside it. interior: refill every ticker with a "
            "thin or blank year between two well-covered ones. all: refetch "
            "--start..--end for every ticker in the database, trusting "
            "nothing."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the fetches; without it, only report what is missing",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="stop after this many tickers (for a smoke test)",
    )
    parser.add_argument(
        "--shard",
        default=None,
        metavar="N/M",
        help=(
            "take only shard N of M, e.g. 2/3. The work is one HTTP round "
            "trip per ticker, so running several shards at once divides the "
            "wall clock without touching the vendor any harder per shard. "
            "Shards are interleaved rather than blocked, so each covers the "
            "whole alphabet and a partial run leaves no contiguous hole."
        ),
    )
    args = parser.parse_args()

    if args.start >= args.end:
        parser.error(f"start {args.start} is not before end {args.end}")

    if args.mode == "all":
        missing = all_tickers()
        what = "in the database — refetching every one"
    elif args.mode == "interior":
        missing = tickers_with_interior_gaps()
        what = "have a thin or blank year between two well-covered ones"
    else:
        missing = tickers_missing_window(args.start, args.end)
        what = f"straddle {args.start}..{args.end} but have little or no data inside it"
    if args.shard:
        try:
            index_text, count_text = args.shard.split("/")
            index, count = int(index_text), int(count_text)
        except ValueError:
            parser.error(f"--shard wants N/M, got {args.shard!r}")
        if not 1 <= index <= count:
            parser.error(f"--shard {args.shard} is out of range")
        missing = missing[index - 1 :: count]
        logger.info(f"shard {index}/{count}: {len(missing)} tickers")

    if args.limit is not None:
        missing = missing[: args.limit]

    logger.info(f"{len(missing)} tickers {what}")
    if not missing:
        return 0
    logger.info(f"first 10: {', '.join(missing[:10])}")

    if not args.apply:
        logger.info("dry run — pass --apply to fetch")
        return 0

    ok, empty, failed = backfill(missing, args.start, args.end)
    logger.info(f"done: {ok} filled, {empty} returned nothing, {failed} errored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
