"""Unit tests for the BenjaminGraham strategy."""

from __future__ import annotations

from datetime import date
from typing import Mapping

import pytest

from agents.graham.net_net import BenjaminGraham
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


class _StubPriceLookup:
    def __init__(self, prices: Mapping[str, float]) -> None:
        self._prices = dict(prices)

    def get(self, ticker: str) -> float | None:
        return self._prices.get(ticker.upper())


class _StubFundamentalsLookup:
    def __init__(self, fin: Mapping[str, PointInTimeFinancials | None]) -> None:
        self._fin = dict(fin)

    def get(self, ticker: str) -> PointInTimeFinancials | None:
        return self._fin.get(ticker.upper())


def _make_fin(
    ticker: str,
    *,
    current_assets: float = 500_000_000,
    total_liabilities: float = 200_000_000,
    shares_outstanding: float = 100_000_000,
    total_debt: float = 100_000_000,
    total_equity: float = 800_000_000,
    net_income: float = 50_000_000,
) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=date(2024, 6, 30),
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=date(2024, 2, 15),
            period_of_report=date(2023, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        current_assets=current_assets,
        total_liabilities=total_liabilities,
        total_equity=total_equity,
        shares_outstanding=shares_outstanding,
        total_debt=total_debt,
        long_term_debt=total_debt,
        net_income=net_income,
    )


class TestStrategyConfig:
    def test_default_name(self) -> None:
        assert BenjaminGraham.name == "benjamin_graham"

    def test_invalid_portfolio_size(self) -> None:
        with pytest.raises(ValueError):
            BenjaminGraham(portfolio_size=0)

    def test_invalid_max_p_ncav(self) -> None:
        with pytest.raises(ValueError):
            BenjaminGraham(max_p_ncav=0)

    def test_invalid_max_de(self) -> None:
        with pytest.raises(ValueError):
            BenjaminGraham(max_de=0)


class TestSelect:
    def test_empty_universe(self) -> None:
        strat = BenjaminGraham(portfolio_size=10, min_market_cap=100)
        weights = strat.select(date(2024, 6, 30), [], _StubPriceLookup({}), _StubFundamentalsLookup({}))
        assert weights == {}

    def test_picks_only_below_two_thirds_ncav(self) -> None:
        strat = BenjaminGraham(portfolio_size=10, min_market_cap=1_000)
        # NCAV/share = 3.0 — passes if price < 2.0
        cheap = _make_fin("CHEAP")
        expensive = _make_fin("EXP")
        prices = _StubPriceLookup({"CHEAP": 1.5, "EXP": 2.5})
        funds = _StubFundamentalsLookup({"CHEAP": cheap, "EXP": expensive})
        weights = strat.select(
            date(2024, 6, 30), ["CHEAP", "EXP"], prices, funds
        )
        assert "CHEAP" in weights
        assert "EXP" not in weights

    def test_share_class_excluded(self) -> None:
        strat = BenjaminGraham(portfolio_size=10, min_market_cap=1_000)
        cheap = _make_fin("ABC-A")
        prices = _StubPriceLookup({"ABC-A": 1.5})
        funds = _StubFundamentalsLookup({"ABC-A": cheap})
        weights = strat.select(date(2024, 6, 30), ["ABC-A"], prices, funds)
        assert weights == {}

    def test_picks_cheapest_n(self) -> None:
        strat = BenjaminGraham(portfolio_size=2, min_market_cap=1_000)
        fins = {f"T{i}": _make_fin(f"T{i}") for i in range(3)}
        prices = {"T0": 0.5, "T1": 1.0, "T2": 1.5}  # all below ⅔ × 3.0 = 2.0
        weights = strat.select(
            date(2024, 6, 30),
            ["T0", "T1", "T2"],
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        # Cheapest 2 are T0, T1 (lowest P/NCAV)
        assert set(weights.keys()) == {"T0", "T1"}
        for w in weights.values():
            assert w == pytest.approx(0.5)

    def test_records_selection_history(self) -> None:
        strat = BenjaminGraham(portfolio_size=2, min_market_cap=1_000)
        fins = {"OK": _make_fin("OK")}
        prices = {"OK": 1.5}
        strat.select(
            date(2024, 6, 30), ["OK"], _StubPriceLookup(prices), _StubFundamentalsLookup(fins)
        )
        assert len(strat.selection_history) == 1
        sel = strat.selection_history[0]
        assert sel.universe_size == 1
        assert sel.candidates_after_filters == 1
        assert sel.selected_tickers == ["OK"]
