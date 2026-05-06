"""Unit tests for Neff filter helpers."""

from __future__ import annotations

from datetime import date

import pytest

from agents.neff.filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    apply_quality_gates,
    debt_to_equity,
    dividend_yield,
    median,
    passes_quality_gates,
    pe_ratio,
    roe,
    total_return_to_pe,
)
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


def _fin(
    *,
    ticker: str = "TEST",
    eps_diluted: float | None = 2.0,
    eps_basic: float | None = 2.0,
    total_equity: float | None = 800_000_000.0,
    dividends_paid: float | None = -20_000_000.0,
    total_debt: float | None = 100_000_000.0,
    long_term_debt: float | None = 100_000_000.0,
    net_income: float | None = 50_000_000.0,
    shares_outstanding: float | None = 100_000_000.0,
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
        eps_diluted=eps_diluted,
        eps_basic=eps_basic,
        total_equity=total_equity,
        dividends_paid=dividends_paid,
        total_debt=total_debt,
        long_term_debt=long_term_debt,
        net_income=net_income,
        shares_outstanding=shares_outstanding,
    )


class TestPeRatio:
    def test_basic(self) -> None:
        # price 20 / EPS 2 = 10
        assert pe_ratio(20.0, _fin()) == pytest.approx(10.0)

    def test_zero_eps_returns_none(self) -> None:
        assert pe_ratio(20.0, _fin(eps_diluted=0, eps_basic=0)) is None

    def test_negative_eps(self) -> None:
        assert pe_ratio(20.0, _fin(eps_diluted=-1, eps_basic=-1)) is None

    def test_falls_back_to_basic(self) -> None:
        assert pe_ratio(20.0, _fin(eps_diluted=None)) == pytest.approx(10.0)

    def test_missing_price(self) -> None:
        assert pe_ratio(None, _fin()) is None


class TestDividendYield:
    def test_basic_negative_dividends(self) -> None:
        # |−20M| / 1B = 2%
        assert dividend_yield(1_000_000_000.0, _fin()) == pytest.approx(0.02)

    def test_no_dividend(self) -> None:
        assert dividend_yield(1_000_000_000.0, _fin(dividends_paid=None)) == 0.0

    def test_missing_mcap(self) -> None:
        assert dividend_yield(None, _fin()) is None


class TestRoe:
    def test_basic(self) -> None:
        # 50M / 800M = 6.25%
        assert roe(_fin()) == pytest.approx(0.0625)

    def test_zero_equity(self) -> None:
        assert roe(_fin(total_equity=0)) is None

    def test_negative_net_income(self) -> None:
        # ROE returns the value (caller decides if it's acceptable).
        assert roe(_fin(net_income=-10_000_000)) == pytest.approx(-0.0125)

    def test_none_input(self) -> None:
        assert roe(None) is None


class TestDebtToEquity:
    def test_basic(self) -> None:
        assert debt_to_equity(_fin()) == pytest.approx(0.125)

    def test_falls_back(self) -> None:
        assert debt_to_equity(
            _fin(total_debt=None, long_term_debt=200_000_000)
        ) == pytest.approx(0.25)

    def test_missing_treats_zero(self) -> None:
        assert debt_to_equity(_fin(total_debt=None, long_term_debt=None)) == 0.0


class TestTotalReturnToPe:
    def test_basic(self) -> None:
        # (10 + 4) / 7 = 2.0
        assert total_return_to_pe(10.0, 4.0, 7.0) == pytest.approx(2.0)

    def test_missing_inputs(self) -> None:
        assert total_return_to_pe(None, 4.0, 7.0) is None
        assert total_return_to_pe(10.0, None, 7.0) is None
        assert total_return_to_pe(10.0, 4.0, None) is None

    def test_zero_pe(self) -> None:
        assert total_return_to_pe(10.0, 4.0, 0.0) is None

    def test_negative_growth(self) -> None:
        # (-5 + 4) / 10 = -0.1
        assert total_return_to_pe(-5.0, 4.0, 10.0) == pytest.approx(-0.1)


class TestMedian:
    def test_odd(self) -> None:
        assert median([1, 3, 2]) == 2.0

    def test_even(self) -> None:
        assert median([1, 2, 3, 4]) == 2.5

    def test_empty(self) -> None:
        assert median([]) is None


class TestPassesQualityGates:
    def _candidate(self, **overrides) -> tuple:
        defaults = dict(
            net_income=50_000_000.0,
            total_debt=100_000_000.0,
            total_equity=800_000_000.0,
            shares_outstanding=100_000_000.0,
        )
        defaults.update({k: v for k, v in overrides.items() if k in defaults})
        fin = _fin(**defaults)
        market_cap = overrides.get("market_cap", 1_000_000_000.0)
        return fin, market_cap

    def test_clean_passes(self) -> None:
        fin, mcap = self._candidate()
        result = passes_quality_gates(fin, mcap)
        assert result.passed is True

    def test_negative_net_income_rejected(self) -> None:
        fin, mcap = self._candidate(net_income=-1)
        result = passes_quality_gates(fin, mcap)
        assert result.passed is False
        assert "net income" in (result.rejection_reason or "")

    def test_high_debt_rejected(self) -> None:
        fin, mcap = self._candidate(total_debt=2_000_000_000)
        result = passes_quality_gates(fin, mcap)
        assert result.passed is False
        assert "D/E" in (result.rejection_reason or "")

    def test_share_class_rejected(self) -> None:
        fin = _fin(ticker="ABC-A")
        result = passes_quality_gates(fin, 1_000_000_000)
        assert result.passed is False
        assert "share class" in (result.rejection_reason or "")

    def test_below_market_cap_floor_rejected(self) -> None:
        fin = _fin()
        result = passes_quality_gates(fin, 100_000_000)
        assert result.passed is False
        assert "market cap" in (result.rejection_reason or "")


class TestApplyQualityGates:
    def test_only_passers_returned(self) -> None:
        ok = _fin(ticker="OK")
        loser = _fin(ticker="LOSER", net_income=-1)
        candidates = [
            (ok, 1_000_000_000.0, 10.0),
            (loser, 1_000_000_000.0, 10.0),
        ]
        result = apply_quality_gates(candidates, as_of=date(2024, 6, 30))
        assert {f.ticker for f, _, _ in result} == {"OK"}


class TestDefaults:
    def test_defaults(self) -> None:
        assert DEFAULT_MAX_DE == 1.0
        assert DEFAULT_MIN_MARKET_CAP_USD == 500_000_000.0
