"""Tests for the JohnNeff strategy class."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Mapping

import pytest

from agents.neff.total_return import JohnNeff
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact


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
    equity: float = 800_000_000.0,
    dividends: float = -40_000_000.0,
    debt: float = 100_000_000.0,
    net_income: float = 130_000_000.0,
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
        total_equity=equity,
        dividends_paid=dividends,
        total_debt=debt,
        long_term_debt=debt,
        net_income=net_income,
        shares_outstanding=shares,
    )


def _seed_growth(cache: EdgarCache, ticker: str) -> None:
    """Seed cache so the ticker has 12% CAGR for 4 years."""
    facts = [
        XbrlFact(
            concept="Revenues",
            namespace="us-gaap",
            unit="USD",
            value=1_000_000_000.0,
            period_start=date(2020, 1, 1),
            period_end=date(2020, 12, 31),
            filed=date(2021, 2, 15),
            form="10-K",
            fiscal_year=2020,
            fiscal_period="FY",
            accession_number=f"{ticker}-21",
        ),
        XbrlFact(
            concept="Revenues",
            namespace="us-gaap",
            unit="USD",
            value=1_575_000_000.0,
            period_start=date(2023, 1, 1),
            period_end=date(2023, 12, 31),
            filed=date(2024, 2, 15),
            form="10-K",
            fiscal_year=2023,
            fiscal_period="FY",
            accession_number=f"{ticker}-24",
        ),
        XbrlFact(
            concept="EarningsPerShareDiluted",
            namespace="us-gaap",
            unit="USD/shares",
            value=1.0,
            period_start=date(2020, 1, 1),
            period_end=date(2020, 12, 31),
            filed=date(2021, 2, 15),
            form="10-K",
            fiscal_year=2020,
            fiscal_period="FY",
            accession_number=f"{ticker}-21",
        ),
        XbrlFact(
            concept="EarningsPerShareDiluted",
            namespace="us-gaap",
            unit="USD/shares",
            value=1.575,
            period_start=date(2023, 1, 1),
            period_end=date(2023, 12, 31),
            filed=date(2024, 2, 15),
            form="10-K",
            fiscal_year=2023,
            fiscal_period="FY",
            accession_number=f"{ticker}-24",
        ),
    ]
    cache.save_facts(ticker, facts)


@pytest.fixture
def edgar_cache(tmp_path: Path) -> EdgarCache:
    return EdgarCache(cache_dir=tmp_path)


class TestStrategyConfig:
    def test_default_name(self, edgar_cache: EdgarCache) -> None:
        s = JohnNeff(edgar_cache=edgar_cache)
        assert s.name == "john_neff"

    def test_invalid_portfolio_size(self, edgar_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            JohnNeff(edgar_cache=edgar_cache, portfolio_size=0)

    def test_invalid_max_de(self, edgar_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            JohnNeff(edgar_cache=edgar_cache, max_de=0)

    def test_invalid_min_market_cap(self, edgar_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            JohnNeff(edgar_cache=edgar_cache, min_market_cap=0)

    def test_missing_cache_raises(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            JohnNeff(edgar_cache=None)  # type: ignore[arg-type]


class TestSelectEdgeCases:
    def test_empty_universe(self, edgar_cache: EdgarCache) -> None:
        s = JohnNeff(edgar_cache=edgar_cache, portfolio_size=10, min_market_cap=100)
        weights = s.select(
            date(2024, 6, 30),
            [],
            _StubPriceLookup({}),
            _StubFundamentalsLookup({}),
        )
        assert weights == {}

    def test_all_loss_makers_returns_empty(
        self, edgar_cache: EdgarCache
    ) -> None:
        s = JohnNeff(edgar_cache=edgar_cache, portfolio_size=10, min_market_cap=100)
        fins = {
            f"T{i}": _make_fin(f"T{i}", net_income=-1_000_000) for i in range(5)
        }
        prices = {t: 5.0 for t in fins}
        weights = s.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert weights == {}

    def test_share_class_excluded(self, edgar_cache: EdgarCache) -> None:
        s = JohnNeff(edgar_cache=edgar_cache, portfolio_size=10, min_market_cap=100)
        fins = {"ABC-A": _make_fin("ABC-A")}
        prices = {"ABC-A": 5.0}
        weights = s.select(
            date(2024, 6, 30),
            ["ABC-A"],
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert weights == {}


class TestSelectionHistory:
    def test_records_selection(self, edgar_cache: EdgarCache) -> None:
        s = JohnNeff(edgar_cache=edgar_cache, portfolio_size=5, min_market_cap=100)
        fins = {f"T{i}": _make_fin(f"T{i}", net_income=-1) for i in range(3)}
        prices = {t: 5.0 for t in fins}
        s.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert len(s.selection_history) == 1
        sel = s.selection_history[0]
        assert sel.universe_size == 3
        assert sel.candidates_with_data == 3
