"""Unit tests for Owner Earnings + DCF intrinsic value."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from agents.buffett.owner_earnings import (
    DEFAULT_DISCOUNT_RATE_PCT,
    DEFAULT_TERMINAL_MULTIPLE,
    _dcf_present_value,
    average_owner_earnings,
    historical_owner_earnings,
    intrinsic_value,
    margin_of_safety_pct,
    trailing_growth_pct,
)
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact


class TestHistoricalOwnerEarnings:
    def test_clean_history(self, buffett_quality_cache: EdgarCache) -> None:
        records = historical_owner_earnings(
            buffett_quality_cache, "WONDERFUL", date(2024, 6, 30), years=5
        )
        assert len(records) == 5
        # Sorted oldest first.
        assert records[0].fiscal_year < records[-1].fiscal_year
        # All OE positive (OCF = 1.4× NI, capex = 0.25× NI → OE = 1.15× NI).
        for r in records:
            assert r.owner_earnings > 0
            assert r.ocf > 0
            assert r.capex > 0

    def test_no_data_empty(self, empty_cache: EdgarCache) -> None:
        assert (
            historical_owner_earnings(
                empty_cache, "NOTHING", date(2024, 6, 30), years=5
            )
            == []
        )


class TestAverageOwnerEarnings:
    def test_basic_average(self, buffett_quality_cache: EdgarCache) -> None:
        records = historical_owner_earnings(
            buffett_quality_cache, "WONDERFUL", date(2024, 6, 30), years=5
        )
        avg = average_owner_earnings(records)
        assert avg is not None
        assert avg > 0

    def test_empty_returns_none(self) -> None:
        assert average_owner_earnings([]) is None


class TestTrailingGrowthPct:
    def test_growing_history(self, buffett_quality_cache: EdgarCache) -> None:
        records = historical_owner_earnings(
            buffett_quality_cache, "WONDERFUL", date(2024, 6, 30), years=5
        )
        g = trailing_growth_pct(records, lookback=5)
        # NI grows at 6.5%; OE = NI × constant ratio so growth ≈ 6.5%.
        assert g is not None
        assert 5.0 < g < 8.0

    def test_too_few_records(self) -> None:
        assert trailing_growth_pct([]) is None


class TestDcfPresentValue:
    def test_zero_growth_zero_discount_caps_at_terminal(self) -> None:
        # Degenerate r=0 should fall back to terminal value.
        pv = _dcf_present_value(
            base_oe=100.0,
            growth_pct=0.0,
            discount_pct=0.0,
            terminal_multiple=10.0,
            years=10,
        )
        assert pv == 1000.0  # 100 × 10

    def test_positive_growth_increases_pv(self) -> None:
        a = _dcf_present_value(
            base_oe=100.0,
            growth_pct=0.0,
            discount_pct=5.0,
            terminal_multiple=13.0,
            years=10,
        )
        b = _dcf_present_value(
            base_oe=100.0,
            growth_pct=5.0,
            discount_pct=5.0,
            terminal_multiple=13.0,
            years=10,
        )
        assert b > a

    def test_higher_discount_decreases_pv(self) -> None:
        cheap = _dcf_present_value(
            base_oe=100.0,
            growth_pct=5.0,
            discount_pct=4.0,
            terminal_multiple=13.0,
            years=10,
        )
        expensive = _dcf_present_value(
            base_oe=100.0,
            growth_pct=5.0,
            discount_pct=8.0,
            terminal_multiple=13.0,
            years=10,
        )
        assert cheap > expensive


class TestIntrinsicValue:
    def test_full_pipeline(self, buffett_quality_cache: EdgarCache) -> None:
        result = intrinsic_value(
            buffett_quality_cache, "WONDERFUL", date(2024, 6, 30)
        )
        assert result is not None
        assert result.intrinsic_value_usd > 0
        assert result.avg_owner_earnings_usd > 0
        # Capped growth.
        assert 0.0 <= result.growth_rate_pct <= 8.0
        assert result.discount_rate_pct == DEFAULT_DISCOUNT_RATE_PCT
        assert result.terminal_multiple == DEFAULT_TERMINAL_MULTIPLE
        assert result.years_of_history >= 3

    def test_no_history_returns_none(self, empty_cache: EdgarCache) -> None:
        assert (
            intrinsic_value(empty_cache, "NOTHING", date(2024, 6, 30))
            is None
        )


class TestMarginOfSafetyPct:
    def test_positive_mos(self) -> None:
        # IV $200B, mcap $150B → MoS = 25%
        assert margin_of_safety_pct(200_000_000_000, 150_000_000_000) == 25.0

    def test_negative_mos(self) -> None:
        # mcap > IV → negative MoS
        assert margin_of_safety_pct(100, 150) == -50.0

    def test_zero_iv_none(self) -> None:
        assert margin_of_safety_pct(0, 100) is None


def _fact(concept: str, value: float, fy: int) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace="us-gaap",
        unit="USD",
        value=value,
        period_start=date(fy, 1, 1),
        period_end=date(fy, 12, 31),
        filed=date(fy + 1, 2, 15),
        form="10-K",
        fiscal_year=fy,
        fiscal_period="FY",
        accession_number=f"acc-{concept}-{fy}",
    )


class TestFinancialsAreRefused:
    """OCF - capex measures deposit and premium inflows for a financial.
    Buffett's own float is a liability; capitalising it as owner
    earnings is precisely backwards."""

    def test_a_bank_yields_no_history(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "JPM",
            [
                _fact("NetCashProvidedByUsedInOperatingActivities", 50_000.0, 2025),
                _fact("PaymentsToAcquirePropertyPlantAndEquipment", 2_000.0, 2025),
            ],
        )

        assert historical_owner_earnings(cache, "JPM", date(2026, 8, 4)) == []

    def test_an_operating_company_is_unaffected(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "AAPL",
            [
                _fact("NetCashProvidedByUsedInOperatingActivities", 50_000.0, 2025),
                _fact("PaymentsToAcquirePropertyPlantAndEquipment", 2_000.0, 2025),
            ],
        )

        records = historical_owner_earnings(cache, "AAPL", date(2026, 8, 4))

        assert len(records) == 1
        assert records[0].owner_earnings == 48_000.0
