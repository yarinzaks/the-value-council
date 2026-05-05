"""Full Greenblatt Magic Formula validation backtest.

Runs the strategy against the **complete survivorship-bias-free
S&P 500 universe** for the full **2010-01-04 → 2024-12-31** window
using the local Parquet fundamentals cache (no FMP rate limits).

Pre-requisite::

    .venv/bin/python -m scripts.prefetch_sp500_history

Usage::

    .venv/bin/python -m agents.greenblatt.run_full_validation
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from agents.greenblatt.magic_formula import MagicFormula
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

logger = get_logger("agents.greenblatt.run_full_validation")


def main() -> None:
    cache = EdgarCache()
    stats = cache.stats()
    logger.info(
        f"EDGAR cache: {stats.ticker_count} tickers, "
        f"{stats.total_facts:,} facts, {stats.total_size_mb():.1f} MB"
    )
    if stats.ticker_count == 0:
        raise SystemExit(
            "No EDGAR cache found. Run "
            "`.venv/bin/python -m scripts.prefetch_sp500_history` first."
        )

    fetcher = FundamentalsFetcher(
        cache=cache,
        client=None,  # offline mode — use cache only
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
    strategy = MagicFormula(
        portfolio_size=30,
        min_market_cap=1_000_000_000.0,
    )
    result = runner.run(strategy)
    report_dir = write_report(result)

    print()
    print("=" * 60)
    print("GREENBLATT MAGIC FORMULA — FULL VALIDATION (2010-2024)")
    print("=" * 60)
    print((report_dir / "summary.txt").read_text())

    print("\nAnnual breakdown:")
    annual = pd.read_csv(report_dir / "annual_returns.csv")
    print(annual.to_string(index=False))

    print("\nSelections per rebalance:")
    for sel in strategy.selection_history:
        print(
            f"  {sel.as_of}: {sel.candidates_after_filters}/{sel.universe_size} "
            f"survivors → top combined rank "
            f"{sel.top_scores[0].combined_rank if sel.top_scores else 'N/A'}, "
            f"selected {len(sel.selected_tickers)} stocks"
        )

    print(f"\nReport written to: {report_dir}")


if __name__ == "__main__":
    main()
