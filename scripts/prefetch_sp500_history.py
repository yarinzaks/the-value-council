"""One-time bulk fetcher for S&P 500 historical fundamentals.

Walks through:

1. The current S&P 500 constituent list, plus every ticker that has
   ever been in the index per the historical change log
   (survivorship-bias-free coverage).
2. For each, fetches the SEC Company Facts JSON (one HTTP call per
   ticker — returns the entire reported XBRL history).
3. Persists to ``data/fundamentals_cache/{TICKER}.parquet``.

Self-throttled to ~8 req/s, well within SEC's 10 req/s limit.
Idempotent — re-running skips tickers that already have a cache file
unless ``--force`` is passed.

Usage::

    .venv/bin/python -m scripts.prefetch_sp500_history
    .venv/bin/python -m scripts.prefetch_sp500_history --force
    .venv/bin/python -m scripts.prefetch_sp500_history --tickers AAPL MSFT
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from core.backtest.universe import HistoricalUniverse, load_universe
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import EdgarFactsClient
from core.logger import get_logger

logger = get_logger("scripts.prefetch_sp500_history")


@dataclass
class PrefetchStats:
    requested: int
    skipped_cached: int
    fetched: int
    failed: int
    total_facts: int
    elapsed_seconds: float


def collect_all_tickers(universe: HistoricalUniverse) -> list[str]:
    """Union of current constituents + every ticker ever added or
    removed in the change log."""
    tickers: set[str] = set(universe.current)
    for change in universe.changes:
        if change.added_ticker:
            tickers.add(change.added_ticker)
        if change.removed_ticker:
            tickers.add(change.removed_ticker)
    return sorted(tickers)


def prefetch(
    tickers: list[str],
    *,
    force: bool = False,
    cache: EdgarCache | None = None,
    client: EdgarFactsClient | None = None,
) -> PrefetchStats:
    cache = cache or EdgarCache()
    client = client or EdgarFactsClient()

    start = time.monotonic()
    requested = len(tickers)
    skipped = 0
    fetched = 0
    failed = 0
    total_facts = 0

    for i, ticker in enumerate(tickers, start=1):
        if not force and cache.has_ticker(ticker):
            skipped += 1
            continue
        try:
            facts = client.get_company_facts(ticker)
        except Exception as exc:
            logger.warning(f"[{i}/{requested}] {ticker}: fetch failed — {exc}")
            failed += 1
            continue
        if not facts:
            logger.info(f"[{i}/{requested}] {ticker}: no facts available")
            failed += 1
            continue
        try:
            cache.save_facts(ticker, facts)
        except Exception as exc:
            logger.warning(f"[{i}/{requested}] {ticker}: cache write failed — {exc}")
            failed += 1
            continue
        fetched += 1
        total_facts += len(facts)
        if i % 25 == 0 or i == requested:
            elapsed = time.monotonic() - start
            rate = fetched / max(elapsed, 0.001)
            logger.info(
                f"[{i}/{requested}] cached={fetched} skipped={skipped} "
                f"failed={failed} facts={total_facts:,} rate={rate:.2f} tickers/s"
            )

    elapsed = time.monotonic() - start
    return PrefetchStats(
        requested=requested,
        skipped_cached=skipped,
        fetched=fetched,
        failed=failed,
        total_facts=total_facts,
        elapsed_seconds=elapsed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even tickers already cached",
    )
    parser.add_argument(
        "--tickers",
        nargs="*",
        default=None,
        help="Override the universe with a hand-picked ticker list",
    )
    args = parser.parse_args()

    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
        logger.info(f"using user-supplied ticker list: {len(tickers)} tickers")
    else:
        universe = load_universe()
        tickers = collect_all_tickers(universe)
        logger.info(
            f"S&P 500 universe (current + historical changes): {len(tickers)} tickers"
        )

    stats = prefetch(tickers, force=args.force)

    cache = EdgarCache()
    s = cache.stats()
    print()
    print("=" * 60)
    print("PREFETCH COMPLETE")
    print("=" * 60)
    print(f"Tickers requested: {stats.requested}")
    print(f"  Already cached:  {stats.skipped_cached}")
    print(f"  Newly fetched:   {stats.fetched}")
    print(f"  Failed:          {stats.failed}")
    print(f"Facts written:     {stats.total_facts:,}")
    print(f"Elapsed:           {stats.elapsed_seconds:.1f}s")
    print()
    print("Cache state:")
    print(f"  Tickers in cache: {s.ticker_count}")
    print(f"  Total facts:      {s.total_facts:,}")
    print(f"  Cache size:       {s.total_size_mb():.1f} MB")
    print("=" * 60)


if __name__ == "__main__":
    main()
