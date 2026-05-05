"""Portfolio performance metrics for backtest results.

All metrics operate on a NAV time series indexed by date. Inputs are
``pandas.Series`` with a :class:`pandas.DatetimeIndex` and float
values representing portfolio net asset value.

Standard finance conventions:

* **Returns** are simple period-over-period (NAV[t] / NAV[t-1] − 1).
* **Risk-free rate** defaults to 0% — backtests should compare to a
  benchmark, not to a risk-free instrument.
* **Annualization** uses ~252 trading days for daily returns, 12 for
  monthly, 4 for quarterly, 1 for annual.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
import pandas as pd

Frequency = Literal["daily", "monthly", "quarterly", "annual"]
_PERIODS_PER_YEAR: dict[Frequency, int] = {
    "daily": 252,
    "monthly": 12,
    "quarterly": 4,
    "annual": 1,
}


def _validate_nav(nav: pd.Series) -> None:
    if not isinstance(nav, pd.Series):
        raise TypeError(f"nav must be a pandas.Series; got {type(nav).__name__}")
    if not isinstance(nav.index, pd.DatetimeIndex):
        raise TypeError("nav.index must be a DatetimeIndex")
    if len(nav) < 2:
        raise ValueError(f"nav must have at least 2 observations; got {len(nav)}")
    if nav.isna().any():
        raise ValueError("nav contains NaN values")
    if (nav <= 0).any():
        raise ValueError("nav must be strictly positive")


def _periods_per_year(frequency: Frequency) -> int:
    if frequency not in _PERIODS_PER_YEAR:
        raise ValueError(f"unknown frequency {frequency!r}")
    return _PERIODS_PER_YEAR[frequency]


def returns_from_nav(nav: pd.Series) -> pd.Series:
    """Compute period-over-period simple returns from a NAV series."""
    _validate_nav(nav)
    return nav.pct_change().dropna()


def cagr(nav: pd.Series) -> float:
    """Compound Annual Growth Rate over the full period.

    Uses the actual elapsed time between first and last observations,
    not assumed period count. This handles series with irregular
    sampling (e.g., business-day NAV that skips weekends/holidays).
    """
    _validate_nav(nav)
    start, end = nav.index[0], nav.index[-1]
    years = (end - start).days / 365.25
    if years <= 0:
        raise ValueError(f"non-positive elapsed time: start={start}, end={end}")
    total_return = nav.iloc[-1] / nav.iloc[0]
    return float(total_return ** (1.0 / years) - 1.0)


def sharpe_ratio(
    nav: pd.Series,
    risk_free_rate: float = 0.0,
    frequency: Frequency = "daily",
) -> float:
    """Annualized Sharpe ratio.

    Args:
        nav: NAV time series.
        risk_free_rate: Annualized risk-free rate (e.g., 0.04 for 4%).
        frequency: Sampling frequency of ``nav``.
    """
    rets = returns_from_nav(nav)
    if rets.std() == 0:
        return 0.0
    n = _periods_per_year(frequency)
    excess = rets - (risk_free_rate / n)
    return float(np.sqrt(n) * excess.mean() / rets.std())


def sortino_ratio(
    nav: pd.Series,
    risk_free_rate: float = 0.0,
    frequency: Frequency = "daily",
) -> float:
    """Annualized Sortino ratio (Sharpe using downside deviation only).

    Downside deviation is computed against zero — i.e., we treat any
    negative return as a "bad" outcome regardless of the risk-free
    rate. This is the most common convention.
    """
    rets = returns_from_nav(nav)
    n = _periods_per_year(frequency)
    excess = rets - (risk_free_rate / n)
    downside = rets[rets < 0]
    if len(downside) == 0:
        return float("inf")
    downside_dev = np.sqrt((downside ** 2).mean())
    if downside_dev == 0:
        return 0.0
    return float(np.sqrt(n) * excess.mean() / downside_dev)


def drawdown_series(nav: pd.Series) -> pd.Series:
    """Series of drawdown percentages (negative values, 0 at peaks)."""
    _validate_nav(nav)
    running_max = nav.cummax()
    return nav / running_max - 1.0


def max_drawdown(nav: pd.Series) -> float:
    """Maximum (most negative) drawdown over the series.

    Returns:
        A negative float (or 0.0 if the series only ever went up).
    """
    return float(drawdown_series(nav).min())


def max_drawdown_duration_days(nav: pd.Series) -> int:
    """Longest peak-to-recovery duration in calendar days.

    Drawdowns that have not yet recovered are measured from the peak
    to the end of the series.
    """
    _validate_nav(nav)
    dd = drawdown_series(nav)
    running_max = nav.cummax()
    # Identify which "peak" each observation belongs to by tracking
    # when running_max changes.
    peak_changes = running_max.ne(running_max.shift())
    peak_groups = peak_changes.cumsum()
    longest = 0
    for _, group in nav.groupby(peak_groups):
        if (dd.loc[group.index] < 0).any():
            duration = (group.index[-1] - group.index[0]).days
            longest = max(longest, duration)
    return longest


def calmar_ratio(nav: pd.Series) -> float:
    """CAGR divided by absolute max drawdown."""
    mdd = max_drawdown(nav)
    if mdd == 0:
        return float("inf")
    return cagr(nav) / abs(mdd)


def hit_rate_monthly(nav: pd.Series) -> float:
    """Fraction of months with positive return.

    Resamples NAV to month-end and computes month-over-month returns.
    """
    _validate_nav(nav)
    monthly = nav.resample("ME").last().dropna()
    if len(monthly) < 2:
        return 0.0
    monthly_rets = monthly.pct_change().dropna()
    return float((monthly_rets > 0).mean())


def annual_returns(nav: pd.Series) -> pd.Series:
    """Calendar-year returns from a NAV series.

    For partial start/end years we use the available NAV at year
    boundaries (or the closest available trading day).
    """
    _validate_nav(nav)
    yearly = nav.resample("YE").last()
    # Prepend the very first NAV so the first year's return is computable.
    first = pd.Series([nav.iloc[0]], index=[nav.index[0]])
    yearly = pd.concat([first, yearly]).sort_index()
    yearly = yearly[~yearly.index.duplicated(keep="first")]
    rets = yearly.pct_change().dropna()
    rets.index = rets.index.year
    rets.index.name = "year"
    rets.name = "return"
    return rets


def best_year(nav: pd.Series) -> tuple[int, float]:
    """Return (year, return) of the best calendar year."""
    rets = annual_returns(nav)
    if rets.empty:
        raise ValueError("not enough data to compute calendar-year returns")
    year = int(rets.idxmax())
    return year, float(rets.max())


def worst_year(nav: pd.Series) -> tuple[int, float]:
    """Return (year, return) of the worst calendar year."""
    rets = annual_returns(nav)
    if rets.empty:
        raise ValueError("not enough data to compute calendar-year returns")
    year = int(rets.idxmin())
    return year, float(rets.min())


def information_ratio(
    nav: pd.Series,
    benchmark_nav: pd.Series,
    frequency: Frequency = "daily",
) -> float:
    """Annualized information ratio vs. a benchmark.

    Aligns the two series on shared dates before computing.
    """
    _validate_nav(nav)
    _validate_nav(benchmark_nav)
    aligned = pd.concat([nav, benchmark_nav], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        raise ValueError("nav and benchmark have fewer than 2 shared dates")
    aligned.columns = ["port", "bench"]
    port_rets = aligned["port"].pct_change().dropna()
    bench_rets = aligned["bench"].pct_change().dropna()
    active = port_rets - bench_rets
    if active.std() == 0:
        return 0.0
    n = _periods_per_year(frequency)
    return float(np.sqrt(n) * active.mean() / active.std())


@dataclass(frozen=True)
class PortfolioMetrics:
    """Container for a complete metrics report on a backtest run."""

    cagr_pct: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    max_drawdown_duration_days: int
    calmar: float
    hit_rate_monthly_pct: float
    best_year: int
    best_year_return_pct: float
    worst_year: int
    worst_year_return_pct: float
    information_ratio_vs_benchmark: float | None
    total_return_pct: float
    n_observations: int
    start_date: str
    end_date: str
    frequency: Frequency
    cost_model_name: str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly dict representation."""
        return asdict(self)


