"""Bulk-fetch SIC industry codes for every ticker in the EDGAR cache.

Output: ``data_bundled/company_sic.json`` mapping ``TICKER -> sic`` (int).

Why bundle: we use SIC codes for industry-relative scoring (Neff's
methodology). The SEC submissions endpoint is permissive for cloud IPs
but each ticker requires its own HTTP call (~125ms). Pre-fetching once
locally and bundling the result keeps daily runs fast and avoids
hammering SEC every time.

Resumable: the script reads the existing bundled file (if present) and
skips tickers that already have a non-null SIC.

Usage::

    .venv/bin/python -m scripts.prefetch_sic
    .venv/bin/python -m scripts.prefetch_sic --max-tickers 100
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import EdgarFactsClient
from core.logger import get_logger

logger = get_logger("scripts.prefetch_sic")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_PATH = PROJECT_ROOT / "data_bundled" / "company_sic.json"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-tickers", type=int, default=None)
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    cache = EdgarCache()
    tickers = cache.tickers()
    logger.info(f"considering {len(tickers)} cached tickers")

    if BUNDLE_PATH.exists() and not args.force:
        existing = json.loads(BUNDLE_PATH.read_text())
        logger.info(f"resuming from existing bundle ({len(existing)} entries)")
    else:
        existing = {}

    if args.max_tickers:
        tickers = tickers[: args.max_tickers]

    client = EdgarFactsClient()
    BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)

    fetched = 0
    skipped = 0
    failed = 0
    for i, ticker in enumerate(tickers):
        if ticker in existing and existing[ticker] is not None:
            skipped += 1
            continue
        sic = client.get_sic_for_ticker(ticker)
        if sic is None:
            failed += 1
            existing[ticker] = None
        else:
            fetched += 1
            existing[ticker] = sic
        # Periodic flush so a Ctrl-C mid-fetch doesn't lose progress.
        if (i + 1) % 200 == 0:
            BUNDLE_PATH.write_text(json.dumps(existing, indent=0, ensure_ascii=False))
            logger.info(
                f"{i + 1}/{len(tickers)}: fetched={fetched} skipped={skipped} failed={failed}"
            )

    BUNDLE_PATH.write_text(json.dumps(existing, indent=0, ensure_ascii=False))
    n_with_sic = sum(1 for v in existing.values() if v is not None)
    logger.info(
        f"done: {n_with_sic} tickers with SIC codes "
        f"({100 * n_with_sic / max(len(existing), 1):.1f}% of {len(existing)})"
    )
    print(f"wrote {BUNDLE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
