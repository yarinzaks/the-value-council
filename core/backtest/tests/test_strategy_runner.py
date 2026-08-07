"""Unit tests for the strategy runner.

We test config validation, the date-helper function, and the
``Strategy`` ABC contract. End-to-end runs that hit yfinance live are
covered by the sanity check (``test_sanity_buy_and_hold_spy.py``).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from core.backtest.strategy_runner import (
    BuyAndHoldSPY,
    EqualWeightUniverse,
    FundamentalsLookup,
    PriceLookup,
    RunnerConfig,
    Strategy,
    _rebalance_dates,
)
from core.backtest.transaction_costs import ZeroCost


class TestRunnerConfig:
    def test_valid_config(self) -> None:
        cfg = RunnerConfig(
            start_date=date(2020, 1, 1),
            end_date=date(2021, 1, 1),
            initial_cash=10_000,
            cost_model=ZeroCost(),
        )
        assert cfg.rebalance_freq == "monthly"  # default

    def test_inverted_dates_raise(self) -> None:
        with pytest.raises(ValueError):
            RunnerConfig(
                start_date=date(2021, 1, 1),
                end_date=date(2020, 1, 1),
            )

    def test_negative_cash_raises(self) -> None:
        with pytest.raises(ValueError):
            RunnerConfig(
                start_date=date(2020, 1, 1),
                end_date=date(2021, 1, 1),
                initial_cash=-100,
            )


class TestRebalanceDates:
    def _calendar(self) -> pd.DatetimeIndex:
        # Build a Jan-Dec 2020 business-day calendar
        return pd.date_range("2020-01-01", "2020-12-31", freq="B")

    def test_monthly_returns_one_per_month(self) -> None:
        dates = _rebalance_dates(
            date(2020, 1, 1), date(2020, 12, 31), "monthly", self._calendar()
        )
        assert len(dates) == 12

    def test_quarterly_returns_four_per_year(self) -> None:
        dates = _rebalance_dates(
            date(2020, 1, 1), date(2020, 12, 31), "quarterly", self._calendar()
        )
        assert len(dates) == 4

    def test_annual_returns_one_per_year(self) -> None:
        dates = _rebalance_dates(
            date(2020, 1, 1), date(2020, 12, 31), "annual", self._calendar()
        )
        assert len(dates) == 1

    def test_daily_returns_all_business_days(self) -> None:
        dates = _rebalance_dates(
            date(2020, 1, 1), date(2020, 12, 31), "daily", self._calendar()
        )
        assert len(dates) > 250  # 252 business days approx

    def test_unknown_frequency_raises(self) -> None:
        with pytest.raises(ValueError):
            _rebalance_dates(
                date(2020, 1, 1),
                date(2020, 12, 31),
                "biweekly",  # type: ignore[arg-type]
                self._calendar(),
            )


class TestStrategyABC:
    def test_cannot_instantiate_base_class(self) -> None:
        with pytest.raises(TypeError):
            Strategy()  # type: ignore[abstract]

    def test_buy_and_hold_spy_returns_spy(self) -> None:
        strat = BuyAndHoldSPY()
        weights = strat.select(
            date(2020, 1, 2),
            ["AAPL", "MSFT"],
            PriceLookup.__new__(PriceLookup),  # type: ignore[arg-type]
            FundamentalsLookup.__new__(FundamentalsLookup),  # type: ignore[arg-type]
        )
        assert weights == {"SPY": 1.0}

    def test_equal_weight_caps_positions(self) -> None:
        strat = EqualWeightUniverse(max_positions=3)
        # Mock the price lookup to return 1.0 for everything
        class StubLookup:
            def get(self, ticker: str) -> float:
                return 1.0

        universe = ["AAPL", "GOOG", "MSFT", "AMZN", "META"]
        weights = strat.select(
            date(2020, 1, 2), universe, StubLookup(), StubLookup()  # type: ignore[arg-type]
        )
        # Should have 3 positions, alphabetically: AAPL, AMZN, GOOG
        assert len(weights) == 3
        assert {"AAPL", "AMZN", "GOOG"} == set(weights.keys())
        assert all(w == pytest.approx(1 / 3) for w in weights.values())

    def test_equal_weight_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            EqualWeightUniverse(max_positions=0)
