"""Marks Cycle-Value validation on the **full US market**.

Universe: every SEC active filer with operating XBRL footprint, market
cap ≥ $500M. Decisions logged per rebalance to
``data/decisions/howard_marks/<YYYY-MM-DD>.json``.

Usage::

    .venv/bin/python -m agents.marks.run_full_market_validation

NOTE — backtest is QUANT-ONLY (no LLM second-level analysis).

Same rationale as the Buffett / Lynch agents: running Gemini inside a
backtest creates lookahead bias and is cost-prohibitive at scale.
The quant pipeline implements:

  * Universe-wide market temperature assessment per rebalance date
    (5 signals → Cold / Cool / Neutral / Warm / Hot).
  * Cycle-adjusted ranking with posture-specific weights and quality
    floors (deeper-value tilt in Cold; raise-the-bar in Hot).
  * Posture-driven deployment intensity per playbook §6.2 — fewer
    positions and more cash as the pendulum swings toward Hot.

Marks's qualitative cores (second-level thinking, scenario-based
risk-adjusted return, "I Don't Know" check, distressed-debt-specific
analysis) require the LLM and run in live mode only.

Academic expectation: 10-15% CAGR. Marks's Oaktree distressed-debt
funds did ~23% over 25+ years on instruments we cannot trade in an
equity-only paper book. The headline target here is conservative.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from agents.marks.cycle_value import HowardMarks
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
from core.data.edgar_cache import EdgarCache
from core.data.fundamentals_fetcher import (
    CachedEdgarAdapter,
    FundamentalsFetcher,
    FundamentalsFetcherConfig,
)
from core.logger import get_logger

logger = get_logger("agents.marks.run_full_market_validation")


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

    decision_logger = DecisionLogger()

    cfg = RunnerConfig(
        start_date=date(2019, 12, 30),
        end_date=date(2024, 12, 31),
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
    strategy = HowardMarks(
        edgar_cache=cache,
        min_market_cap=500_000_000.0,
        second_level_analyzer=None,  # quant-only — see module docstring
        decision_logger=decision_logger,
    )
    result = runner.run(strategy)
    report_dir = write_report(result)

    print()
    print("=" * 60)
    print("MARKS CYCLE-VALUE — FULL US MARKET (2020-2024)")
    print("=" * 60)
    print((report_dir / "summary.txt").read_text())
    print("\nAnnual breakdown:")
    print(pd.read_csv(report_dir / "annual_returns.csv").to_string(index=False))
    print("\nPosture history:")
    for sel in strategy.selections_to_records():
        print(
            f"  {sel['as_of']}: posture={sel['posture']:>8s} "
            f"score={sel['temperature_score']:+.1f} "
            f"deploy={sel['deployed_fraction']:.0%} "
            f"positions={len(sel['selected_tickers'])}"
        )
    print(f"\nDecisions logged: data/decisions/howard_marks/")
    print(f"Report: {report_dir}")


if __name__ == "__main__":
    main()
