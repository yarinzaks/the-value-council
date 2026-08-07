"""Unit tests for the strategy runner.

We test config validation, the date-helper function, and the
``Strategy`` ABC contract. End-to-end runs that hit yfinance live are
covered by the sanity check (``test_sanity_buy_and_hold_spy.py``).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from core.backtest.point_in_time import PointInTimeError, PointInTimeFinancials
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


class TestFundamentalsLookupSurvivesABadFiling:
    """One unparseable 10-Q must not end a five-hour backtest.

    ``PointInTimeLoader.get_financials`` raises when a filing exists but
    parses to nothing — correct there, since ``None`` would be
    indistinguishable from a company that never filed. At the screening
    seam it is fatal: Buffett's 2026-08-07 run died at the fourth
    rebalance on FCHS, one name out of 6,601.
    """

    class _Raising:
        """Stands in for a loader whose adapter cannot parse FCHS."""

        def __init__(self) -> None:
            self.asked: list[str] = []

        def get_financials(
            self, ticker: str, as_of: date
        ) -> PointInTimeFinancials | None:
            self.asked.append(ticker)
            if ticker == "FCHS":
                raise PointInTimeError(
                    "FCHS: 10-Q filed 2018-11-07 yielded no usable data"
                )
            return None

    def test_a_parse_failure_reads_as_no_data(self) -> None:
        lookup = FundamentalsLookup(
            self._Raising(),  # type: ignore[arg-type]
            date(2022, 12, 30),
        )

        assert lookup.get("FCHS") is None

    def test_the_screen_continues_past_it(self) -> None:
        loader = self._Raising()
        lookup = FundamentalsLookup(loader, date(2022, 12, 30))  # type: ignore[arg-type]

        for ticker in ("AAPL", "FCHS", "MSFT"):
            lookup.get(ticker)

        # Before the fix the third call never happened.
        assert loader.asked == ["AAPL", "FCHS", "MSFT"]

    def test_the_dropped_ticker_is_recorded_not_swallowed(self) -> None:
        lookup = FundamentalsLookup(
            self._Raising(),  # type: ignore[arg-type]
            date(2022, 12, 30),
        )

        lookup.get("AAPL")
        lookup.get("FCHS")

        # AAPL returned None because it has no filing — a different
        # thing, and it must not show up here.
        assert lookup.unparseable == {"FCHS"}

    def test_no_loader_still_returns_none(self) -> None:
        lookup = FundamentalsLookup(None, date(2022, 12, 30))

        assert lookup.get("FCHS") is None
        assert lookup.unparseable == set()
