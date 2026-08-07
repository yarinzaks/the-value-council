"""Unit tests for Owner Earnings + DCF intrinsic value."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agents.buffett.owner_earnings import (
    DEFAULT_DISCOUNT_RATE_PCT,
    DEFAULT_TERMINAL_MULTIPLE,
    _dcf_present_value,
    average_owner_earnings,
    historical_owner_earnings,
    intrinsic_value,
    maintenance_capex,
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


class TestMaintenanceCapex:
    """Greenwald's split. PP&E intensity — plant per dollar of sales —
    is the capital it takes to support a dollar of revenue; times the
    year's revenue growth, that is the capital spent *growing*. The rest
    was spent standing still."""

    def test_a_growing_business_splits_its_capex(self) -> None:
        # 50c of plant per $1 of sales, revenue up $100 → $50 of the
        # $120 spent was growth; $70 was upkeep.
        out = maintenance_capex(
            capex=120.0, ppe_net=500.0, revenue=1_100.0, prior_revenue=1_000.0
        )
        assert out == pytest.approx(120.0 - (500.0 / 1_100.0) * 100.0)

    def test_flat_revenue_means_all_capex_is_maintenance(self) -> None:
        out = maintenance_capex(
            capex=120.0, ppe_net=500.0, revenue=1_000.0, prior_revenue=1_000.0
        )
        assert out == pytest.approx(120.0)

    def test_a_revenue_decline_does_not_credit_capital_back(self) -> None:
        # Growth capex clamps at zero: shrinking does not hand the owner
        # money back.
        out = maintenance_capex(
            capex=120.0, ppe_net=500.0, revenue=800.0, prior_revenue=1_000.0
        )
        assert out == pytest.approx(120.0)

    def test_growth_capex_cannot_exceed_what_was_spent(self) -> None:
        # Explosive growth on a small capex budget: maintenance floors
        # at zero rather than going negative.
        out = maintenance_capex(
            capex=10.0, ppe_net=5_000.0, revenue=2_000.0, prior_revenue=1_000.0
        )
        assert out == pytest.approx(0.0)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"ppe_net": None, "revenue": 1_000.0, "prior_revenue": 900.0},
            {"ppe_net": 500.0, "revenue": None, "prior_revenue": 900.0},
            {"ppe_net": 500.0, "revenue": 1_000.0, "prior_revenue": None},
            {"ppe_net": 500.0, "revenue": 0.0, "prior_revenue": 900.0},
        ],
    )
    def test_missing_or_unusable_inputs_yield_none(self, kwargs: dict) -> None:
        assert maintenance_capex(capex=100.0, **kwargs) is None


class TestOwnerEarningsBasis:
    """OE was implemented as OCF minus capex, which is free cash flow.
    For a growing business the two diverge by exactly the growth capex —
    the amount Buffett argues should *not* be charged against the owner.
    Measured across 300 cached tickers: 68% of annual records can now be
    built on the real definition, and OE exceeds FCF by more than 5% in
    42% of them."""

    AS_OF = date(2026, 8, 4)

    def _cache(self, tmp_path: Path, *facts: XbrlFact) -> EdgarCache:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("ACME", list(facts))
        return cache

    def test_the_full_inputs_give_buffetts_definition(
        self, tmp_path: Path
    ) -> None:
        cache = self._cache(
            tmp_path,
            _fact("NetCashProvidedByUsedInOperatingActivities", 1_000.0, 2025),
            _fact("PaymentsToAcquirePropertyPlantAndEquipment", 300.0, 2025),
            _fact("NetIncomeLoss", 700.0, 2025),
            _fact("DepreciationDepletionAndAmortization", 200.0, 2025),
            _fact("PropertyPlantAndEquipmentNet", 500.0, 2025),
            _fact("Revenues", 1_100.0, 2025),
            _fact("Revenues", 1_000.0, 2024),
        )

        rec = historical_owner_earnings(cache, "ACME", self.AS_OF)[0]

        assert rec.basis == "greenwald"
        # growth capex = (500/1100) x 100 = 45.45; maintenance = 254.55
        assert rec.maintenance_capex == pytest.approx(300.0 - (500 / 1100) * 100)
        assert rec.owner_earnings == pytest.approx(
            700.0 + 200.0 - rec.maintenance_capex
        )
        # ...and it is not free cash flow.
        assert rec.free_cash_flow == pytest.approx(700.0)
        assert rec.owner_earnings != pytest.approx(rec.free_cash_flow)

    def test_missing_inputs_fall_back_to_free_cash_flow(
        self, tmp_path: Path
    ) -> None:
        cache = self._cache(
            tmp_path,
            _fact("NetCashProvidedByUsedInOperatingActivities", 1_000.0, 2025),
            _fact("PaymentsToAcquirePropertyPlantAndEquipment", 300.0, 2025),
        )

        rec = historical_owner_earnings(cache, "ACME", self.AS_OF)[0]

        assert rec.basis == "free_cash_flow"
        assert rec.owner_earnings == pytest.approx(700.0)
        assert rec.maintenance_capex is None

    def test_legs_from_different_fiscal_years_are_refused(
        self, tmp_path: Path
    ) -> None:
        # Summing net income from one year with D&A from another is
        # arithmetic on unrelated periods.
        cache = self._cache(
            tmp_path,
            _fact("NetCashProvidedByUsedInOperatingActivities", 1_000.0, 2025),
            _fact("PaymentsToAcquirePropertyPlantAndEquipment", 300.0, 2025),
            _fact("NetIncomeLoss", 700.0, 2022),
            _fact("DepreciationDepletionAndAmortization", 200.0, 2025),
            _fact("PropertyPlantAndEquipmentNet", 500.0, 2025),
            _fact("Revenues", 1_100.0, 2025),
            _fact("Revenues", 1_000.0, 2024),
        )

        rec = historical_owner_earnings(cache, "ACME", self.AS_OF)[0]

        assert rec.basis == "free_cash_flow"

    def test_free_cash_flow_is_always_recorded(self, tmp_path: Path) -> None:
        # So a reader can compare the two rather than guess which they
        # are looking at.
        cache = self._cache(
            tmp_path,
            _fact("NetCashProvidedByUsedInOperatingActivities", 1_000.0, 2025),
            _fact("PaymentsToAcquirePropertyPlantAndEquipment", 300.0, 2025),
            _fact("NetIncomeLoss", 700.0, 2025),
            _fact("DepreciationDepletionAndAmortization", 200.0, 2025),
            _fact("PropertyPlantAndEquipmentNet", 500.0, 2025),
            _fact("Revenues", 1_100.0, 2025),
            _fact("Revenues", 1_000.0, 2024),
        )

        rec = historical_owner_earnings(cache, "ACME", self.AS_OF)[0]

        assert rec.free_cash_flow == pytest.approx(rec.ocf - rec.capex)
