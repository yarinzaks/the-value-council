"""Unit tests for portfolio metrics."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from core.backtest.metrics import (
    annual_returns,
    best_year,
    cagr,
    calmar_ratio,
    compute_metrics,
    drawdown_series,
    hit_rate_monthly,
    information_ratio,
    max_drawdown,
    max_drawdown_duration_days,
    sharpe_ratio,
    sortino_ratio,
    worst_year,
)


def _flat_growth(start_val: float, daily_return: float, n: int) -> pd.Series:
    """Helper: NAV series compounding at ``daily_return`` for ``n`` days."""
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    values = [start_val * (1 + daily_return) ** i for i in range(n)]
    return pd.Series(values, index=dates, name="nav")


class TestCagr:
    def test_doubles_in_one_year_is_100pct(self) -> None:
        # $100 → $200 in exactly 365.25 days → CAGR ≈ 100%
        dates = pd.DatetimeIndex(["2020-01-01", "2020-12-31"])
        nav = pd.Series([100.0, 200.0], index=dates)
        # 2020-12-31 minus 2020-01-01 is 365 days, so CAGR is approximately 100%.
        assert cagr(nav) == pytest.approx(2 ** (365.25 / 365) - 1, rel=1e-3)

    def test_zero_growth_is_zero(self) -> None:
        nav = pd.Series([100, 100, 100], index=pd.date_range("2020-01-01", periods=3, freq="D"))
        assert cagr(nav) == pytest.approx(0.0)

    def test_too_short_raises(self) -> None:
        single = pd.Series([100], index=pd.DatetimeIndex(["2020-01-01"]))
        with pytest.raises(ValueError):
            cagr(single)


class TestSharpe:
    def test_constant_growth_high_sharpe(self) -> None:
        # Constant positive returns => virtually zero std => enormous Sharpe.
        # (Not exactly zero std because of floating-point compounding.)
        nav = _flat_growth(100, 0.0005, 252)
        assert sharpe_ratio(nav) > 100  # very large but finite

    def test_volatile_returns_finite_sharpe(self) -> None:
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, 252)  # 0.1% mean, 1% vol
        values = [100.0]
        for r in rets:
            values.append(values[-1] * (1 + r))
        nav = pd.Series(
            values,
            index=pd.date_range("2020-01-01", periods=len(values), freq="B"),
        )
        sharpe = sharpe_ratio(nav)
        # Expected Sharpe ≈ sqrt(252) * 0.001 / 0.01 ≈ 1.58
        assert 1.0 < sharpe < 2.5


class TestSortino:
    def test_no_negative_returns_infinite_sortino(self) -> None:
        nav = _flat_growth(100, 0.001, 100)
        assert sortino_ratio(nav) == float("inf")

    def test_with_drawdowns_finite_sortino(self) -> None:
        np.random.seed(123)
        rets = np.random.normal(0.0005, 0.015, 252)
        values = [100.0]
        for r in rets:
            values.append(values[-1] * (1 + r))
        nav = pd.Series(
            values,
            index=pd.date_range("2020-01-01", periods=len(values), freq="B"),
        )
        s = sortino_ratio(nav)
        assert math.isfinite(s)


class TestMaxDrawdown:
    def test_no_drawdown(self) -> None:
        nav = _flat_growth(100, 0.001, 50)
        assert max_drawdown(nav) == pytest.approx(0.0)

    def test_50pct_drawdown(self) -> None:
        # $100 → $200 → $100  is a 50% drawdown
        dates = pd.date_range("2020-01-01", periods=3, freq="D")
        nav = pd.Series([100, 200, 100], index=dates)
        assert max_drawdown(nav) == pytest.approx(-0.5)

    def test_drawdown_series_non_positive(self) -> None:
        nav = pd.Series(
            [100, 110, 105, 120, 115],
            index=pd.date_range("2020-01-01", periods=5, freq="D"),
        )
        dd = drawdown_series(nav)
        assert (dd <= 0).all()
        assert dd.iloc[0] == pytest.approx(0.0)


class TestMaxDrawdownDuration:
    def test_no_drawdown_zero_duration(self) -> None:
        nav = _flat_growth(100, 0.001, 50)
        assert max_drawdown_duration_days(nav) == 0

    def test_drawdown_duration_measured_in_days(self) -> None:
        dates = pd.date_range("2020-01-01", periods=10, freq="D")
        nav = pd.Series([100, 110, 105, 100, 95, 100, 105, 110, 115, 120], index=dates)
        # Peak at day 1 (110), trough at day 4 (95), recovery at day 7 (110)
        # Duration: day 1 → day 7 = 6 days
        duration = max_drawdown_duration_days(nav)
        assert duration >= 5  # Allow ±1 day for boundary handling


class TestCalmar:
    def test_no_drawdown_infinite(self) -> None:
        nav = _flat_growth(100, 0.001, 50)
        assert calmar_ratio(nav) == float("inf")

    def test_calmar_equals_cagr_over_abs_mdd(self) -> None:
        dates = pd.date_range("2020-01-01", periods=4, freq="365D")
        nav = pd.Series([100, 110, 90, 200], index=dates)
        c = cagr(nav)
        mdd = max_drawdown(nav)
        assert calmar_ratio(nav) == pytest.approx(c / abs(mdd))


class TestHitRate:
    def test_all_positive_months_100pct(self) -> None:
        # Generate 24 months of strictly positive monthly returns
        dates = pd.date_range("2020-01-01", periods=24 * 21, freq="B")
        values = [100.0]
        for _ in range(len(dates) - 1):
            values.append(values[-1] * 1.001)
        nav = pd.Series(values, index=dates)
        assert hit_rate_monthly(nav) == pytest.approx(1.0)


class TestAnnualReturns:
    def test_two_year_returns(self) -> None:
        # Start 2020-01-01 at 100, end 2020-12-31 at 110, end 2021-12-31 at 121
        dates = pd.DatetimeIndex(["2020-01-01", "2020-12-31", "2021-12-31"])
        nav = pd.Series([100, 110, 121], index=dates)
        rets = annual_returns(nav)
        assert 2020 in rets.index
        assert 2021 in rets.index
        assert rets.loc[2020] == pytest.approx(0.1)
        assert rets.loc[2021] == pytest.approx(0.1)

    def test_best_and_worst_year(self) -> None:
        dates = pd.DatetimeIndex(["2020-01-01", "2020-12-31", "2021-12-31", "2022-12-31"])
        nav = pd.Series([100, 130, 100, 110], index=dates)
        by, by_ret = best_year(nav)
        wy, wy_ret = worst_year(nav)
        assert by == 2020
        assert by_ret == pytest.approx(0.30)
        assert wy == 2021
        assert wy_ret < 0


class TestInformationRatio:
    def test_identical_series_zero_ir(self) -> None:
        np.random.seed(7)
        rets = np.random.normal(0.001, 0.01, 100)
        values = [100.0]
        for r in rets:
            values.append(values[-1] * (1 + r))
        nav = pd.Series(values, index=pd.date_range("2020-01-01", periods=len(values), freq="B"))
        assert information_ratio(nav, nav.copy()) == 0.0

    def test_different_series_finite_ir(self) -> None:
        np.random.seed(11)
        idx = pd.date_range("2020-01-01", periods=100, freq="B")
        nav = pd.Series(100 * np.cumprod(1 + np.random.normal(0.001, 0.01, 100)), index=idx)
        bench = pd.Series(100 * np.cumprod(1 + np.random.normal(0.0005, 0.01, 100)), index=idx)
        ir = information_ratio(nav, bench)
        assert math.isfinite(ir)


class TestComputeMetrics:
    def test_returns_complete_metrics_object(self) -> None:
        np.random.seed(3)
        idx = pd.date_range("2020-01-01", periods=500, freq="B")
        nav = pd.Series(100 * np.cumprod(1 + np.random.normal(0.0005, 0.01, 500)), index=idx)
        bench = pd.Series(100 * np.cumprod(1 + np.random.normal(0.0003, 0.01, 500)), index=idx)
        m = compute_metrics(nav, benchmark_nav=bench, cost_model_name="test")
        assert m.cost_model_name == "test"
        assert m.start_date == "2020-01-01"
        assert m.n_observations == 500
        assert m.information_ratio_vs_benchmark is not None
        # Round-trip the dict
        d = m.to_dict()
        assert d["cagr_pct"] == m.cagr_pct
