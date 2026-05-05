"""One-time bulk fetcher for **every** SEC active filer.

The SEC's ``company_tickers.json`` lists ~10,000 entries. After
filtering out:

* Already-cached tickers (from the prior S&P 500 prefetch — ~600).
* Obvious non-equity entities (ETF / trust naming patterns).
* Tickers that clearly fail (404s) on the first fetch.

…we typically fetch ~6,000-8,000 new ticker fact dumps. Each fetch
is ~500ms-1s (one HTTP call returns the entire historical XBRL
record). Total cold-cache time: **30-90 minutes** depending on
network and SEC rate limits.

The script is **resumable** — re-running it skips tickers already
cached unless ``--force`` is passed.

Usage::

    .venv/bin/python -m scripts.prefetch_full_us_market
    .venv/bin/python -m scripts.prefetch_full_us_market --max-tickers 1000
    .venv/bin/python -m scripts.prefetch_full_us_market --force
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import EdgarFactsClient
from core.logger import get_logger

logger = get_logger("scripts.prefetch_full_us_market")


# Rough ETF/trust/holding-company naming heuristics. Anything matching
# these patterns is skipped to avoid wasting fetches on entities that
# don't have standard XBRL income statements.
ETF_NAME_PATTERNS: tuple[str, ...] = (
    " etf",
    " trust",
    " fund",
    " spdr",
    "ishares",
    "vanguard ",
    "schwab strategic",
    "proshares",
    "wisdomtree",
    "first trust",
    " strategist",
    "invesco ",
    "innovator ",
    "krane shares",
    "global x",
    "direxion",
)


@dataclass
class PrefetchStats:
    requested: int
    skipped_cached: int
    skipped_etf_pattern: int
    fetched: int
    failed_no_facts: int
    failed_error: int
    total_facts: int
    elapsed_seconds: float


def looks_like_non_equity(title: str | None) -> bool:
    if not title:
        return False
    lower = title.lower()
    return any(pat in lower for pat in ETF_NAME_PATTERNS)


def prefetch_full_market(
    *,
    force: bool = False,
    max_tickers: int | None = None,
    cache: EdgarCache | None = None,
    client: EdgarFactsClient | None = None,
) -> PrefetchStats:
    cache = cache or EdgarCache()
    client = client or EdgarFactsClient()

    # Load the full SEC company tickers map. EdgarFactsClient caches it.
    client._ensure_cik_map()
    # Re-fetch the raw entries (with company titles) so we can filter
    # ETFs by name. The map cache only stored ticker→cik.
    raw = client._get_json("https://www.sec.gov/files/company_tickers.json")
    entries = list(raw.values())
    logger.info(f"SEC reports {len(entries)} active filers")

    # Build the candidate list
    candidates: list[tuple[str, str]] = []  # (ticker, title)
    skipped_etf = 0
    for e in entries:
        try:
            ticker = str(e["ticker"]).upper()
            title = str(e.get("title", ""))
        except (KeyError, ValueError):
            continue
        if looks_like_non_equity(title):
            skipped_etf += 1
            continue
        candidates.append((ticker, title))

    if max_tickers:
        candidates = candidates[:max_tickers]

    logger.info(
        f"fetching {len(candidates)} candidates "
        f"(skipped {skipped_etf} ETF-like by name)"
    )

    start = time.monotonic()
    requested = len(candidates)
    skipped_cached = 0
    fetched = 0
    failed_no_facts = 0
    failed_error = 0
    total_facts = 0

    for i, (ticker, title) in enumerate(candidates, start=1):
        if not force and cache.has_ticker(ticker):
            skipped_cached += 1
            continue
        try:
            facts = client.get_company_facts(ticker)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[{i}/{requested}] {ticker}: error — {exc}")
            failed_error += 1
            continue
        if not facts:
            failed_no_facts += 1
            continue
        try:
            cache.save_facts(ticker, facts)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{i}/{requested}] {ticker}: cache write failed — {exc}")
            failed_error += 1
            continue
        fetched += 1
        total_facts += len(facts)

        if i % 100 == 0 or i == requested:
            elapsed = time.monotonic() - start
            rate = fetched / max(elapsed, 0.001)
            logger.info(
                f"[{i}/{requested}] cached={fetched} skipped_cached={skipped_cached} "
                f"failed={failed_error + failed_no_facts} facts={total_facts:,} "
                f"rate={rate:.2f} tickers/s"
            )

    elapsed = time.monotonic() - start
    return PrefetchStats(
        requested=requested,
        skipped_cached=skipped_cached,
        skipped_etf_pattern=skipped_etf,
        fetched=fetched,
        failed_no_facts=failed_no_facts,
        failed_error=failed_error,
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
        "--max-tickers",
        type=int,
        default=None,
        help="Cap the number of tickers attempted (for testing)",
    )
    args = parser.parse_args()

    stats = prefetch_full_market(force=args.force, max_tickers=args.max_tickers)

    cache = EdgarCache()
    s = cache.stats()
    print()
    print("=" * 60)
    print("FULL-MARKET PREFETCH COMPLETE")
    print("=" * 60)
    print(f"Tickers requested:    {stats.requested}")
    print(f"  Already cached:     {stats.skipped_cached}")
    print(f"  ETF-pattern skip:   {stats.skipped_etf_pattern}")
    print(f"  Newly fetched:      {stats.fetched}")
    print(f"  Failed (no facts):  {stats.failed_no_facts}")
    print(f"  Failed (error):     {stats.failed_error}")
    print(f"Facts written:        {stats.total_facts:,}")
    print(f"Elapsed:              {stats.elapsed_seconds:.1f}s")
    print()
    print(f"Cache state:")
    print(f"  Tickers in cache: {s.ticker_count}")
    print(f"  Total facts:      {s.total_facts:,}")
    print(f"  Cache size:       {s.total_size_mb():.1f} MB")
    print("=" * 60)


if __name__ == "__main__":
    main()
