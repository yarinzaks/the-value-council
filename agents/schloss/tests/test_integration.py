"""Integration test for the Schloss strategy through BacktestRunner.

Uses fully-stubbed price + fundamentals data so the test is offline,
deterministic, and fast.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from agents.schloss.deep_value import WalterSchloss
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
from core.backtest.universe import HistoricalUniverse


class _FakeFundamentalsAdapter(EdgarAdapter):
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


def _seed_prices(
    loader: PriceDataLoader, ticker: str, prices: dict[date, float]
) -> None:
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
    """3 cheap winners (low P/B) + 3 losers (high P/B) + SPY benchmark."""
    price_loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
    all_dates = _date_range(date(2019, 12, 31), date(2022, 1, 31))
    cheap = ["CHEAP1", "CHEAP2", "CHEAP3"]
    rich = ["RICH1", "RICH2", "RICH3"]

    # Cheap stocks compound 50%/yr (linear-ish)
    for t in cheap:
        prices = {d: 5.0 * (1 + 0.0010 * i) for i, d in enumerate(all_dates)}
        _seed_prices(price_loader, t, prices)
    # Rich stocks stay flat
    for t in rich:
        prices = {d: 50.0 for d in all_dates}
        _seed_prices(price_loader, t, prices)
    spy = {d: 100.0 * (1 + 0.0004 * i) for i, d in enumerate(all_dates)}
    _seed_prices(price_loader, "SPY", spy)

    filings: dict[str, list[FilingMetadata]] = {}
    financials: dict[str, dict[str, float | None]] = {}
    # Both groups have BVPS = 10 (1B equity / 100M shares)
    # Cheap: price 5, P/B 0.5 → passes
    # Rich: price 50, P/B 5.0 → fails
    for group in (cheap, rich):
        for t in group:
            meta = FilingMetadata(
                ticker=t,
                cik="1",
                form_type="10-K",
                filing_date=date(2010, 1, 1),  # >5 years public by 2020
                period_of_report=date(2009, 12, 31),
                accession_number=f"acc-{t}",
            )
            filings[t] = [meta]
            financials[meta.accession_number] = {
                "total_equity": 1_000_000_000.0,
                "shares_outstanding": 100_000_000.0,
                "total_debt": 100_000_000.0,
                "long_term_debt": 100_000_000.0,
                "net_income": 50_000_000.0,
            }

    pit_loader = PointInTimeLoader(
        adapter=_FakeFundamentalsAdapter(filings, financials),
        cache_path=tmp_path / "edgar.sqlite",
    )
    universe = HistoricalUniverse(
        current_constituents=cheap + rich,
        change_log=[],
        cache_dir=tmp_path,
    )
    return price_loader, pit_loader, universe


class TestEndToEnd:
    def test_strategy_picks_cheap_and_outperforms(self, setup) -> None:
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
        strat = WalterSchloss(
            portfolio_size=3,
            min_market_cap=100_000_000.0,
            min_years_public=5,
        )
        result = runner.run(strat)

        # Strategy selected the cheap names
        first_sel = strat.selection_history[0]
        assert set(first_sel.selected_tickers) == {"CHEAP1", "CHEAP2", "CHEAP3"}

        # Outperforms benchmark
        final_strat = float(result.nav_series.iloc[-1])
        final_bench = float(result.benchmark_nav_series.iloc[-1])
        assert final_strat > final_bench

        # Trades executed
        assert len(result.trades) >= 3

    def test_no_qualifying_candidates_stays_in_cash(self, tmp_path: Path) -> None:
        price_loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        spy = {
            d: 100.0 * (1 + 0.001 * i)
            for i, d in enumerate(_date_range(date(2020, 1, 1), date(2020, 12, 31)))
        }
        _seed_prices(price_loader, "SPY", spy)

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
        strat = WalterSchloss(
            portfolio_size=10,
            min_market_cap=100_000_000.0,
            min_years_public=0,
        )
        result = runner.run(strat)
        assert len(result.trades) == 0
        assert result.nav_series.iloc[-1] == pytest.approx(10_000.0)
