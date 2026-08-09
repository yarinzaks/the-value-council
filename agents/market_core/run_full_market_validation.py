"""Market Core validation on the **full US market**.

Same window, frequency, universe and cost model as the other ten, so
the leaderboard row means the same thing theirs does. Decisions logged
per rebalance to ``data/backtest_decisions/market_core/``.

The screen needs filed share counts to build a market capitalisation,
but it reads them straight from the XBRL cache rather than through the
point-in-time loader, so ``use_fundamentals`` stays off and the run
avoids an hour of per-rebalance EDGAR work.

Usage::

    .venv/bin/python -m agents.market_core.run_full_market_validation
"""

from __future__ import annotations

import pandas as pd

from agents.market_core.strategy import MarketCore
from core.backtest.data_loader import PriceDataLoader
from core.backtest.decision_logger import DecisionLogger
from core.backtest.full_market_universe import FullMarketUniverse
from core.backtest.reporting import write_report
from core.backtest.strategy_runner import BacktestRunner, RunnerConfig
from core.backtest.transaction_costs import PercentageCost
from core.backtest.validation_window import (
    VALIDATION_END,
    VALIDATION_REBALANCE,
    VALIDATION_START,
)
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger
from core.paths import backtest_decisions_dir

logger = get_logger("agents.market_core.run_full_market_validation")


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
        use_fundamentals=False,
    )

    # One loader for the runner's marks and for both screening
    # measures, so a bar a position was priced from is the same bar its
    # volatility was estimated against.
    price_loader = PriceDataLoader()

    runner = BacktestRunner(cfg, price_loader=price_loader, universe=universe)
    strategy = MarketCore(
        price_loader=price_loader,
        edgar_cache=cache,
        decision_logger=decision_logger,
    )
    result = runner.run(strategy)
    report_dir = write_report(result)

    print()
    print("=" * 60)
    print("QUIET COMPOUNDER — FULL US MARKET")
    print("=" * 60)
    print((report_dir / "summary.txt").read_text())
    print("\nAnnual breakdown:")
    print(pd.read_csv(report_dir / "annual_returns.csv").to_string(index=False))
    print("\nDecisions logged: data/backtest_decisions/market_core/")
    print(f"Report: {report_dir}")


if __name__ == "__main__":
    main()
