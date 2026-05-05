"""Unit tests for the :class:`MagicFormula` strategy class.

These tests run the ``select`` method directly with stub price /
fundamentals lookups, so they are fast, offline, and deterministic.
"""

from __future__ import annotations

from datetime import date
from typing import Mapping

import pytest

from agents.greenblatt.magic_formula import MagicFormula
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


class _StubPriceLookup:
    def __init__(self, prices: Mapping[str, float]) -> None:
        self._prices = dict(prices)

    def get(self, ticker: str) -> float | None:
        return self._prices.get(ticker.upper())


class _StubFundamentalsLookup:
    def __init__(self, financials: Mapping[str, PointInTimeFinancials | None]) -> None:
        self._fin = dict(financials)

    def get(self, ticker: str) -> PointInTimeFinancials | None:
        return self._fin.get(ticker.upper())


def _make_fin(
    ticker: str,
    *,
    operating_income: float,
    shares_outstanding: float,
    current_assets: float,
    current_liabilities: float,
    ppe_net: float,
    cash: float = 0.0,
    total_debt: float = 0.0,
    sic_code: str = "3674",
    filing_date: date = date(2020, 6, 15),
) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=date(2021, 1, 4),
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=filing_date,
            period_of_report=date(2019, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        operating_income=operating_income,
        shares_outstanding=shares_outstanding,
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        ppe_net=ppe_net,
        cash_and_equivalents=cash,
        total_debt=total_debt,
        sic_code=sic_code,
    )


class TestStrategyConfig:
    def test_invalid_portfolio_size_raises(self) -> None:
        with pytest.raises(ValueError):
            MagicFormula(portfolio_size=0)
        with pytest.raises(ValueError):
            MagicFormula(portfolio_size=-5)

    def test_invalid_min_market_cap_raises(self) -> None:
        with pytest.raises(ValueError):
            MagicFormula(min_market_cap=0)

    def test_strategy_name(self) -> None:
        assert MagicFormula.name == "greenblatt_magic_formula"


