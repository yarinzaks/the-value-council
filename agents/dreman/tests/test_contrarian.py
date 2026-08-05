"""Unit tests for the DavidDreman strategy."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest

from agents.dreman.contrarian import DavidDreman
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
    eps: float = 2.0,
    ocf: float = 100_000_000.0,
    equity: float = 800_000_000.0,
    dividends: float = -20_000_000.0,
    debt: float = 100_000_000.0,
    net_income: float = 50_000_000.0,
    shares: float = 100_000_000.0,
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
        eps_diluted=eps,
        eps_basic=eps,
        operating_cash_flow=ocf,
        total_equity=equity,
        dividends_paid=dividends,
        total_debt=debt,
        long_term_debt=debt,
        net_income=net_income,
        shares_outstanding=shares,
    )


class TestStrategyConfig:
    def test_default_name(self) -> None:
        assert DavidDreman.name == "david_dreman"

    def test_invalid_portfolio_size(self) -> None:
        with pytest.raises(ValueError):
            DavidDreman(portfolio_size=0)

    def test_invalid_min_qualifying(self) -> None:
        with pytest.raises(ValueError):
            DavidDreman(min_qualifying_metrics=0)
        with pytest.raises(ValueError):
            DavidDreman(min_qualifying_metrics=5)

    def test_invalid_quintile(self) -> None:
        with pytest.raises(ValueError):
            DavidDreman(quintile=0.0)
        with pytest.raises(ValueError):
            DavidDreman(quintile=0.5)
        with pytest.raises(ValueError):
            DavidDreman(quintile=0.6)

    def test_invalid_max_de(self) -> None:
        with pytest.raises(ValueError):
            DavidDreman(max_de=0)

    def test_invalid_min_market_cap(self) -> None:
        with pytest.raises(ValueError):
            DavidDreman(min_market_cap=0)


class TestSelectEdgeCases:
    def test_empty_universe(self) -> None:
        strat = DavidDreman(portfolio_size=10, min_market_cap=100)
        weights = strat.select(
            date(2024, 6, 30), [], _StubPriceLookup({}), _StubFundamentalsLookup({})
        )
        assert weights == {}

    def test_all_loss_makers_returns_empty(self) -> None:
        strat = DavidDreman(portfolio_size=10, min_market_cap=1_000)
        fins = {f"T{i}": _make_fin(f"T{i}", net_income=-1_000_000) for i in range(5)}
        prices = {f"T{i}": 5.0 for i in range(5)}
        weights = strat.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert weights == {}

    def test_share_class_excluded(self) -> None:
        strat = DavidDreman(portfolio_size=10, min_market_cap=1_000)
        fins = {"ABC-A": _make_fin("ABC-A")}
        prices = {"ABC-A": 5.0}
        weights = strat.select(
            date(2024, 6, 30), ["ABC-A"], _StubPriceLookup(prices), _StubFundamentalsLookup(fins)
        )
        assert weights == {}


class TestSelectQuintileGate:
    def test_picks_contrarian_outliers(self) -> None:
        """Build a universe of 20 stocks where one is clearly contrarian.

        Stock CHEAP has lowest P/E, P/B and highest yield among the
        population. It should make the cut on >= 2 metrics.
        """
        strat = DavidDreman(
            portfolio_size=5, min_qualifying_metrics=2, min_market_cap=1_000
        )
        # Build 19 "normal" stocks
        fins = {
            f"NORMAL{i}": _make_fin(f"NORMAL{i}", eps=1.0, equity=100_000_000)
            for i in range(19)
        }
        # CHEAP has 10x EPS (cheap P/E), 10x equity (cheap P/B), 5x dividends
        fins["CHEAP"] = _make_fin(
            "CHEAP", eps=10.0, equity=1_000_000_000, dividends=-100_000_000
        )
        prices = {t: 5.0 for t in fins}
        weights = strat.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert "CHEAP" in weights

    def test_equal_weighting(self) -> None:
        """Selected stocks must be equal-weighted."""
        strat = DavidDreman(
            portfolio_size=3, min_qualifying_metrics=1, min_market_cap=1_000
        )
        # 5 stocks with varying P/E so quintile gives clear winners
        fins = {f"T{i}": _make_fin(f"T{i}", eps=1.0 + i * 2.0) for i in range(5)}
        prices = {t: 5.0 for t in fins}
        weights = strat.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        if weights:
            ws = list(weights.values())
            assert all(w == pytest.approx(1.0 / len(ws)) for w in ws)
            assert sum(ws) == pytest.approx(1.0)


class TestSelectionHistory:
    def test_records_selection(self) -> None:
        strat = DavidDreman(
            portfolio_size=5, min_qualifying_metrics=1, min_market_cap=1_000
        )
        fins = {f"T{i}": _make_fin(f"T{i}", eps=1.0 + i * 2.0) for i in range(5)}
        prices = {t: 5.0 for t in fins}
        strat.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert len(strat.selection_history) == 1
        sel = strat.selection_history[0]
        assert sel.universe_size == 5
        assert sel.candidates_with_data == 5
        assert sel.candidates_after_quality >= 0

    def test_records_empty_when_no_qualifiers(self) -> None:
        strat = DavidDreman(portfolio_size=5, min_market_cap=1_000)
        # All loss-makers → quality gate filters everyone
        fins = {f"T{i}": _make_fin(f"T{i}", net_income=-1) for i in range(3)}
        prices = {t: 5.0 for t in fins}
        strat.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert len(strat.selection_history) == 1
        sel = strat.selection_history[0]
        assert sel.candidates_after_quality == 0
        assert sel.selected_tickers == []


class TestSelectionsToRecords:
    def test_records_serializable(self) -> None:
        strat = DavidDreman(
            portfolio_size=2, min_qualifying_metrics=1, min_market_cap=1_000
        )
        fins = {f"T{i}": _make_fin(f"T{i}", eps=1.0 + i * 2.0) for i in range(5)}
        prices = {t: 5.0 for t in fins}
        strat.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        records = strat.selections_to_records()
        assert len(records) == 1
        r = records[0]
        assert r["as_of"] == "2024-06-30"
        assert "universe_size" in r
        assert "selected_tickers" in r
