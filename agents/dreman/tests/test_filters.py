"""Unit tests for Dreman 4-metric contrarian filters."""

from __future__ import annotations

from datetime import date

import pytest

from agents.dreman.filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    DEFAULT_MIN_QUALIFYING_METRICS,
    DEFAULT_QUINTILE,
    apply_quality_gates,
    debt_to_equity,
    dividend_yield,
    passes_quality_gates,
    pb_ratio,
    pcf_ratio,
    pe_ratio,
    quintile_thresholds,
)
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


def _fin(
    *,
    ticker: str = "TEST",
    eps_diluted: float | None = 2.0,
    eps_basic: float | None = 2.0,
    operating_cash_flow: float | None = 100_000_000.0,
    total_equity: float | None = 800_000_000.0,
    dividends_paid: float | None = -20_000_000.0,
    total_debt: float | None = 100_000_000.0,
    long_term_debt: float | None = 100_000_000.0,
    net_income: float | None = 50_000_000.0,
    shares_outstanding: float | None = 100_000_000.0,
    total_assets: float | None = 1_000_000_000.0,
    total_liabilities: float | None = 200_000_000.0,
    current_liabilities: float | None = 200_000_000.0,
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
        operating_cash_flow=operating_cash_flow,
        total_equity=total_equity,
        dividends_paid=dividends_paid,
        total_debt=total_debt,
        long_term_debt=long_term_debt,
        net_income=net_income,
        shares_outstanding=shares_outstanding,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        current_liabilities=current_liabilities,
    )


class TestPeRatio:
    def test_basic_diluted(self) -> None:
        # price 20 / EPS 2 = 10
        assert pe_ratio(20.0, _fin()) == pytest.approx(10.0)

    def test_falls_back_to_basic(self) -> None:
        # diluted None → use basic
        assert pe_ratio(20.0, _fin(eps_diluted=None, eps_basic=2.5)) == pytest.approx(8.0)

    def test_negative_eps_returns_none(self) -> None:
        assert pe_ratio(20.0, _fin(eps_diluted=-1.0, eps_basic=-1.0)) is None

    def test_zero_eps_returns_none(self) -> None:
        assert pe_ratio(20.0, _fin(eps_diluted=0.0, eps_basic=0.0)) is None

    def test_missing_eps_both_returns_none(self) -> None:
        assert pe_ratio(20.0, _fin(eps_diluted=None, eps_basic=None)) is None

    def test_missing_price(self) -> None:
        assert pe_ratio(None, _fin()) is None

    def test_zero_price(self) -> None:
        assert pe_ratio(0.0, _fin()) is None

    def test_none_fin(self) -> None:
        assert pe_ratio(20.0, None) is None


class TestPcfRatio:
    def test_basic(self) -> None:
        # market cap 1B / OCF 100M = 10
        assert pcf_ratio(1_000_000_000.0, _fin()) == pytest.approx(10.0)

    def test_negative_ocf_returns_none(self) -> None:
        assert pcf_ratio(1_000_000_000.0, _fin(operating_cash_flow=-50_000_000)) is None

    def test_zero_ocf_returns_none(self) -> None:
        assert pcf_ratio(1_000_000_000.0, _fin(operating_cash_flow=0.0)) is None

    def test_missing_market_cap(self) -> None:
        assert pcf_ratio(None, _fin()) is None

    def test_missing_ocf(self) -> None:
        assert pcf_ratio(1_000_000_000.0, _fin(operating_cash_flow=None)) is None


class TestPbRatio:
    def test_basic(self) -> None:
        # mcap 1B / equity 800M = 1.25
        assert pb_ratio(1_000_000_000.0, _fin()) == pytest.approx(1.25)

    def test_negative_equity(self) -> None:
        assert pb_ratio(1_000_000_000.0, _fin(total_equity=-100_000_000)) is None

    def test_zero_equity(self) -> None:
        assert pb_ratio(1_000_000_000.0, _fin(total_equity=0.0)) is None

    def test_missing_mcap(self) -> None:
        assert pb_ratio(None, _fin()) is None


class TestDividendYield:
    def test_basic_negative_dividends_value(self) -> None:
        # SEC reports payments often negative; we use abs value.
        # |−20M| / 1B = 2%
        assert dividend_yield(1_000_000_000.0, _fin()) == pytest.approx(0.02)

    def test_positive_dividends_value(self) -> None:
        # Some filings report PaymentsOfDividends as positive cash outflow
        assert dividend_yield(
            1_000_000_000.0, _fin(dividends_paid=20_000_000.0)
        ) == pytest.approx(0.02)

    def test_no_dividends_returns_zero(self) -> None:
        assert dividend_yield(1_000_000_000.0, _fin(dividends_paid=None)) == 0.0

    def test_missing_mcap(self) -> None:
        assert dividend_yield(None, _fin()) is None


