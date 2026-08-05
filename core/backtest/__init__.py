"""Backtest engine for The Value Council.

Production-quality and point-in-time-correct: no component reads a
filing or a price stamped after the date it is asked about.

Survivorship bias is handled per universe, not by the framework, and
the two shipped universes differ. :class:`SP500Universe` is free of it
— it walks the index change log backward, so removed members reappear
at the dates they were members. :class:`FullMarketUniverse`, which the
live runner uses, is **not**: its roster is the SEC's current
registrant list, so anything delisted before the prefetch is missing at
every historical date, and its backtest returns are an upper bound. The
warning in :mod:`core.backtest.full_market_universe` has the evidence.

See ``docs/backtest_architecture.md`` for the full design rationale.

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
    # portfolio
    "BacktestPortfolio",
    # strategy_runner
    "BacktestResult",
    "BacktestRunner",
    "BuyAndHoldSPY",
    # transaction_costs
    "CostModel",
    "EqualWeightUniverse",
    "Holding",
    "PerShareCost",
    "PercentageCost",
    # metrics
    "PortfolioMetrics",
    "RunnerConfig",
    "Strategy",
    "ZeroCost",
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
