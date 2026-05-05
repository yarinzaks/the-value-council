"""Tests for the live agent adapter wrappers.

We hit the actual strategy code (not mocked) so any drift in the
strategy's selection_history schema breaks these tests early.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents.dreman.contrarian import DavidDreman
from agents.greenblatt.magic_formula import MagicFormula
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.live.agent_adapter import (
    DremanLive,
    GreenblattLive,
    LiveTarget,
    LiveWatch,
    ScanResult,
)


def _fin(
    *,
    ticker: str,
    revenue: float = 1_000_000_000.0,
    net_income: float = 100_000_000.0,
    eps_diluted: float = 2.0,
    operating_income: float = 150_000_000.0,
    operating_cash_flow: float = 100_000_000.0,
    total_equity: float = 800_000_000.0,
    current_assets: float = 500_000_000.0,
    current_liabilities: float = 200_000_000.0,
    total_debt: float = 100_000_000.0,
    total_assets: float = 900_000_000.0,
    cash_and_equivalents: float = 50_000_000.0,
    ppe_net: float = 200_000_000.0,
    shares_outstanding: float = 100_000_000.0,
    dividends_paid: float = -10_000_000.0,
    total_liabilities: float = 300_000_000.0,
) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=date(2026, 4, 29),
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=date(2026, 2, 15),
            period_of_report=date(2025, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        revenue=revenue,
        net_income=net_income,
        eps_diluted=eps_diluted,
        eps_basic=eps_diluted,
        operating_income=operating_income,
        operating_cash_flow=operating_cash_flow,
        total_equity=total_equity,
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        total_debt=total_debt,
        long_term_debt=total_debt,
        total_assets=total_assets,
        cash_and_equivalents=cash_and_equivalents,
        ppe_net=ppe_net,
        shares_outstanding=shares_outstanding,
        dividends_paid=dividends_paid,
        total_liabilities=total_liabilities,
    )


class _StubLookup:
    def __init__(self, data: dict) -> None:
        self._d = data

    def get(self, k: str):  # type: ignore[no-untyped-def]
        return self._d.get(k)


class TestGreenblattLive:
    def test_run_scan_returns_targets_and_watchlist(self) -> None:
        # 5 stocks, varying earnings yields. Rank 1 has highest EY.
        fins = {}
        prices = {}
        for i in range(5):
            t = f"T{i}"
            # Higher i → higher operating_income → lower EBIT/EV (worse rank)
            fins[t] = _fin(ticker=t, operating_income=50_000_000 + i * 50_000_000)
            prices[t] = 10.0
        adapter = GreenblattLive(
            MagicFormula(portfolio_size=2, min_market_cap=100),
            watchlist_size=3,
        )
        result = adapter.run_scan(
            date(2026, 4, 29),
            list(fins.keys()),
            _StubLookup(prices),
            _StubLookup(fins),
        )
        assert isinstance(result, ScanResult)
        assert len(result.targets) == 2
        # Targets are LiveTarget instances with rationales
        for t in result.targets:
            assert isinstance(t, LiveTarget)
            assert t.why_en
            assert t.why_he
            assert t.rank in (1, 2)
        # Watchlist is at most watchlist_size — held tickers excluded
        assert len(result.watchlist) <= 3
        held = {t.ticker for t in result.targets}
        for w in result.watchlist:
            assert w.ticker not in held

    def test_targets_ranked_in_order(self) -> None:
        fins = {f"T{i}": _fin(ticker=f"T{i}", operating_income=50_000_000 + i * 10_000_000) for i in range(5)}
        prices = {t: 10.0 for t in fins}
        adapter = GreenblattLive(MagicFormula(portfolio_size=3, min_market_cap=100))
        result = adapter.run_scan(
            date(2026, 4, 29),
            list(fins.keys()),
            _StubLookup(prices),
            _StubLookup(fins),
        )
        ranks = [t.rank for t in result.targets]
        assert ranks == sorted(ranks)


class TestDremanLive:
    def test_returns_targets_with_bilingual_why(self) -> None:
        # Build a universe where T0 has cheap P/E + high yield
        fins = {}
        prices = {}
        for i in range(20):
            fins[f"T{i}"] = _fin(
                ticker=f"T{i}",
                eps_diluted=1.0 + i * 0.5,  # T0 cheap, T19 expensive
                dividends_paid=-(50_000_000 - i * 2_000_000),
            )
            prices[f"T{i}"] = 5.0
        adapter = DremanLive(
            DavidDreman(portfolio_size=3, min_qualifying_metrics=1, min_market_cap=100),
            watchlist_size=5,
        )
        result = adapter.run_scan(
            date(2026, 4, 29),
            list(fins.keys()),
            _StubLookup(prices),
            _StubLookup(fins),
        )
        assert result.targets
        for t in result.targets:
            assert "/" in t.why_en or "rank" in t.why_en.lower()
            assert t.why_he  # non-empty Hebrew
        for w in result.watchlist:
            assert isinstance(w, LiveWatch)


class TestEmptyUniverse:
    def test_no_targets_no_crash(self) -> None:
        adapter = GreenblattLive(MagicFormula(portfolio_size=10, min_market_cap=100))
        result = adapter.run_scan(
            date(2026, 4, 29), [], _StubLookup({}), _StubLookup({})
        )
        assert result.targets == []
        assert result.watchlist == []
        assert result.universe_size == 0
