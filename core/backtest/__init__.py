"""Backtest engine for The Value Council.

Production-quality, point-in-time-correct, survivorship-bias-free
backtesting framework. See ``docs/backtest_architecture.md`` for the
full design rationale.

Public API::

    from core.backtest import (
        BacktestRunner,
        Strategy,
        BuyAndHoldSPY,
        EqualWeightUniverse,
        compute_metrics,
        write_report,
    )
"""

from .metrics import (
    PortfolioMetrics,
    annual_returns,
    cagr,
    calmar_ratio,
    compute_metrics,
    drawdown_series,
    hit_rate_monthly,
    information_ratio,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)
from .portfolio import BacktestPortfolio, Holding
from .reporting import write_report
from .strategy_runner import (
    BacktestResult,
    BacktestRunner,
    BuyAndHoldSPY,
    EqualWeightUniverse,
    RunnerConfig,
    Strategy,
)
from .transaction_costs import (
    CostModel,
    PercentageCost,
    PerShareCost,
    ZeroCost,
)

__all__ = [
    # strategy_runner
    "BacktestResult",
    "BacktestRunner",
    "BuyAndHoldSPY",
    "EqualWeightUniverse",
    "RunnerConfig",
    "Strategy",
    # portfolio
    "BacktestPortfolio",
    "Holding",
    # transaction_costs
    "CostModel",
    "PercentageCost",
    "PerShareCost",
    "ZeroCost",
    # metrics
    "PortfolioMetrics",
    "annual_returns",
    "cagr",
    "calmar_ratio",
    "compute_metrics",
    "drawdown_series",
    "hit_rate_monthly",
    "information_ratio",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    # reporting
    "write_report",
]
