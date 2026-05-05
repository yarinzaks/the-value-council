"""Integration test — sanity check buy-and-hold SPY 2010-2024.

Validates the entire backtest pipeline end-to-end against real
yfinance data. The engine's NAV result must match SPY's actual total
return over the same window within 0.5%.

Marked ``@pytest.mark.integration`` because it hits the network.
Skipped automatically when the network is unavailable.
"""

from __future__ import annotations

from datetime import date

import pytest

from core.backtest.data_loader import PriceDataLoader
from core.backtest.strategy_runner import (
    BacktestRunner,
    BuyAndHoldSPY,
    RunnerConfig,
)
from core.backtest.transaction_costs import ZeroCost


@pytest.mark.integration
def test_buy_and_hold_spy_matches_actual_return() -> None:
    """The full pipeline must reproduce SPY total return within 0.5%."""
    loader = PriceDataLoader()

    # Get the actual SPY total return for the window
    spy = loader.get_adj_close("SPY", date(2010, 1, 4), date(2024, 12, 31))
    assert not spy.empty, "yfinance returned no SPY data — check network/auth"

    actual_total_return = float(spy.iloc[-1] / spy.iloc[0] - 1.0)

    # Run the backtest
    cfg = RunnerConfig(
        start_date=date(2010, 1, 4),
        end_date=date(2024, 12, 31),
        initial_cash=10_000.0,
        rebalance_freq="annual",  # rebalance once a year (irrelevant for buy-and-hold)
        benchmark_ticker="SPY",
        cost_model=ZeroCost(),  # no costs to compare cleanly
        use_universe=False,
        use_fundamentals=False,
    )
    runner = BacktestRunner(cfg, price_loader=loader, universe=None)
    result = runner.run(BuyAndHoldSPY())

    final_nav = float(result.nav_series.iloc[-1])
    engine_total_return = (final_nav / cfg.initial_cash) - 1.0

    # Within 0.5% absolute return difference
    diff = abs(engine_total_return - actual_total_return)
    assert diff < 0.005, (
        f"engine_return={engine_total_return:.4f}, "
        f"actual={actual_total_return:.4f}, diff={diff:.4f}"
    )
