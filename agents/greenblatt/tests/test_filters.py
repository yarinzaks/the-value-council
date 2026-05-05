"""Unit tests for Greenblatt universe filters."""

from __future__ import annotations

from datetime import date

import pytest

from agents.greenblatt.filters import (
    DEFAULT_MIN_MARKET_CAP_USD,
    EXCLUDED_SIC_RANGES,
    filter_candidates,
    has_positive_ebit,
    has_recent_earnings,
    is_excluded_sector,
    passes_filters,
    passes_market_cap,
)
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


def _fin(
    ticker: str = "TEST",
    *,
    operating_income: float | None = 100.0,
    sic_code: str | None = "3674",  # Semiconductors — not excluded
    shares_outstanding: float | None = 1_000_000,
    filing_date: date = date(2020, 6, 15),
    current_assets: float | None = 200.0,
    current_liabilities: float | None = 100.0,
    ppe_net: float | None = 500.0,
    cash: float | None = 50.0,
    total_debt: float | None = 100.0,
) -> PointInTimeFinancials:
    """Helper to build a PIT financials object with sensible defaults."""
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=date(2020, 12, 31),
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="12345",
            form_type="10-K",
            filing_date=filing_date,
            period_of_report=date(2019, 12, 31),
            accession_number=f"acc-{ticker}",
        ),
        operating_income=operating_income,
        sic_code=sic_code,
        shares_outstanding=shares_outstanding,
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        ppe_net=ppe_net,
        cash_and_equivalents=cash,
        total_debt=total_debt,
    )


class TestSectorExclusion:
    def test_excluded_sic_range_constants(self) -> None:
        assert (4900, 4999) in EXCLUDED_SIC_RANGES
        assert (6000, 6999) in EXCLUDED_SIC_RANGES

    def test_utility_excluded(self) -> None:
        assert is_excluded_sector("4911") is True  # Electric Services

    def test_bank_excluded(self) -> None:
        assert is_excluded_sector("6020") is True  # State Commercial Banks

    def test_semiconductor_not_excluded(self) -> None:
        assert is_excluded_sector("3674") is False

    def test_missing_sic_not_excluded(self) -> None:
        assert is_excluded_sector(None) is False
        assert is_excluded_sector("") is False

    def test_handles_5digit_codes(self) -> None:
        # Some sources include extended classification — first 4 digits matter
        assert is_excluded_sector("60201") is True

    def test_invalid_sic_not_excluded(self) -> None:
        assert is_excluded_sector("XXXX") is False


class TestMarketCap:
    def test_above_threshold(self) -> None:
        assert passes_market_cap(2_000_000_000) is True

    def test_below_threshold(self) -> None:
        assert passes_market_cap(500_000_000) is False

    def test_missing_rejects(self) -> None:
        assert passes_market_cap(None) is False

    def test_custom_threshold(self) -> None:
        assert passes_market_cap(75_000_000, minimum=50_000_000) is True


class TestEbitFilter:
    def test_positive_passes(self) -> None:
        assert has_positive_ebit(100.0) is True

    def test_zero_fails(self) -> None:
        assert has_positive_ebit(0.0) is False

    def test_negative_fails(self) -> None:
        assert has_positive_ebit(-100.0) is False

    def test_missing_fails(self) -> None:
        assert has_positive_ebit(None) is False


class TestEarningsRecency:
    def test_within_window(self) -> None:
        assert has_recent_earnings(
            date(2020, 12, 30), as_of=date(2021, 1, 2), cutoff_days=7
        ) is True

    def test_outside_window(self) -> None:
        assert has_recent_earnings(
            date(2020, 6, 15), as_of=date(2021, 1, 2), cutoff_days=7
        ) is False

    def test_no_filing_returns_false(self) -> None:
        assert has_recent_earnings(None, as_of=date(2021, 1, 2)) is False


class TestPassesFilters:
    def test_clean_candidate_passes(self) -> None:
        result = passes_filters(
            _fin("AAPL"),
            market_cap_usd=3_000_000_000_000.0,
            as_of=date(2020, 12, 31),
        )
        assert result.passed is True
        assert result.rejection_reason is None

    def test_missing_financials_rejects(self) -> None:
        result = passes_filters(None, 1_000_000_000, as_of=date(2020, 12, 31))
        assert result.passed is False
        assert "no point-in-time" in (result.rejection_reason or "")

    def test_utility_sector_rejected(self) -> None:
        result = passes_filters(
            _fin("XYZ", sic_code="4911"),
            market_cap_usd=5_000_000_000,
            as_of=date(2020, 12, 31),
        )
        assert result.passed is False
        assert "excluded sector" in (result.rejection_reason or "")

    def test_bank_sector_rejected(self) -> None:
        result = passes_filters(
            _fin("BAC", sic_code="6020"),
            market_cap_usd=5_000_000_000,
            as_of=date(2020, 12, 31),
        )
        assert result.passed is False
        assert "excluded sector" in (result.rejection_reason or "")

    def test_small_market_cap_rejected(self) -> None:
        result = passes_filters(
            _fin("SMALL"),
            market_cap_usd=100_000_000,  # below $1B default
            as_of=date(2020, 12, 31),
        )
        assert result.passed is False
        assert "market cap" in (result.rejection_reason or "")

    def test_negative_ebit_rejected(self) -> None:
        result = passes_filters(
            _fin("LOSS", operating_income=-100.0),
            market_cap_usd=5_000_000_000,
            as_of=date(2020, 12, 31),
        )
        assert result.passed is False
        assert "EBIT" in (result.rejection_reason or "")

    def test_recent_earnings_rejected(self) -> None:
        result = passes_filters(
            _fin("REC", filing_date=date(2020, 12, 28)),
            market_cap_usd=5_000_000_000,
            as_of=date(2020, 12, 31),
        )
        assert result.passed is False
        assert "earnings filed" in (result.rejection_reason or "")


class TestFilterCandidatesBatch:
    def test_returns_only_passers(self) -> None:
        candidates = [
            (_fin("OK", sic_code="3674"), 5_000_000_000.0),  # passes
            (_fin("UTIL", sic_code="4911"), 5_000_000_000.0),  # excluded sector
            (_fin("SMALL"), 100_000_000.0),  # below market cap
            (_fin("LOSS", operating_income=-1.0), 5_000_000_000.0),  # negative EBIT
        ]
        result = filter_candidates(candidates, as_of=date(2020, 12, 31))
        assert len(result) == 1
        assert result[0][0].ticker == "OK"

    def test_empty_input_returns_empty(self) -> None:
        result = filter_candidates([], as_of=date(2020, 12, 31))
        assert result == []