class TestDebtToEquity:
    def test_basic(self) -> None:
        # 100M / 800M = 0.125
        assert debt_to_equity(_fin()) == pytest.approx(0.125)

    def test_falls_back_to_long_term(self) -> None:
        assert debt_to_equity(
            _fin(total_debt=None, long_term_debt=200_000_000)
        ) == pytest.approx(0.25)

    def test_missing_debt_on_a_tagged_balance_sheet_is_zero(self) -> None:
        # A filer that tagged assets, liabilities and current liabilities
        # but no debt concept has told us its liabilities are not
        # borrowings. XBRL does not require tagging a zero.
        assert (
            debt_to_equity(
                _fin(
                    total_debt=None,
                    long_term_debt=None,
                    total_assets=1_000_000_000.0,
                    total_liabilities=200_000_000.0,
                    current_liabilities=200_000_000.0,
                )
            )
            == 0.0
        )

    def test_missing_debt_on_a_sparse_balance_sheet_is_none(self) -> None:
        # Nothing was reported. Scoring that as zero leverage is the
        # best possible mark on no evidence.
        assert (
            debt_to_equity(
                _fin(
                    total_debt=None,
                    long_term_debt=None,
                    total_assets=None,
                    total_liabilities=None,
                    current_liabilities=None,
                )
            )
            is None
        )

    def test_negative_equity_returns_none(self) -> None:
        assert debt_to_equity(_fin(total_equity=-100_000_000)) is None


class TestQuintileThresholds:
    def test_basic(self) -> None:
        # 0..9 (10 values). bottom 20% = first 2 (indices 0, 1) → low_idx 1, value=1
        # top 20% = last 2 (indices 8, 9) → high_idx 8, value=8
        low, high = quintile_thresholds([float(i) for i in range(10)], quintile=0.20)
        assert low == 1.0
        assert high == 8.0

    def test_custom_quintile(self) -> None:
        # 25% quintile of 0..7 (n=8); low_idx=int(8*.25)-1=1, high_idx=int(8*.75)=6
        low, high = quintile_thresholds([float(i) for i in range(8)], quintile=0.25)
        assert low == 1.0
        assert high == 6.0

    def test_empty(self) -> None:
        low, high = quintile_thresholds([])
        assert low == float("inf")
        assert high == float("-inf")

    def test_unsorted_input(self) -> None:
        # threshold logic must sort internally
        low, high = quintile_thresholds([5.0, 1.0, 9.0, 3.0, 7.0, 0.0, 8.0, 2.0, 4.0, 6.0], quintile=0.20)
        assert low == 1.0
        assert high == 8.0


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

    def test_clean_candidate_passes(self) -> None:
        fin, mcap = self._candidate()
        result = passes_quality_gates(fin, mcap)
        assert result.passed is True

    def test_negative_net_income_rejected(self) -> None:
        fin, mcap = self._candidate(net_income=-10_000_000)
        result = passes_quality_gates(fin, mcap)
        assert result.passed is False
        assert "net income" in (result.rejection_reason or "")

    def test_zero_net_income_rejected(self) -> None:
        fin, mcap = self._candidate(net_income=0.0)
        result = passes_quality_gates(fin, mcap)
        assert result.passed is False

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

    def test_negative_equity_rejected(self) -> None:
        fin = _fin(total_equity=-10_000_000)
        result = passes_quality_gates(fin, 1_000_000_000)
        assert result.passed is False
        assert "D/E" in (result.rejection_reason or "")

    def test_none_fin_rejected(self) -> None:
        result = passes_quality_gates(None, 1_000_000_000)
        assert result.passed is False


class TestApplyQualityGates:
    def test_only_passers_returned(self) -> None:
        ok = _fin(ticker="OK")
        loser = _fin(ticker="LOSER", net_income=-10_000_000)
        levered = _fin(ticker="LEV", total_debt=2_000_000_000)
        small = _fin(ticker="SMALL")
        candidates = [
            (ok, 1_000_000_000.0, 10.0),
            (loser, 1_000_000_000.0, 10.0),
            (levered, 1_000_000_000.0, 10.0),
            (small, 100_000_000.0, 1.0),
        ]
        result = apply_quality_gates(candidates, as_of=date(2024, 6, 30))
        tickers = {t[0].ticker for t in result}
        assert tickers == {"OK"}

    def test_skips_when_market_cap_or_price_missing(self) -> None:
        ok = _fin(ticker="OK")
        candidates = [
            (ok, None, 10.0),  # mcap missing
            (ok, 1_000_000_000.0, None),  # price missing
        ]
        result = apply_quality_gates(candidates, as_of=date(2024, 6, 30))
        assert result == []


class TestDefaults:
    def test_defaults_documented(self) -> None:
        assert DEFAULT_QUINTILE == 0.20
        assert DEFAULT_MIN_QUALIFYING_METRICS == 2
        assert DEFAULT_MAX_DE == 1.0
        assert DEFAULT_MIN_MARKET_CAP_USD == 500_000_000.0
