"""Klarman Margin-of-Safety validation on the **full US market**.

Universe: every SEC active filer with operating XBRL footprint, market
cap ≥ $500M. Decisions logged per rebalance to
``data/decisions/seth_klarman/<YYYY-MM-DD>.json``.

Usage::

    .venv/bin/python -m agents.klarman.run_full_market_validation

NOTE — backtest is QUANT-ONLY (no LLM downside analysis).

Same rationale as the other hybrid agents: Gemini in a backtest
creates lookahead bias and burns free-tier quota. The quant pipeline
implements the playbook §4.2 30%-MoS floor on a conservative DCF
intrinsic value with cash-as-residual sizing per §4.4.

Klarman's qualitative cores (what-could-go-wrong scenarios, NAV /
sum-of-parts / recovery analyses, capital structure analysis,
catalyst identification) require the LLM and run in live mode only.

Academic expectation: 10-15% CAGR. Klarman's Baupost did ~16% over
30 years across ALL asset classes, with significant returns from
distressed debt that an equity-only paper book cannot replicate.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from agents.klarman.margin_of_safety import SethKlarman
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

logger = get_logger("agents.klarman.run_full_market_validation")


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
    strategy = SethKlarman(
        edgar_cache=cache,
        min_market_cap=500_000_000.0,
        max_portfolio_size=20,  # Klarman §6.1: 12-25 paper-portfolio target
        downside_analyzer=None,  # quant-only — see module docstring
        decision_logger=decision_logger,
    )
    result = runner.run(strategy)
    report_dir = write_report(result)

    print()
    print("=" * 60)
    print("KLARMAN MARGIN-OF-SAFETY — FULL US MARKET (2020-2024)")
    print("=" * 60)
    print((report_dir / "summary.txt").read_text())
    print("\nAnnual breakdown:")
    print(pd.read_csv(report_dir / "annual_returns.csv").to_string(index=False))
    print("\nDeployment history:")
    for sel in strategy.selections_to_records():
        print(
            f"  {sel['as_of']}: qualifying={sel['candidates_after_screen']:>3d} "
            f"deploy={sel['deployed_fraction']:.0%} "
            f"positions={len(sel['selected_tickers']):>2d} "
            f"top MoS={sel['top_mos_pct'] or 0:.1f}%"
        )
    print(f"\nDecisions logged: data/decisions/seth_klarman/")
    print(f"Report: {report_dir}")


if __name__ == "__main__":
    main()
