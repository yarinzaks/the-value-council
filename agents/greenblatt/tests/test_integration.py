"""Integration test — Magic Formula end-to-end through BacktestRunner.

Uses a fake price loader, a fake universe, and a fake fundamentals
adapter so the test is deterministic and offline. The goal is to
verify that all the moving parts wire together correctly:

1. Runner asks the strategy to ``select`` on each rebalance date.
2. Strategy picks the right tickers per the Magic Formula.
3. Portfolio executes the trades, tracks NAV, applies costs.
4. Final NAV reflects realistic compounding through the picks.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from agents.greenblatt.magic_formula import MagicFormula
from core.backtest.data_loader import PriceDataLoader
from core.backtest.point_in_time import (
    EdgarAdapter,
    FilingMetadata,
    PointInTimeLoader,
)
from core.backtest.strategy_runner import (
    BacktestRunner,
    RunnerConfig,
)
from core.backtest.transaction_costs import ZeroCost
from core.backtest.universe import Change, HistoricalUniverse


class _FakeFundamentalsAdapter(EdgarAdapter):
    """Fake adapter that returns canned filings + financials for a fixture universe."""

    def __init__(
        self,
        filings: dict[str, list[FilingMetadata]],
        financials: dict[str, dict[str, float | None]],
    ) -> None:
        self._filings = filings
        self._financials = financials

    def list_filings(
        self, ticker: str, *, form_types: tuple[str, ...]
    ) -> list[FilingMetadata]:
        return [
            f
            for f in self._filings.get(ticker.upper(), [])
            if f.form_type in form_types
        ]

    def parse_financials(
        self, filing: FilingMetadata
    ) -> dict[str, float | None]:
        return self._financials.get(filing.accession_number, {})


def _seed_price_cache(
    loader: PriceDataLoader, ticker: str, prices: dict[date, float]
) -> None:
    """Insert OHLCV rows into the loader's SQLite cache."""
    idx = pd.DatetimeIndex(sorted(prices.keys()))
    df = pd.DataFrame(
        {
            "Open": [prices[d.date()] for d in idx],
            "High": [prices[d.date()] for d in idx],
            "Low": [prices[d.date()] for d in idx],
            "Close": [prices[d.date()] for d in idx],
            "Adj Close": [prices[d.date()] for d in idx],
            "Volume": [1_000_000] * len(idx),
        },
        index=idx,
    )
    loader._write_cache(ticker, df)


def _date_range(start: date, end: date) -> list[date]:
    rng = pd.date_range(start, end, freq="B")
    return [d.date() for d in rng]


@pytest.fixture
def setup(tmp_path: Path):
    """Build a fully self-contained backtest harness:
    - A 6-stock universe (3 Magic-Formula winners, 3 losers, 1 SPY benchmark).
    - Two-year window with monthly price points compounding cleanly.
    - Fake fundamentals adapter so the strategy gets PIT data.
    """
    price_loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")

    # Build a price calendar 2020-01-01 to 2022-01-01
    all_dates = _date_range(date(2019, 12, 31), date(2022, 1, 31))
    # Each "winner" doubles over the full 2-year period (linear-ish for simplicity)
    # Each "loser" stays flat
    winners = ["WIN1", "WIN2", "WIN3"]
    losers = ["LOSE1", "LOSE2", "LOSE3"]
    for i, t in enumerate(winners):
        # start at 100, grow ~50% per year
        prices = {
            d: 100.0 * (1 + 0.0010 * day_idx)
            for day_idx, d in enumerate(all_dates)
        }
        _seed_price_cache(price_loader, t, prices)
    for t in losers:
        prices = {d: 100.0 for d in all_dates}
        _seed_price_cache(price_loader, t, prices)
    # Benchmark — SPY grows 10%/year linearly
    spy_prices = {
        d: 100.0 * (1 + 0.0004 * day_idx) for day_idx, d in enumerate(all_dates)
    }
    _seed_price_cache(price_loader, "SPY", spy_prices)

    # Build fundamentals — winners have HIGH EBIT/EV and HIGH ROC; losers low.
    filings: dict[str, list[FilingMetadata]] = {}
    financials: dict[str, dict[str, float | None]] = {}
    for t in winners:
        meta = FilingMetadata(
            ticker=t,
            cik="11111",
            form_type="10-K",
            filing_date=date(2019, 6, 15),
            period_of_report=date(2018, 12, 31),
            accession_number=f"acc-{t}",
        )
        filings[t] = [meta]
        financials[meta.accession_number] = {
            "operating_income": 100.0,  # high EBIT
            "current_assets": 200.0,
            "current_liabilities": 100.0,
            "ppe_net": 100.0,  # IC = 200, ROC = 50%
            "shares_outstanding": 1_000_000.0,
            "cash_and_equivalents": 0.0,
            "total_debt": 0.0,
            "sic_code": "3674",
        }
    for t in losers:
        meta = FilingMetadata(
            ticker=t,
            cik="22222",
            form_type="10-K",
            filing_date=date(2019, 6, 15),
            period_of_report=date(2018, 12, 31),
            accession_number=f"acc-{t}",
        )
        filings[t] = [meta]
        financials[meta.accession_number] = {
            "operating_income": 10.0,  # low EBIT
            "current_assets": 200.0,
            "current_liabilities": 100.0,
            "ppe_net": 1000.0,  # IC = 1100, ROC = 0.9%
            "shares_outstanding": 1_000_000.0,
            "cash_and_equivalents": 0.0,
            "total_debt": 0.0,
            "sic_code": "3674",
        }

    pit_loader = PointInTimeLoader(
        adapter=_FakeFundamentalsAdapter(filings, financials),
        cache_path=tmp_path / "edgar.sqlite",
    )

    # Universe: all 6 tickers, no churn
    universe = HistoricalUniverse(
        current_constituents=winners + losers,
        change_log=[],
        cache_dir=tmp_path,
    )

    return price_loader, pit_loader, universe


