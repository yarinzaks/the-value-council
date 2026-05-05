"""Cross-source validation of the EDGAR cache vs FMP.

Samples 50 random tickers from the cache and compares the key
fundamental fields (revenue, net_income, operating_income, total_assets,
current_assets, current_liabilities) against FMP's reported numbers.

Tolerance defaults to 5% — discrepancies up to that level are accepted
because the two sources apply different normalizations (vendor-side
adjustments, restatement timing, etc.).

Outputs:
    * Stdout: pass rate, systematic discrepancies (fields that fail
      consistently), per-ticker summary.
    * ``data/cache_validation_report.json``: full structured report.

Usage::

    .venv/bin/python -m scripts.validate_edgar_cache
    .venv/bin/python -m scripts.validate_edgar_cache --sample 100
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from core.backtest.fmp_adapter import FMPAdapter
from core.data.cache_validator import CacheValidator
from core.data.edgar_cache import EdgarCache
from core.data.fundamentals_fetcher import (
    FundamentalsFetcher,
    FundamentalsFetcherConfig,
)
from core.logger import get_logger

logger = get_logger("scripts.validate_edgar_cache")

DEFAULT_AS_OF = date(2024, 6, 30)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample", type=int, default=50, help="Number of tickers to validate"
    )
    parser.add_argument(
        "--tolerance", type=float, default=0.05, help="Relative tolerance (default 5%)"
    )
    parser.add_argument(
        "--as-of",
        default=DEFAULT_AS_OF.isoformat(),
        help="PIT date for the comparison (YYYY-MM-DD)",
    )
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of)

    cache = EdgarCache()
    tickers = cache.tickers()
    if not tickers:
        raise SystemExit(
            "No tickers in cache. Run "
            "`.venv/bin/python -m scripts.prefetch_sp500_history` first."
        )
    logger.info(f"validating against {len(tickers)} cached tickers")

    edgar = FundamentalsFetcher(
        cache=cache,
        client=None,
        config=FundamentalsFetcherConfig(populate_cache_on_miss=False),
    )
    fmp = FMPAdapter()
    validator = CacheValidator(edgar, fmp, tolerance=args.tolerance)
    report = validator.validate_sample(tickers, args.sample, as_of)

    out_path = PROJECT_ROOT / "data" / "cache_validation_report.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2, default=str))
    logger.info(f"wrote validation report → {out_path}")

    print()
    print("=" * 60)
    print(f"CACHE VALIDATION REPORT — {as_of}")
    print("=" * 60)
    print(f"Sample size:         {report.sample_size}")
    print(f"Tolerance:           {report.tolerance:.1%}")
    print(f"Fields validated:    {', '.join(report.fields_validated)}")
    print(f"Overall pass rate:   {report.overall_pass_rate():.1%}")
    discrepancies = report.systematic_discrepancies(threshold=0.50)
    if discrepancies:
        print(f"Systematic issues:   {', '.join(discrepancies)}")
    else:
        print("Systematic issues:   none")
    print()
    print("Per-ticker summary (top 20):")
    for t in report.per_ticker[:20]:
        print(
            f"  {t.ticker:<6} on {t.as_of}: "
            f"{t.fields_within_tolerance}/{t.fields_compared} fields within "
            f"{report.tolerance:.0%}"
        )
    print(f"\nFull JSON report: {out_path}")


if __name__ == "__main__":
    main()
