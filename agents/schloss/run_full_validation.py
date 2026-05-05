"""Full Walter Schloss validation backtest.

Runs the diversified deep-value strategy against the complete
survivorship-bias-free S&P 500 universe for **2010-01-04 → 2024-12-31**
using the prefetched EDGAR Parquet cache.

Pre-requisite::

    .venv/bin/python -m scripts.prefetch_sp500_history

Usage::

    .venv/bin/python -m agents.schloss.run_full_validation
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from agents.schloss.deep_value import WalterSchloss
from core.backtest.data_loader import PriceDataLoader
from core.backtest.point_in_time import PointInTimeLoader
from core.backtest.reporting import write_report
from core.backtest.strategy_runner import (
    BacktestRunner,
    RunnerConfig,
)
from core.backtest.transaction_costs import PercentageCost
from core.backtest.universe import load_universe
from core.data.edgar_cache import EdgarCache
from core.data.fundamentals_fetcher import (
    CachedEdgarAdapter,
    FundamentalsFetcher,
    FundamentalsFetcherConfig,
)
from core.logger import get_logger

logger = get_logger("agents.schloss.run_full_validation")


def main() -> None:
    cache = EdgarCache()
    stats = cache.stats()
    logger.info(
        f"EDGAR cache: {stats.ticker_count} tickers, "
        f"{stats.total_facts:,} facts, {stats.total_size_mb():.1f} MB"
    )
    if stats.ticker_count == 0:
        raise SystemExit(
            "No EDGAR cache. Run "
            "`.venv/bin/python -m scripts.prefetch_sp500_history` first."
        )

    fetcher = FundamentalsFetcher(
        cache=cache,
        client=None,
        config=FundamentalsFetcherConfig(populate_cache_on_miss=False),
    )
    cached_adapter = CachedEdgarAdapter(fetcher=fetcher)
    pit_loader = PointInTimeLoader(adapter=cached_adapter)

    universe = load_universe()
    logger.info(
        f"Universe: {len(universe.current)} current constituents, "
        f"{len(universe.changes)} historical changes"
    )

    cfg = RunnerConfig(
        start_date=date(2010, 1, 4),
        end_date=date(2024, 12, 31),
        initial_cash=10_000.0,
        rebalance_freq="annual",
        benchmark_ticker="SPY",
        cost_model=PercentageCost(0.001),  # 10 bps per side
        use_universe=True,
        use_fundamentals=True,
    )

    runner = BacktestRunner(
        cfg,
        price_loader=PriceDataLoader(),
        pit_loader=pit_loader,
        universe=universe,
    )
    # S&P 500 calibration:
    # - Schloss's natural universe was small/mid-caps where P/B < 0.75
    #   produced 100-200 candidates. In the S&P 500, P/B < 0.75 yields
    #   nearly zero non-financial survivors after D/E ≤ 1.0. We relax
    #   to P/B ≤ 1.0 ("below book" still) to make the strategy
    #   meaningful on this universe. Documented in README §D6.
    # - min_years_public set to 0 because the existing source-filing
    #   date is the LATEST filing, not earliest, so this filter would
    #   otherwise reject everything. S&P 500 inclusion implies maturity.
    strategy = WalterSchloss(
        portfolio_size=100,
        max_pb=1.0,
        max_de=1.0,
        min_years_public=0,
        min_market_cap=300_000_000.0,
    )
    result = runner.run(strategy)
    report_dir = write_report(result)

    print()
    print("=" * 60)
    print("WALTER SCHLOSS — FULL VALIDATION (2010-2024)")
    print("=" * 60)
    print((report_dir / "summary.txt").read_text())

    print("\nAnnual breakdown:")
    annual = pd.read_csv(report_dir / "annual_returns.csv")
    print(annual.to_string(index=False))

    print("\nSelections per rebalance:")
    for sel in strategy.selection_history:
        median_pb = (
            sorted(s.pb_ratio for s in sel.top_scores)[len(sel.top_scores) // 2]
            if sel.top_scores
            else 0.0
        )
        top_pb = sel.top_scores[0].pb_ratio if sel.top_scores else 0.0
        print(
            f"  {sel.as_of}: {sel.candidates_after_filters}/{sel.universe_size} "
            f"survivors → selected {len(sel.selected_tickers)} stocks "
            f"(top P/B {top_pb:.3f}, median {median_pb:.3f})"
        )

    print(f"\nReport written to: {report_dir}")


if __name__ == "__main__":
    main()
