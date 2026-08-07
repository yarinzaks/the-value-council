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
from typing import ClassVar

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
from core.backtest.universe import HistoricalUniverse


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
    for _i, t in enumerate(winners):
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


# ---------------------------------------------------------------------------
# Holding period, end to end through select()
# ---------------------------------------------------------------------------
class _Lookup:
    """Minimal stand-in for PriceLookup / FundamentalsLookup."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def get(self, ticker: str):  # type: ignore[no-untyped-def]
        return self._data.get(ticker)


def _pit(ticker: str, *, ebit: float, shares: float = 1_000_000.0):  # type: ignore[no-untyped-def]
    """A candidate that clears the Magic Formula filters cleanly.

    The filing date is deliberately old: Greenblatt excludes anything
    that reported within the last seven days, so a fresh filing date
    would make every fixture vanish before it could be ranked.
    """
    from core.backtest.point_in_time import PointInTimeFinancials

    return PointInTimeFinancials(
        ticker=ticker,
        as_of=date(2026, 8, 5),
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=date(2026, 5, 1),
            period_of_report=date(2026, 3, 31),
            accession_number=f"acc-{ticker}",
        ),
        operating_income=ebit,
        net_income=ebit * 0.7,
        revenue=ebit * 5,
        total_assets=5_000_000.0,
        total_liabilities=1_000_000.0,
        total_equity=4_000_000.0,
        current_assets=2_000_000.0,
        current_liabilities=1_000_000.0,
        cash_and_equivalents=500_000.0,
        ppe_net=1_500_000.0,
        total_debt=0.0,
        long_term_debt=0.0,
        shares_outstanding=shares,
        eps_diluted=ebit * 0.7 / shares,
        eps_basic=ebit * 0.7 / shares,
        sic_code="3571",
    )


class TestHoldingPeriodEndToEnd:
    """The rule has to survive the whole select() path, not just the
    helper — the runner sold anything that left today's top N, so the
    live book turned over 28% in three trading days."""

    AS_OF = date(2026, 8, 5)
    UNIVERSE: ClassVar[list[str]] = ["AAA", "BBB", "CCC"]

    def _inputs(self):  # type: ignore[no-untyped-def]
        prices = _Lookup({"AAA": 10.0, "BBB": 10.0, "CCC": 10.0})
        fundamentals = _Lookup(
            {
                "AAA": _pit("AAA", ebit=900_000.0),
                "BBB": _pit("BBB", ebit=600_000.0),
                "CCC": _pit("CCC", ebit=50_000.0),  # worst rank
            }
        )
        return prices, fundamentals

    def _held(self, days_ago: int):  # type: ignore[no-untyped-def]
        from datetime import timedelta

        from core.backtest.strategy_runner import HeldPosition

        return {
            "CCC": HeldPosition(
                ticker="CCC",
                shares=10.0,
                entry_price=9.0,
                entry_date=self.AS_OF - timedelta(days=days_ago),
                current_price=10.0,
            )
        }

    def test_worst_ranked_is_excluded_without_a_book(self) -> None:
        prices, fundamentals = self._inputs()
        strategy = MagicFormula(portfolio_size=2, min_market_cap=1.0)

        weights = strategy.select(self.AS_OF, self.UNIVERSE, prices, fundamentals)

        assert set(weights) == {"AAA", "BBB"}

    def test_a_young_holding_survives_falling_out_of_the_ranking(self) -> None:
        prices, fundamentals = self._inputs()
        strategy = MagicFormula(portfolio_size=2, min_market_cap=1.0)

        weights = strategy.select(
            self.AS_OF, self.UNIVERSE, prices, fundamentals, held=self._held(30)
        )

        assert "CCC" in weights
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_a_matured_holding_loses_its_protection(self) -> None:
        prices, fundamentals = self._inputs()
        strategy = MagicFormula(portfolio_size=2, min_market_cap=1.0)

        weights = strategy.select(
            self.AS_OF, self.UNIVERSE, prices, fundamentals, held=self._held(400)
        )

        assert "CCC" not in weights
