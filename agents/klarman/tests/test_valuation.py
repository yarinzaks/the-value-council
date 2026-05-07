"""Unit tests for conservative DCF + Margin of Safety."""

from __future__ import annotations

from datetime import date

import pytest

from agents.klarman.valuation import (
    DEFAULT_DISCOUNT_RATE_PCT,
    DEFAULT_MAX_GROWTH_PCT,
    DEFAULT_TERMINAL_MULTIPLE,
    _dcf_present_value,
    average_fcf,
    historical_fcf,
    intrinsic_value,
    margin_of_safety_pct,
    trailing_growth_pct,
)
from core.data.edgar_cache import EdgarCache


class TestHistoricalFcf:
    def test_clean_history(self, steady_fcf_cache: EdgarCache) -> None:
        records = historical_fcf(
            steady_fcf_cache, "STEADY", date(2024, 6, 30), years=5
        )
        assert len(records) == 5
        assert records[0].fiscal_year < records[-1].fiscal_year
        for r in records:
            assert r.fcf > 0

    def test_no_data_empty(self, empty_cache: EdgarCache) -> None:
        assert (
            historical_fcf(
                empty_cache, "NOTHING", date(2024, 6, 30), years=5
            )
            == []
        )


class TestAverageFcf:
    def test_basic(self, steady_fcf_cache: EdgarCache) -> None:
        records = historical_fcf(
            steady_fcf_cache, "STEADY", date(2024, 6, 30), years=5
        )
        avg = average_fcf(records)
        assert avg is not None
        assert avg > 0

    def test_empty_none(self) -> None:
        assert average_fcf([]) is None


class TestTrailingGrowth:
    def test_3pct_growth(self, steady_fcf_cache: EdgarCache) -> None:
        records = historical_fcf(
            steady_fcf_cache, "STEADY", date(2024, 6, 30), years=5
        )
        g = trailing_growth_pct(records, lookback=5)
        assert g is not None
        assert 2.0 < g < 4.0

    def test_too_few_records(self) -> None:
        assert trailing_growth_pct([]) is None


class TestDcfPresentValue:
    def test_zero_discount_terminal_only(self) -> None:
        # Degenerate r=0 falls back to terminal multiple.
        pv = _dcf_present_value(
            base_fcf=100.0,
            growth_pct=0.0,
            discount_pct=0.0,
            terminal_multiple=10.0,
            years=10,
        )
        assert pv == 1000.0

    def test_growth_increases_pv(self) -> None:
        a = _dcf_present_value(
            base_fcf=100.0,
            growth_pct=0.0,
            discount_pct=8.0,
            terminal_multiple=10.0,
            years=10,
        )
        b = _dcf_present_value(
            base_fcf=100.0,
            growth_pct=4.0,
            discount_pct=8.0,
            terminal_multiple=10.0,
            years=10,
        )
        assert b > a

    def test_higher_discount_lower_pv(self) -> None:
        cheap = _dcf_present_value(
            base_fcf=100.0,
            growth_pct=3.0,
            discount_pct=5.0,
            terminal_multiple=10.0,
            years=10,
        )
        expensive = _dcf_present_value(
            base_fcf=100.0,
            growth_pct=3.0,
            discount_pct=12.0,
            terminal_multiple=10.0,
            years=10,
        )
        assert cheap > expensive


class TestIntrinsicValue:
    def test_full_pipeline(self, steady_fcf_cache: EdgarCache) -> None:
        result = intrinsic_value(
            steady_fcf_cache, "STEADY", date(2024, 6, 30)
        )
        assert result is not None
        assert result.intrinsic_value_usd > 0
        assert result.avg_fcf_usd > 0
        # Conservative growth cap.
        assert 0.0 <= result.growth_rate_pct <= DEFAULT_MAX_GROWTH_PCT
        assert result.discount_rate_pct == DEFAULT_DISCOUNT_RATE_PCT
        assert result.terminal_multiple == DEFAULT_TERMINAL_MULTIPLE

    def test_no_history_none(self, empty_cache: EdgarCache) -> None:
        assert (
            intrinsic_value(empty_cache, "NOTHING", date(2024, 6, 30))
            is None
        )


class TestMarginOfSafety:
    def test_positive(self) -> None:
        assert margin_of_safety_pct(200_000_000_000, 100_000_000_000) == 50.0

    def test_negative(self) -> None:
        assert margin_of_safety_pct(100, 150) == -50.0

    def test_zero_iv_none(self) -> None:
        assert margin_of_safety_pct(0, 100) is None


class TestConservatism:
    """Klarman uses MORE conservative DCF settings than Buffett."""

    def test_growth_cap_lower_than_buffett(self) -> None:
        # Buffett's cap is 8%; Klarman's is 5%.
        assert DEFAULT_MAX_GROWTH_PCT == 5.0

    def test_discount_higher_than_buffett(self) -> None:
        # Buffett uses 5%; Klarman uses 8%.
        assert DEFAULT_DISCOUNT_RATE_PCT == 8.0

    def test_terminal_multiple_lower_than_buffett(self) -> None:
        # Buffett uses 13×; Klarman uses 10×.
        assert DEFAULT_TERMINAL_MULTIPLE == 10.0
