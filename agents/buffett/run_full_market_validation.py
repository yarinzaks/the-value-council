"""Buffett "Wonderful Business" validation on the **full US market**.

Universe: every SEC active filer with operating XBRL footprint, market
cap ≥ $5B (Buffett's modern size floor). Decisions logged per
rebalance to ``data/decisions/warren_buffett/<YYYY-MM-DD>.json``.

Usage::

    .venv/bin/python -m agents.buffett.run_full_market_validation

NOTE — backtest is QUANT-ONLY by design.

The Buffett agent's hybrid design (Python quant + Gemini moat
analyzer) shines in the LIVE pipeline. Running the LLM inside a
historical backtest is intentionally avoided here because:

  1. **Lookahead bias.** Gemini's training data postdates the
     2020-2024 window; asking it to opine on AAPL on 2020-12-30
     leaks information from later years.
  2. **Cost / runtime.** ~30 candidates × 6 rebalance dates × 4-second
     throttle = 12+ minutes of pure LLM latency, plus daily-quota
     burn on Gemini's free tier.

The quantitative pipeline (six Berkshire Acquisition Criteria + DCF
on Owner Earnings + 15% margin-of-safety floor) is faithful to
playbook §4 and is what produces the validation numbers below. The
LLM moat analyzer is invoked at LIVE rebalance time only (see
``core.live.runner``).

Academic expectation: 10-15% CAGR, comparable to or modestly better
than SPY. Berkshire's 60-year alpha vs S&P came largely from
insurance float, BNSF, and concentrated long holds we cannot
replicate in a 5-year paper backtest.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from agents.buffett.wonderful_business import WarrenBuffett
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

logger = get_logger("agents.buffett.run_full_market_validation")


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
        start_date=date(2019, 12, 30),  # 2020-2024 sanity run
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
    strategy = WarrenBuffett(
        edgar_cache=cache,
        portfolio_size=8,  # Buffett: 6-10 concentrated
        min_market_cap=5_000_000_000.0,
        moat_analyzer=None,  # quant-only — see module docstring
        decision_logger=decision_logger,
    )
    result = runner.run(strategy)
    report_dir = write_report(result)

    print()
    print("=" * 60)
    print("BUFFETT WONDERFUL-BUSINESS — FULL US MARKET (2020-2024)")
    print("=" * 60)
    print((report_dir / "summary.txt").read_text())
    print("\nAnnual breakdown:")
    print(pd.read_csv(report_dir / "annual_returns.csv").to_string(index=False))
    print("\nDecisions logged: data/decisions/warren_buffett/")
    print(f"Report: {report_dir}")


if __name__ == "__main__":
    main()
