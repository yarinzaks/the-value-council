"""Dreman 4-metric contrarian validation on the **full US market**.

Universe: every SEC active filer with operating XBRL footprint, market
cap >= $500M. Decisions logged per rebalance to
``data/backtest_decisions/david_dreman/<YYYY-MM-DD>.json``.

Usage::

    .venv/bin/python -m agents.dreman.run_full_market_validation
"""

from __future__ import annotations

import pandas as pd

from agents.dreman.contrarian import DavidDreman
from core.backtest.data_loader import PriceDataLoader
from core.backtest.decision_logger import DecisionLogger
from core.backtest.full_market_universe import FullMarketUniverse
from core.backtest.point_in_time import PointInTimeLoader
from core.backtest.reporting import write_report
from core.backtest.strategy_runner import (
    BacktestRunner,
    RunnerConfig,
)
from core.backtest.transaction_costs import PercentageCost
from core.backtest.validation_window import (
    VALIDATION_END,
    VALIDATION_REBALANCE,
    VALIDATION_START,
)
from core.data.edgar_cache import EdgarCache
from core.data.fundamentals_fetcher import (
    CachedEdgarAdapter,
    FundamentalsFetcher,
    FundamentalsFetcherConfig,
)
from core.logger import get_logger
from core.paths import backtest_decisions_dir

logger = get_logger("agents.dreman.run_full_market_validation")


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
            "`.venv/bin/python -m scripts.prefetch_full_us_market` first."
        )

    fetcher = FundamentalsFetcher(
        cache=cache,
        client=None,
        config=FundamentalsFetcherConfig(populate_cache_on_miss=False),
    )
    cached_adapter = CachedEdgarAdapter(fetcher=fetcher)
    pit_loader = PointInTimeLoader(adapter=cached_adapter)

    universe = FullMarketUniverse(cache=cache)
    universe._ensure_loaded()
    universe_stats = universe.stats()
    logger.info(
        f"Full-market universe: {universe_stats['total_indexed']} indexed, "
        f"{universe_stats['with_operating_concepts']} with operating data"
    )

    decision_logger = DecisionLogger(root=backtest_decisions_dir())

    cfg = RunnerConfig(
        start_date=VALIDATION_START,
        end_date=VALIDATION_END,
        initial_cash=10_000.0,
        rebalance_freq=VALIDATION_REBALANCE,
        benchmark_ticker="SPY",
        cost_model=PercentageCost(0.001),
        use_universe=True,
        use_fundamentals=True,
    )

    runner = BacktestRunner(
        cfg,
        price_loader=PriceDataLoader(),
        pit_loader=pit_loader,
        universe=universe,
    )
    strategy = DavidDreman(
        portfolio_size=25,  # Dreman's mid-range (20-30)
        min_qualifying_metrics=2,  # bottom 20% on >= 2 of 4 per playbook
        min_market_cap=500_000_000.0,
        decision_logger=decision_logger,
    )
    result = runner.run(strategy)
    report_dir = write_report(result)

    print()
    print("=" * 60)
    print("DREMAN 4-METRIC CONTRARIAN — FULL US MARKET (2020-2024)")
    print("=" * 60)
    print((report_dir / "summary.txt").read_text())
    print("\nAnnual breakdown:")
    print(pd.read_csv(report_dir / "annual_returns.csv").to_string(index=False))
    print("\nDecisions logged: data/backtest_decisions/david_dreman/")
    print(f"Report: {report_dir}")


if __name__ == "__main__":
    main()
