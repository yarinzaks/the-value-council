"""Lynch GARP validation on the **full US market**.

Universe: every SEC active filer with operating XBRL footprint, market
cap ≥ $300M (Lynch's mid-cap focus). Decisions logged per rebalance
to ``data/decisions/peter_lynch/<YYYY-MM-DD>.json``.

Usage::

    .venv/bin/python -m agents.lynch.run_full_market_validation

NOTE — backtest is QUANT-ONLY.

Same rationale as the Buffett agent: running the LLM category
classifier inside a historical backtest creates lookahead bias
(Gemini's training postdates the 2020-2024 window) and is cost-
prohibitive at scale. The quant pipeline implements three of the
six Lynch categories (Slow Grower / Stalwart / Fast Grower) — the
ones with clean quantitative signatures. Cyclicals / Turnarounds /
Asset Plays require the qualitative judgment Lynch did "by hand"
in his Magellan years; they fall to the LLM in live mode.

Academic expectation: 10-15% CAGR, comparable to or modestly better
than SPY. Magellan's 29.2% over 13 years drew heavily on Lynch's
on-the-ground company visits and qualitative moats — neither of
which the agent can fully replicate.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from agents.lynch.garp import PeterLynch
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

logger = get_logger("agents.lynch.run_full_market_validation")


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
    strategy = PeterLynch(
        edgar_cache=cache,
        portfolio_size=30,  # Lynch: 30-50 (1,400 in Magellan)
        min_market_cap=300_000_000.0,
        category_classifier=None,  # quant-only — see module docstring
        decision_logger=decision_logger,
    )
    result = runner.run(strategy)
    report_dir = write_report(result)

    print()
    print("=" * 60)
    print("LYNCH GARP — FULL US MARKET (2020-2024)")
    print("=" * 60)
    print((report_dir / "summary.txt").read_text())
    print("\nAnnual breakdown:")
    print(pd.read_csv(report_dir / "annual_returns.csv").to_string(index=False))
    print(f"\nDecisions logged: data/decisions/peter_lynch/")
    print(f"Report: {report_dir}")


if __name__ == "__main__":
    main()
