"""Tests for the price-derived feature panel.

The failures worth guarding against here are the quiet ones. A feature
that accidentally reads one bar into the future does not raise; it
produces a strategy that looks brilliant and is arithmetic. A forward
return measured over a fixed 21 bars instead of to the next rebalance
does not raise either; it just accumulates a small error in one
direction across 180 months.

These exercise the maths against hand-built matrices, so a regression
shows up as a wrong number rather than as a suspiciously good backtest.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.research.price_panel import (
    DAYS_PER_MONTH,
    MARKET_TICKER,
    MOMENTUM_LOOKBACK,
    MOMENTUM_SKIP,
    PanelSpec,
    benchmark_returns,
    rebalance_dates,
    trend_exposure,
)


def _sessions(n: int, start: str = "2019-01-02") -> pd.DatetimeIndex:
    """``n`` weekday sessions, which is close enough to a trading calendar."""
    return pd.bdate_range(start=start, periods=n)


def _matrix(series: dict[str, np.ndarray], index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(series, index=index, dtype="float64")


class TestPanelSpec:
    def test_start_must_precede_end(self) -> None:
        from datetime import date

        with pytest.raises(ValueError):
            PanelSpec(start=date(2020, 1, 1), end=date(2019, 1, 1))

    def test_the_history_requirement_covers_the_momentum_window(self) -> None:
        from datetime import date

        spec = PanelSpec(start=date(2015, 1, 1), end=date(2016, 1, 1))
        assert spec.min_history >= MOMENTUM_LOOKBACK + MOMENTUM_SKIP

    def test_common_equity_filtering_is_on_by_default(self) -> None:
        from datetime import date

        # Off by default, a low-volatility screen returns preferred
        # series — bonds with equity tickers — and reports their Sharpe
        # as an equity result.
        assert PanelSpec(start=date(2015, 1, 1), end=date(2016, 1, 1)).common_equity_only


class TestRebalanceDates:
    def test_it_returns_the_last_session_of_each_month(self) -> None:
        from datetime import date

        idx = _sessions(90, start="2020-01-01")
        adj = _matrix({MARKET_TICKER: np.ones(90)}, idx)
        spec = PanelSpec(start=date(2020, 1, 1), end=date(2020, 4, 30))
        dates = rebalance_dates(adj, spec)

        for when in dates:
            same_month = idx[(idx.year == when.year) & (idx.month == when.month)]
            assert when == same_month.max()

    def test_every_date_is_one_the_market_actually_traded(self) -> None:
        from datetime import date

        idx = _sessions(60, start="2020-01-01")
        adj = _matrix({MARKET_TICKER: np.ones(60)}, idx)
        spec = PanelSpec(start=date(2020, 1, 1), end=date(2020, 3, 31))
        assert set(rebalance_dates(adj, spec)).issubset(set(idx))

    def test_dates_outside_the_window_are_excluded(self) -> None:
        from datetime import date

        idx = _sessions(200, start="2020-01-01")
        adj = _matrix({MARKET_TICKER: np.ones(200)}, idx)
        spec = PanelSpec(start=date(2020, 3, 1), end=date(2020, 5, 31))
        dates = rebalance_dates(adj, spec)
        assert dates.min() >= pd.Timestamp("2020-03-01")
        assert dates.max() <= pd.Timestamp("2020-05-31")


class TestBenchmarkReturns:
    def test_it_measures_rebalance_to_rebalance(self) -> None:
        idx = _sessions(4)
        adj = _matrix({MARKET_TICKER: np.array([100.0, 110.0, 121.0, 121.0])}, idx)
        r = benchmark_returns(adj, idx)
        assert r.iloc[0] == pytest.approx(0.10)
        assert r.iloc[1] == pytest.approx(0.10)

    def test_the_final_date_has_no_forward_return(self) -> None:
        # There is no next rebalance to measure to, and inventing one
        # would put a fabricated period into every summary.
        idx = _sessions(3)
        adj = _matrix({MARKET_TICKER: np.array([100.0, 110.0, 120.0])}, idx)
        assert bool(np.isnan(benchmark_returns(adj, idx).iloc[-1]))


class TestTrendExposure:
    def test_a_rising_market_stays_fully_invested(self) -> None:
        idx = _sessions(300)
        rising = np.linspace(100.0, 300.0, 300)
        adj = _matrix({MARKET_TICKER: rising}, idx)
        exposure = trend_exposure(adj, idx[-10:], window=100)
        assert (exposure == 1.0).all()

    def test_a_falling_market_goes_defensive(self) -> None:
        idx = _sessions(300)
        falling = np.linspace(300.0, 100.0, 300)
        adj = _matrix({MARKET_TICKER: falling}, idx)
        exposure = trend_exposure(adj, idx[-10:], window=100)
        assert (exposure == 0.0).all()

    def test_the_defensive_level_is_configurable(self) -> None:
        idx = _sessions(300)
        adj = _matrix({MARKET_TICKER: np.linspace(300.0, 100.0, 300)}, idx)
        exposure = trend_exposure(
            adj, idx[-5:], window=100, defensive_exposure=0.4
        )
        assert (exposure == 0.4).all()

    def test_the_signal_uses_no_bar_after_the_rebalance_date(self) -> None:
        # The rule is evaluated on closes up to and including the
        # rebalance and applied to the period after it. If it could see
        # one bar ahead it would sidestep every drawdown perfectly,
        # which is what a look-ahead bug looks like from the outside.
        idx = _sessions(300)
        prices = np.concatenate([np.linspace(100.0, 200.0, 250), np.full(50, 50.0)])
        adj = _matrix({MARKET_TICKER: prices}, idx)

        crash_starts = idx[250]
        exposure = trend_exposure(adj, pd.DatetimeIndex([idx[249]]), window=100)
        # On the last day before the crash the trend is still up, so the
        # rule is invested and takes the hit. Anything else means it saw
        # the crash coming.
        assert exposure.iloc[0] == 1.0
        assert crash_starts > idx[249]


class TestMomentumConstruction:
    def test_the_skip_month_is_one_month(self) -> None:
        assert MOMENTUM_SKIP == DAYS_PER_MONTH

    def test_the_lookback_is_twelve_months(self) -> None:
        assert MOMENTUM_LOOKBACK == 12 * DAYS_PER_MONTH

    def test_momentum_excludes_the_most_recent_month(self) -> None:
        # 12-1, not 12-0. Without the skip a signal measured through the
        # rebalance close and a fill struck at that same close would
        # collect the one-month reversal for free.
        n = MOMENTUM_LOOKBACK + MOMENTUM_SKIP + 5
        idx = _sessions(n)
        prices = np.ones(n)
        # A spike confined to the skipped window must not move the signal.
        prices[-MOMENTUM_SKIP:] = 5.0
        adj = _matrix({"X": prices}, idx)

        mom = adj.shift(MOMENTUM_SKIP) / adj.shift(MOMENTUM_SKIP + MOMENTUM_LOOKBACK) - 1.0
        assert mom.iloc[-1, 0] == pytest.approx(0.0)
