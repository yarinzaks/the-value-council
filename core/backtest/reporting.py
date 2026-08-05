"""Backtest result reporting — CSV, charts, and human-readable summaries.

Outputs go to ``data/backtest_results/<run_id>/`` by default.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

# Use non-interactive backend for headless / CI environments
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from core.logger import get_logger

from .metrics import (
    PortfolioMetrics,
    annual_returns,
    compute_metrics,
    drawdown_series,
)
from .strategy_runner import BacktestResult

logger = get_logger("core.backtest.reporting")

from core.paths import backtest_results_dir as _backtest_results_dir

DEFAULT_RESULTS_DIR = _backtest_results_dir()


def _write_nav_csv(result: BacktestResult, out_path: Path) -> None:
    df = pd.DataFrame(
        {
            "nav": result.nav_series,
            "benchmark_nav": result.benchmark_nav_series,
        }
    )
    df.index.name = "date"
    df.to_csv(out_path, float_format="%.4f")


def _write_trades_csv(result: BacktestResult, out_path: Path) -> None:
    if not result.trades:
        out_path.write_text("trade_date,ticker,side,shares,price,notional,cost,cash_after\n")
        return
    df = pd.DataFrame(
        [
            {
                "trade_date": t.trade_date.isoformat(),
                "ticker": t.ticker,
                "side": t.side,
                "shares": t.shares,
                "price": t.price,
                "notional": t.notional,
                "cost": t.cost,
                "cash_after": t.cash_after,
            }
            for t in result.trades
        ]
    )
    df.to_csv(out_path, index=False, float_format="%.4f")


def _write_annual_csv(result: BacktestResult, out_path: Path) -> None:
    rets = annual_returns(result.nav_series)
    bench_rets = annual_returns(result.benchmark_nav_series)
    df = pd.concat(
        {
            "strategy_return_pct": rets * 100,
            "benchmark_return_pct": bench_rets * 100,
            "alpha_pct": (rets - bench_rets) * 100,
        },
        axis=1,
    )
    df.index.name = "year"
    df.to_csv(out_path, float_format="%.2f")


def _draw_drawdown_chart(result: BacktestResult, out_path: Path) -> None:
    dd = drawdown_series(result.nav_series) * 100
    bench_dd = drawdown_series(result.benchmark_nav_series) * 100
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    # Top panel: NAV
    axes[0].plot(
        result.nav_series.index,
        result.nav_series.values,
        label=result.strategy_name,
        linewidth=1.2,
    )
    axes[0].plot(
        result.benchmark_nav_series.index,
        result.benchmark_nav_series.values,
        label=f"benchmark {result.config.benchmark_ticker}",
        linewidth=1.2,
        alpha=0.7,
    )
    axes[0].set_ylabel("NAV (USD)")
    axes[0].set_title(f"Backtest: {result.strategy_name}  ({result.run_id})")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend()
    # Bottom panel: drawdown
    axes[1].fill_between(dd.index, dd.values, 0, alpha=0.3, color="C0", label="strategy")
    axes[1].fill_between(
        bench_dd.index, bench_dd.values, 0, alpha=0.2, color="C1", label="benchmark"
    )
    axes[1].set_ylabel("Drawdown (%)")
    axes[1].set_xlabel("Date")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()
    axes[1].xaxis.set_major_locator(mdates.YearLocator(2))
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _summary_text(metrics: PortfolioMetrics, bench_metrics: PortfolioMetrics) -> str:
    lines = [
        "=" * 60,
        "BACKTEST SUMMARY",
        "=" * 60,
        f"Period:           {metrics.start_date}  →  {metrics.end_date}",
        f"Observations:     {metrics.n_observations}",
        f"Cost model:       {metrics.cost_model_name}",
        "",
        f"{'Metric':<35}{'Strategy':>14}{'Benchmark':>14}",
        "-" * 63,
        f"{'Total return (%)':<35}{metrics.total_return_pct:>14.2f}{bench_metrics.total_return_pct:>14.2f}",
        f"{'CAGR (%)':<35}{metrics.cagr_pct:>14.2f}{bench_metrics.cagr_pct:>14.2f}",
        f"{'Sharpe ratio':<35}{metrics.sharpe:>14.3f}{bench_metrics.sharpe:>14.3f}",
        f"{'Sortino ratio':<35}{metrics.sortino:>14.3f}{bench_metrics.sortino:>14.3f}",
        f"{'Calmar ratio':<35}{metrics.calmar:>14.3f}{bench_metrics.calmar:>14.3f}",
        f"{'Max drawdown (%)':<35}{metrics.max_drawdown_pct:>14.2f}{bench_metrics.max_drawdown_pct:>14.2f}",
        f"{'Max DD duration (days)':<35}{metrics.max_drawdown_duration_days:>14d}{bench_metrics.max_drawdown_duration_days:>14d}",
        f"{'Hit rate, monthly (%)':<35}{metrics.hit_rate_monthly_pct:>14.2f}{bench_metrics.hit_rate_monthly_pct:>14.2f}",
        f"{'Best year':<35}{metrics.best_year:>14d}{bench_metrics.best_year:>14d}",
        f"{'Best year return (%)':<35}{metrics.best_year_return_pct:>14.2f}{bench_metrics.best_year_return_pct:>14.2f}",
        f"{'Worst year':<35}{metrics.worst_year:>14d}{bench_metrics.worst_year:>14d}",
        f"{'Worst year return (%)':<35}{metrics.worst_year_return_pct:>14.2f}{bench_metrics.worst_year_return_pct:>14.2f}",
    ]
    if metrics.information_ratio_vs_benchmark is not None:
        lines.append(
            f"{'Information ratio vs benchmark':<35}{metrics.information_ratio_vs_benchmark:>14.3f}"
        )
    lines.append("=" * 60)
    return "\n".join(lines)


def write_report(
    result: BacktestResult,
    *,
    out_dir: Path | None = None,
    risk_free_rate: float = 0.0,
) -> Path:
    """Write a complete report (CSVs, chart, summary) for a backtest result.

    Returns the directory the report was written to.
    """
    base = out_dir or DEFAULT_RESULTS_DIR
    run_dir = base / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    metrics = compute_metrics(
        result.nav_series,
        benchmark_nav=result.benchmark_nav_series,
        risk_free_rate=risk_free_rate,
        cost_model_name=result.config.cost_model.name(),
    )
    bench_metrics = compute_metrics(
        result.benchmark_nav_series,
        risk_free_rate=risk_free_rate,
        cost_model_name="N/A (benchmark)",
    )

    _write_nav_csv(result, run_dir / "nav.csv")
    _write_trades_csv(result, run_dir / "orders.csv")
    _write_annual_csv(result, run_dir / "annual_returns.csv")
    _draw_drawdown_chart(result, run_dir / "drawdown.png")

    summary = _summary_text(metrics, bench_metrics)
    (run_dir / "summary.txt").write_text(summary + "\n")
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "run_id": result.run_id,
                "strategy_name": result.strategy_name,
                "config": {
                    "start_date": result.config.start_date.isoformat(),
                    "end_date": result.config.end_date.isoformat(),
                    "initial_cash": result.config.initial_cash,
                    "rebalance_freq": result.config.rebalance_freq,
                    "benchmark_ticker": result.config.benchmark_ticker,
                    "cost_model": result.config.cost_model.name(),
                },
                "n_trades": len(result.trades),
                "n_rebalances": result.n_rebalances,
                "total_costs_paid": result.total_costs_paid,
                "strategy_metrics": metrics.to_dict(),
                "benchmark_metrics": bench_metrics.to_dict(),
            },
            indent=2,
            default=str,
        )
    )

    logger.info(f"wrote backtest report to {run_dir}")
    return run_dir


__all__ = ["write_report"]
