"""Fisher quality-growth validation on the **full US market**.

Universe: every SEC active filer with operating XBRL footprint, market
cap ≥ $1B (Fisher mid- to large-cap tilt). Decisions logged per
rebalance to ``data/backtest_decisions/philip_fisher/<YYYY-MM-DD>.json``.

Usage::

    .venv/bin/python -m agents.fisher.run_full_market_validation

NOTE — backtest is QUANT-ONLY (no LLM scuttlebutt analysis).

Same rationale as the other hybrid agents: Gemini in a backtest
creates lookahead bias and burns free-tier quota. The quant pipeline
implements the 5 quant-checkable Fisher points (revenue growth,
R&D effectiveness, operating margin, margin trend, share-count
discipline) and tier-classifies into Tier A (5/5) or Tier B (4/5).

Fisher's qualitative cores (Points 4, 7, 8, 9, 10, 11, 12, 14, 15
— management quality, sales organization, labor relations,
communication candor, integrity) require the LLM scuttlebutt
analyzer and run in live mode only.

Academic expectation: 10-15% CAGR. Fisher's actual fund-level
returns are not publicly documented, but his Motorola hold alone
generated 2,000-3,000x over 49 years — a lesson in compounding that
a 5-year paper backtest cannot fully reproduce.
"""

from __future__ import annotations

import pandas as pd

from agents.fisher.quality_growth import PhilipFisher
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

logger = get_logger("agents.fisher.run_full_market_validation")


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
    strategy = PhilipFisher(
        edgar_cache=cache,
        min_market_cap=1_000_000_000.0,
        max_portfolio_size=15,  # playbook §6.1: 14-30, lower end for $10K
        scuttlebutt_analyzer=None,  # quant-only — see module docstring
        decision_logger=decision_logger,
    )
    result = runner.run(strategy)
    report_dir = write_report(result)

    print()
    print("=" * 60)
    print("FISHER QUALITY-GROWTH — FULL US MARKET (2020-2024)")
    print("=" * 60)
    print((report_dir / "summary.txt").read_text())
    print("\nAnnual breakdown:")
    print(pd.read_csv(report_dir / "annual_returns.csv").to_string(index=False))
    print("\nSelection history:")
    for sel in strategy.selections_to_records():
        print(
            f"  {sel['as_of']}: positions={len(sel['selected_tickers']):>2d} "
            f"(A={sel['tier_a_count']:>2d}, B={sel['tier_b_count']:>2d}) "
            f"deploy={sel['deployed_fraction']:.0%}"
        )
    print("\nDecisions logged: data/backtest_decisions/philip_fisher/")
    print(f"Report: {report_dir}")


if __name__ == "__main__":
    main()
