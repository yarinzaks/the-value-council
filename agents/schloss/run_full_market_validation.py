"""Schloss validation on the **full US market** (not just S&P 500).

Same architecture as Greenblatt's full-market run. Decisions logged
to ``data/backtest_decisions/walter_schloss/<YYYY-MM-DD>.json``.

Usage::

    .venv/bin/python -m agents.schloss.run_full_market_validation
"""

from __future__ import annotations

import pandas as pd

from agents.schloss.deep_value import WalterSchloss
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

logger = get_logger("agents.schloss.run_full_market_validation")


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
        rebalance_freq="annual",
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
    # On the full market, Schloss's classic P/B 0.75 is appropriate —
    # the universe contains thousands of small caps where deep discounts
    # are common. Raised market-cap floor to $200M to match Greenblatt's
    # data-anomaly guard.
    strategy = WalterSchloss(
        portfolio_size=100,
        max_pb=0.75,
        max_de=1.0,
        min_years_public=0,  # see README D7
        min_market_cap=500_000_000.0,  # raised per priority spec
        decision_logger=decision_logger,
    )
    result = runner.run(strategy)
    report_dir = write_report(result)

    print()
    print("=" * 60)
    print("WALTER SCHLOSS — FULL US MARKET (2010-2024)")
    print("=" * 60)
    print((report_dir / "summary.txt").read_text())
    print("\nAnnual breakdown:")
    print(pd.read_csv(report_dir / "annual_returns.csv").to_string(index=False))
    print("\nDecisions logged: data/backtest_decisions/walter_schloss/")
    print(f"Report: {report_dir}")


if __name__ == "__main__":
    main()
