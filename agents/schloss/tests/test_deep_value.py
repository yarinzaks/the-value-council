"""Unit tests for the :class:`WalterSchloss` strategy class."""

from __future__ import annotations

from datetime import date
from typing import Mapping

import pytest

from agents.schloss.deep_value import WalterSchloss
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
    total_equity: float = 1_000_000_000.0,
    shares_outstanding: float = 100_000_000.0,
    total_debt: float = 200_000_000.0,
    net_income: float = 50_000_000.0,
    filing_date: date = date(2010, 1, 1),
) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=date(2024, 6, 30),
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=filing_date,
            period_of_report=date(2009, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        total_equity=total_equity,
        shares_outstanding=shares_outstanding,
        total_debt=total_debt,
        long_term_debt=total_debt,
        net_income=net_income,
    )


class TestStrategyConfig:
    def test_default_name(self) -> None:
        assert WalterSchloss.name == "walter_schloss"

    def test_invalid_portfolio_size_raises(self) -> None:
        with pytest.raises(ValueError):
            WalterSchloss(portfolio_size=0)
        with pytest.raises(ValueError):
            WalterSchloss(portfolio_size=-10)

    def test_invalid_max_pb_raises(self) -> None:
        with pytest.raises(ValueError):
            WalterSchloss(max_pb=0)
        with pytest.raises(ValueError):
            WalterSchloss(max_pb=-1.0)

    def test_invalid_max_de_raises(self) -> None:
        with pytest.raises(ValueError):
            WalterSchloss(max_de=0)

    def test_invalid_min_market_cap_raises(self) -> None:
        with pytest.raises(ValueError):
            WalterSchloss(min_market_cap=0)


class TestSelect:
    def test_empty_universe(self) -> None:
        strat = WalterSchloss(portfolio_size=10, min_market_cap=100, min_years_public=0)
        weights = strat.select(
            date(2024, 6, 30),
            [],
            _StubPriceLookup({}),
            _StubFundamentalsLookup({}),
        )
        assert weights == {}

    def test_all_candidates_too_expensive(self) -> None:
        strat = WalterSchloss(
            portfolio_size=10,
            min_market_cap=100,
            min_years_public=0,
            max_pb=0.75,
        )
        # P/B = 1.5 — all rejected
        fin = _make_fin("AAPL")  # BVPS 10
        prices = _StubPriceLookup({"AAPL": 15.0})
        funds = _StubFundamentalsLookup({"AAPL": fin})
        weights = strat.select(
            date(2024, 6, 30), ["AAPL"], prices, funds
        )
        assert weights == {}

    def test_picks_cheapest_n(self) -> None:
        strat = WalterSchloss(
            portfolio_size=2,
            min_market_cap=100_000_000,
            min_years_public=0,
        )
        # Three candidates all passing, with different P/B
        fins = {
            f"T{i}": _make_fin(f"T{i}")
            for i in range(3)
        }
        prices = {
            "T0": 3.0,  # P/B 0.3
            "T1": 5.0,  # P/B 0.5
            "T2": 7.0,  # P/B 0.7
        }
        weights = strat.select(
            date(2024, 6, 30),
            ["T0", "T1", "T2"],
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert len(weights) == 2
        # Two cheapest: T0, T1
        assert {"T0", "T1"} == set(weights.keys())
        for w in weights.values():
            assert w == pytest.approx(0.5)

    def test_takes_all_when_fewer_qualify(self) -> None:
        strat = WalterSchloss(
            portfolio_size=100,
            min_market_cap=100_000_000,
            min_years_public=0,
        )
        # Only 1 qualifies
        fins = {
            "OK": _make_fin("OK"),
            "EXP": _make_fin("EXP"),  # too expensive
        }
        prices = {"OK": 5.0, "EXP": 50.0}
        weights = strat.select(
            date(2024, 6, 30),
            ["OK", "EXP"],
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert weights == {"OK": 1.0}

    def test_filters_negative_book(self) -> None:
        strat = WalterSchloss(
            portfolio_size=10,
            min_market_cap=100_000_000,
            min_years_public=0,
        )
        fins = {"BAD": _make_fin("BAD", total_equity=-100_000_000)}
        prices = {"BAD": 5.0}
        weights = strat.select(
            date(2024, 6, 30), ["BAD"], _StubPriceLookup(prices), _StubFundamentalsLookup(fins)
        )
        assert weights == {}

    def test_selection_history_recorded(self) -> None:
        strat = WalterSchloss(
            portfolio_size=2,
            min_market_cap=100_000_000,
            min_years_public=0,
        )
        fins = {"OK": _make_fin("OK")}
        prices = {"OK": 5.0}
        strat.select(
            date(2024, 6, 30), ["OK"], _StubPriceLookup(prices), _StubFundamentalsLookup(fins)
        )
        assert len(strat.selection_history) == 1
        sel = strat.selection_history[0]
        assert sel.universe_size == 1
        assert sel.candidates_after_filters == 1
        assert sel.selected_tickers == ["OK"]

    def test_records_to_dict(self) -> None:
        strat = WalterSchloss(
            portfolio_size=2,
            min_market_cap=100_000_000,
            min_years_public=0,
        )
        fins = {"OK": _make_fin("OK")}
        prices = {"OK": 5.0}
        strat.select(
            date(2024, 6, 30), ["OK"], _StubPriceLookup(prices), _StubFundamentalsLookup(fins)
        )
        records = strat.selections_to_records()
        assert len(records) == 1
        assert records[0]["selected_tickers"] == ["OK"]
        assert records[0]["top_pb"] == pytest.approx(0.5)