class TestEndToEnd:
    def test_strategy_picks_winners_and_outperforms_benchmark(self, setup) -> None:
        price_loader, pit_loader, universe = setup
        cfg = RunnerConfig(
            start_date=date(2020, 1, 2),
            end_date=date(2021, 12, 31),
            initial_cash=10_000.0,
            rebalance_freq="annual",
            benchmark_ticker="SPY",
            cost_model=ZeroCost(),
            use_universe=True,
            use_fundamentals=True,
        )
        runner = BacktestRunner(
            cfg,
            price_loader=price_loader,
            pit_loader=pit_loader,
            universe=universe,
        )
        strat = MagicFormula(portfolio_size=3, min_market_cap=1_000_000)
        result = runner.run(strat)

        # Strategy selected the winners
        assert len(strat.selection_history) >= 1
        first_selection = strat.selection_history[0]
        assert set(first_selection.selected_tickers) == {"WIN1", "WIN2", "WIN3"}

        # The strategy should outperform the benchmark since winners grow 2.5x
        # the benchmark per day.
        final_strategy_nav = float(result.nav_series.iloc[-1])
        final_benchmark_nav = float(result.benchmark_nav_series.iloc[-1])
        assert final_strategy_nav > final_benchmark_nav, (
            f"Strategy NAV {final_strategy_nav:.2f} should exceed "
            f"benchmark NAV {final_benchmark_nav:.2f}"
        )

        # Trades should have been executed (1 buy per winner per rebalance)
        assert len(result.trades) >= 3

    def test_no_qualifying_candidates_stays_in_cash(self, tmp_path: Path) -> None:
        """If the universe has zero qualifying stocks, the portfolio holds cash."""
        price_loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        # SPY only — needed by runner for benchmark calendar
        spy_prices = {
            d: 100.0 * (1 + 0.001 * i)
            for i, d in enumerate(_date_range(date(2020, 1, 1), date(2020, 12, 31)))
        }
        _seed_price_cache(price_loader, "SPY", spy_prices)

        # No fundamentals at all — every candidate fails filters
        adapter = _FakeFundamentalsAdapter({}, {})
        pit_loader = PointInTimeLoader(
            adapter=adapter, cache_path=tmp_path / "edgar.sqlite"
        )

        universe = HistoricalUniverse(
            current_constituents=["NODATA"], change_log=[], cache_dir=tmp_path
        )

        cfg = RunnerConfig(
            start_date=date(2020, 2, 1),
            end_date=date(2020, 11, 30),
            initial_cash=10_000.0,
            rebalance_freq="annual",
            benchmark_ticker="SPY",
            cost_model=ZeroCost(),
            use_universe=True,
            use_fundamentals=True,
        )
        runner = BacktestRunner(
            cfg,
            price_loader=price_loader,
            pit_loader=pit_loader,
            universe=universe,
        )
        strat = MagicFormula(portfolio_size=10, min_market_cap=1_000)
        result = runner.run(strat)

        # No trades should have been executed
        assert len(result.trades) == 0
        # Final NAV should be unchanged (all cash, ZeroCost)
        assert result.nav_series.iloc[-1] == pytest.approx(10_000.0)