class TestSelect:
    def test_empty_universe_returns_empty(self) -> None:
        strat = MagicFormula(portfolio_size=10, min_market_cap=100)
        weights = strat.select(
            date(2021, 1, 4), [], _StubPriceLookup({}), _StubFundamentalsLookup({})
        )
        assert weights == {}

    def test_single_candidate_full_weight(self) -> None:
        strat = MagicFormula(portfolio_size=10, min_market_cap=1_000)
        # Build one passing candidate; market cap = 100 * 1M = $100M (well above $1k threshold)
        fin = _make_fin(
            "AAPL",
            operating_income=100,
            shares_outstanding=1_000_000,
            current_assets=200,
            current_liabilities=100,
            ppe_net=100,
        )
        prices = _StubPriceLookup({"AAPL": 100.0})
        funds = _StubFundamentalsLookup({"AAPL": fin})
        weights = strat.select(date(2021, 1, 4), ["AAPL"], prices, funds)
        assert weights == {"AAPL": 1.0}

    def test_excluded_sector_filtered_out(self) -> None:
        strat = MagicFormula(portfolio_size=10, min_market_cap=1_000)
        bank = _make_fin(
            "BAC",
            operating_income=100,
            shares_outstanding=1_000_000,
            current_assets=200,
            current_liabilities=100,
            ppe_net=100,
            sic_code="6020",  # bank
        )
        ok = _make_fin(
            "AAPL",
            operating_income=100,
            shares_outstanding=1_000_000,
            current_assets=200,
            current_liabilities=100,
            ppe_net=100,
        )
        prices = _StubPriceLookup({"BAC": 100.0, "AAPL": 100.0})
        funds = _StubFundamentalsLookup({"BAC": bank, "AAPL": ok})
        weights = strat.select(
            date(2021, 1, 4), ["BAC", "AAPL"], prices, funds
        )
        assert "BAC" not in weights
        assert weights["AAPL"] == 1.0

    def test_negative_ebit_filtered_out(self) -> None:
        strat = MagicFormula(portfolio_size=10, min_market_cap=1_000)
        loss = _make_fin(
            "LOSS",
            operating_income=-100,
            shares_outstanding=1_000_000,
            current_assets=200,
            current_liabilities=100,
            ppe_net=100,
        )
        ok = _make_fin(
            "OK",
            operating_income=100,
            shares_outstanding=1_000_000,
            current_assets=200,
            current_liabilities=100,
            ppe_net=100,
        )
        prices = _StubPriceLookup({"LOSS": 100.0, "OK": 100.0})
        funds = _StubFundamentalsLookup({"LOSS": loss, "OK": ok})
        weights = strat.select(
            date(2021, 1, 4), ["LOSS", "OK"], prices, funds
        )
        assert "LOSS" not in weights
        assert "OK" in weights

    def test_top_n_capping(self) -> None:
        """When more candidates qualify than portfolio_size, only the top N are picked."""
        strat = MagicFormula(portfolio_size=3, min_market_cap=1_000)
        fins: dict[str, PointInTimeFinancials | None] = {}
        prices: dict[str, float] = {}
        # 5 candidates with decreasing EBIT (so decreasing EY)
        for i in range(5):
            ticker = f"T{i:02d}"
            fins[ticker] = _make_fin(
                ticker,
                operating_income=100 - i * 10,
                shares_outstanding=1_000_000,
                current_assets=200,
                current_liabilities=100,
                ppe_net=100,
            )
            prices[ticker] = 100.0
        weights = strat.select(
            date(2021, 1, 4),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert len(weights) == 3
        # Highest EBIT names should win (T00, T01, T02)
        assert set(weights.keys()) == {"T00", "T01", "T02"}
        # Equal weighted at 1/3 each
        for w in weights.values():
            assert w == pytest.approx(1 / 3)

    def test_recent_earnings_excluded(self) -> None:
        strat = MagicFormula(portfolio_size=10, min_market_cap=1_000)
        recent = _make_fin(
            "RECENT",
            operating_income=100,
            shares_outstanding=1_000_000,
            current_assets=200,
            current_liabilities=100,
            ppe_net=100,
            filing_date=date(2021, 1, 1),  # 3 days before rebalance
        )
        prices = _StubPriceLookup({"RECENT": 100.0})
        funds = _StubFundamentalsLookup({"RECENT": recent})
        weights = strat.select(
            date(2021, 1, 4), ["RECENT"], prices, funds
        )
        assert weights == {}

    def test_selection_history_recorded(self) -> None:
        strat = MagicFormula(portfolio_size=2, min_market_cap=1_000)
        fins = {
            "A": _make_fin("A", operating_income=100, shares_outstanding=1_000_000,
                            current_assets=200, current_liabilities=100, ppe_net=100),
            "B": _make_fin("B", operating_income=80, shares_outstanding=1_000_000,
                            current_assets=200, current_liabilities=100, ppe_net=200),
        }
        prices = _StubPriceLookup({"A": 100.0, "B": 100.0})
        funds = _StubFundamentalsLookup(fins)
        strat.select(date(2021, 1, 4), ["A", "B"], prices, funds)
        assert len(strat.selection_history) == 1
        sel = strat.selection_history[0]
        assert sel.universe_size == 2
        assert sel.candidates_after_filters == 2
        assert len(sel.selected_tickers) == 2

    def test_selections_to_records_format(self) -> None:
        strat = MagicFormula(portfolio_size=2, min_market_cap=1_000)
        fins = {
            "A": _make_fin("A", operating_income=100, shares_outstanding=1_000_000,
                            current_assets=200, current_liabilities=100, ppe_net=100),
        }
        prices = _StubPriceLookup({"A": 100.0})
        funds = _StubFundamentalsLookup(fins)
        strat.select(date(2021, 1, 4), ["A"], prices, funds)
        records = strat.selections_to_records()
        assert len(records) == 1
        assert records[0]["as_of"] == "2021-01-04"
        assert records[0]["universe_size"] == 1
        assert records[0]["selected_tickers"] == ["A"]
