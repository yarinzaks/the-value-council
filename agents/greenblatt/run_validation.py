"""Validation backtest for the Greenblatt Magic Formula.

Runs the strategy against real market data (yfinance prices + FMP
fundamentals) on a curated 30-name S&P 100 subset over the past
4 calendar years (2021-2024). The window is the maximum the FMP
free tier supports (5 fiscal years of statements).

Output:
- Full report in ``data/backtest_results/<run_id>/``
- Stdout summary including alpha vs SPY

Usage::

    .venv/bin/python -m agents.greenblatt.run_validation
"""

from __future__ import annotations

from datetime import date

from agents.greenblatt.magic_formula import MagicFormula
from core.backtest.data_loader import PriceDataLoader
from core.backtest.fmp_adapter import FMPAdapter
from core.backtest.point_in_time import PointInTimeLoader
from core.backtest.reporting import write_report
from core.backtest.strategy_runner import (
    BacktestRunner,
    RunnerConfig,
)
from core.backtest.transaction_costs import PercentageCost
from core.backtest.universe import Change, HistoricalUniverse
from core.logger import get_logger

logger = get_logger("agents.greenblatt.run_validation")


# Curated subset of S&P 100 — large, well-known, FMP-supported tickers
# spanning multiple sectors (excluding obvious financials/utilities so
# we have enough survivors after the Magic Formula filters).
VALIDATION_UNIVERSE: list[str] = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "TSLA", "AVGO", "ORCL", "CRM",
    "WMT", "COST", "HD", "MCD", "NKE",
    "PEP", "KO", "PG", "DIS", "NFLX",
    "PFE", "MRK", "JNJ", "UNH", "ABBV",
    "XOM", "CVX", "CAT", "DE", "GE",
]


def main() -> None:
    universe = HistoricalUniverse(
        current_constituents=VALIDATION_UNIVERSE,
        change_log=[],  # static — these are all current S&P 500 names
    )

    cfg = RunnerConfig(
        start_date=date(2021, 1, 4),
        end_date=date(2024, 12, 31),
        initial_cash=10_000.0,
        rebalance_freq="annual",
        benchmark_ticker="SPY",
        cost_model=PercentageCost(0.001),  # 10 bps per side
        use_universe=True,
        use_fundamentals=True,
    )

    price_loader = PriceDataLoader()
    pit_loader = PointInTimeLoader(adapter=FMPAdapter())

    runner = BacktestRunner(
        cfg,
        price_loader=price_loader,
        pit_loader=pit_loader,
        universe=universe,
    )
    strategy = MagicFormula(
        portfolio_size=10,  # 10 of 30 candidates
        min_market_cap=1_000_000_000.0,
    )
    result = runner.run(strategy)
    report_dir = write_report(result)

    print(f"\nReport written to: {report_dir}\n")
    print((report_dir / "summary.txt").read_text())

    print("\nSelections per rebalance:")
    for sel in strategy.selection_history:
        print(
            f"  {sel.as_of}: {sel.candidates_after_filters}/{sel.universe_size} "
            f"survivors → {sel.selected_tickers}"
        )


if __name__ == "__main__":
    main()