def compute_metrics(
    nav: pd.Series,
    *,
    benchmark_nav: pd.Series | None = None,
    risk_free_rate: float = 0.0,
    frequency: Frequency = "daily",
    cost_model_name: str = "unknown",
) -> PortfolioMetrics:
    """Compute the full set of portfolio metrics.

    Args:
        nav: Portfolio NAV time series.
        benchmark_nav: Optional benchmark NAV for IR; must overlap.
        risk_free_rate: Annualized risk-free rate.
        frequency: Sampling frequency of nav (and benchmark).
        cost_model_name: Free-text label included in the result.
    """
    _validate_nav(nav)
    by, by_ret = best_year(nav)
    wy, wy_ret = worst_year(nav)
    ir: float | None
    if benchmark_nav is not None:
        ir = information_ratio(nav, benchmark_nav, frequency=frequency)
    else:
        ir = None
    return PortfolioMetrics(
        cagr_pct=cagr(nav) * 100,
        sharpe=sharpe_ratio(nav, risk_free_rate, frequency),
        sortino=sortino_ratio(nav, risk_free_rate, frequency),
        max_drawdown_pct=max_drawdown(nav) * 100,
        max_drawdown_duration_days=max_drawdown_duration_days(nav),
        calmar=calmar_ratio(nav),
        hit_rate_monthly_pct=hit_rate_monthly(nav) * 100,
        best_year=by,
        best_year_return_pct=by_ret * 100,
        worst_year=wy,
        worst_year_return_pct=wy_ret * 100,
        information_ratio_vs_benchmark=ir,
        total_return_pct=float(nav.iloc[-1] / nav.iloc[0] - 1.0) * 100,
        n_observations=len(nav),
        start_date=nav.index[0].strftime("%Y-%m-%d"),
        end_date=nav.index[-1].strftime("%Y-%m-%d"),
        frequency=frequency,
        cost_model_name=cost_model_name,
    )


__all__ = [
    "Frequency",
    "PortfolioMetrics",
    "annual_returns",
    "best_year",
    "cagr",
    "calmar_ratio",
    "compute_metrics",
    "drawdown_series",
    "hit_rate_monthly",
    "information_ratio",
    "max_drawdown",
    "max_drawdown_duration_days",
    "returns_from_nav",
    "sharpe_ratio",
    "sortino_ratio",
    "worst_year",
]
