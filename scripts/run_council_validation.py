"""Run the full hybrid council against a shared 2020-2024 backtest.

Builds the EDGAR cache, universe, price loader, and runner ONCE — then
runs each of the six hybrid agents (Buffett, Lynch, Marks, Klarman,
Fisher, Neff) against the same config and prints a side-by-side
scoreboard.

This is the "run together" view requested after the merge: same
universe, same dates, same costs, comparable metrics. Each agent is
its quant-only path (no LLM in backtest — same lookahead-bias
rationale documented in each agent's run_full_market_validation.py).

Usage::

    .venv/bin/python -m scripts.run_council_validation
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from agents.buffett import WarrenBuffett
from agents.fisher import PhilipFisher
from agents.klarman import SethKlarman
from agents.lynch import PeterLynch
from agents.marks import HowardMarks
from agents.neff.total_return import JohnNeff
from core.backtest.data_loader import PriceDataLoader
from core.backtest.full_market_universe import FullMarketUniverse
from core.backtest.metrics import compute_metrics
from core.backtest.point_in_time import PointInTimeLoader
from core.backtest.strategy_runner import (
    BacktestRunner,
    RunnerConfig,
    Strategy,
)
from core.backtest.transaction_costs import PercentageCost
from core.backtest.validation_window import (
    VALIDATION_END,
    VALIDATION_REBALANCE,
    VALIDATION_START,
)
from core.data.edgar_cache import EdgarCache
from core.data.fundamentals_fetcher import (
    CachedEdgarAdapter,
    FundamentalsFetcher,
    FundamentalsFetcherConfig,
)
from core.logger import get_logger

logger = get_logger("scripts.run_council_validation")


@dataclass
class CouncilRow:
    """One row in the comparison scoreboard."""

    persona: str
    cagr_pct: float
    total_return_pct: float
    sharpe: float
    sortino: float
    calmar: float
    max_dd_pct: float
    information_ratio: float | None
    n_rebalances: int


def _build_runner() -> tuple[BacktestRunner, EdgarCache, RunnerConfig]:
    cache = EdgarCache()
    stats = cache.stats()
    if stats.ticker_count == 0:
        raise SystemExit(
            "No EDGAR cache. Run "
            "`.venv/bin/python -m scripts.prefetch_full_us_market` first."
        )
    fetcher = FundamentalsFetcher(
        cache=cache,
        client=None,
        config=FundamentalsFetcherConfig(populate_cache_on_miss=False),
    )
    adapter = CachedEdgarAdapter(fetcher=fetcher)
    pit_loader = PointInTimeLoader(adapter=adapter)

    universe = FullMarketUniverse(cache=cache)
    universe._ensure_loaded()

    cfg = RunnerConfig(
        start_date=VALIDATION_START,
        end_date=VALIDATION_END,
        initial_cash=10_000.0,
        rebalance_freq=VALIDATION_REBALANCE,
        benchmark_ticker="SPY",
        cost_model=PercentageCost(0.001),
        use_universe=True,
        use_fundamentals=True,
    )
    runner = BacktestRunner(
        cfg,
        price_loader=PriceDataLoader(),
        pit_loader=pit_loader,
        universe=universe,
    )
    return runner, cache, cfg


def _build_strategies(cache: EdgarCache) -> dict[str, Strategy]:
    """The six hybrid council agents — all in their quant-only mode.

    Each strategy's defaults match the agent's own
    ``run_full_market_validation.py`` so the comparison is fair.
    """
    return {
        "Warren Buffett":  WarrenBuffett(
            edgar_cache=cache,
            portfolio_size=8,
            min_market_cap=5_000_000_000.0,
            moat_analyzer=None,
        ),
        "Peter Lynch": PeterLynch(
            edgar_cache=cache,
            portfolio_size=30,
            min_market_cap=300_000_000.0,
            category_classifier=None,
        ),
        "Howard Marks": HowardMarks(
            edgar_cache=cache,
            min_market_cap=500_000_000.0,
            second_level_analyzer=None,
        ),
        "Seth Klarman": SethKlarman(
            edgar_cache=cache,
            min_market_cap=500_000_000.0,
            max_portfolio_size=20,
            downside_analyzer=None,
        ),
        "Philip Fisher": PhilipFisher(
            edgar_cache=cache,
            min_market_cap=1_000_000_000.0,
            max_portfolio_size=15,
            scuttlebutt_analyzer=None,
        ),
        "John Neff": JohnNeff(
            edgar_cache=cache,
            portfolio_size=25,
            min_market_cap=500_000_000.0,
        ),
    }


def _row_from(persona: str, result, benchmark_nav) -> CouncilRow:
    metrics = compute_metrics(
        result.nav_series,
        benchmark_nav=benchmark_nav,
        cost_model_name=result.config.cost_model.__class__.__name__,
    )
    return CouncilRow(
        persona=persona,
        cagr_pct=metrics.cagr_pct,
        total_return_pct=metrics.total_return_pct,
        sharpe=metrics.sharpe,
        sortino=metrics.sortino,
        calmar=metrics.calmar,
        max_dd_pct=metrics.max_drawdown_pct,
        information_ratio=metrics.information_ratio_vs_benchmark,
        n_rebalances=result.n_rebalances,
    )


def _print_scoreboard(rows: list[CouncilRow], spy_cagr: float) -> None:
    """Pretty-print the council scoreboard."""
    rows_sorted = sorted(rows, key=lambda r: -r.cagr_pct)
    headers = [
        "Persona",
        "CAGR%",
        "Alpha%",
        "Total%",
        "Sharpe",
        "Sortino",
        "Calmar",
        "MaxDD%",
        "IR",
        "Rebals",
    ]
    print()
    print("=" * 100)
    print(
        "  THE VALUE COUNCIL — 2020-2024 hybrid scoreboard "
        f"(SPY benchmark CAGR {spy_cagr:.2f}%)"
    )
    print("=" * 100)
    fmt = (
        "{persona:<16}{cagr:>9}{alpha:>9}{tot:>10}{sh:>9}{so:>9}"
        "{ca:>9}{dd:>9}{ir:>8}{rb:>8}"
    )
    print(
        fmt.format(
            persona=headers[0],
            cagr=headers[1],
            alpha=headers[2],
            tot=headers[3],
            sh=headers[4],
            so=headers[5],
            ca=headers[6],
            dd=headers[7],
            ir=headers[8],
            rb=headers[9],
        )
    )
    print("-" * 100)
    for r in rows_sorted:
        ir_str = f"{r.information_ratio:+.2f}" if r.information_ratio is not None else "  n/a"
        print(
            fmt.format(
                persona=r.persona,
                cagr=f"{r.cagr_pct:+.2f}",
                alpha=f"{r.cagr_pct - spy_cagr:+.2f}",
                tot=f"{r.total_return_pct:+.1f}",
                sh=f"{r.sharpe:.3f}",
                so=f"{r.sortino:.3f}",
                ca=f"{r.calmar:.3f}",
                dd=f"{r.max_dd_pct:.2f}",
                ir=ir_str,
                rb=f"{r.n_rebalances}",
            )
        )
    print("=" * 100)


def main() -> None:
    runner, cache, _cfg = _build_runner()
    strategies = _build_strategies(cache)

    rows: list[CouncilRow] = []
    spy_cagr = 0.0
    benchmark_nav: pd.Series | None = None

    for persona, strategy in strategies.items():
        logger.info(f"=== Running {persona} ({strategy.name}) ===")
        result = runner.run(strategy)
        if benchmark_nav is None:
            benchmark_nav = result.benchmark_nav_series
            spy_metrics = compute_metrics(benchmark_nav)
            spy_cagr = spy_metrics.cagr_pct
        rows.append(_row_from(persona, result, benchmark_nav))

    _print_scoreboard(rows, spy_cagr)


if __name__ == "__main__":
    main()
