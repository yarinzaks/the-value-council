"""Unit tests for conservative DCF + Margin of Safety."""

from __future__ import annotations

from datetime import date

import pytest

from agents.klarman.valuation import (
    DEFAULT_DISCOUNT_RATE_PCT,
    DEFAULT_MAX_GROWTH_PCT,
    DEFAULT_MIN_GROWTH_PCT,
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
        # STEADY's OCF compounds at 3% against flat $100M capex, so FCF
        # compounds at 3.8% — under the 5% cap, and applied as measured.
        # The old assertion was a range that a floored negative would
        # also have satisfied.
        assert result.growth_rate_pct == pytest.approx(3.84, abs=0.05)
        assert result.discount_rate_pct == DEFAULT_DISCOUNT_RATE_PCT
        assert result.terminal_multiple == DEFAULT_TERMINAL_MULTIPLE

    def test_no_history_none(self, empty_cache: EdgarCache) -> None:
        assert (
            intrinsic_value(empty_cache, "NOTHING", date(2024, 6, 30))
            is None
        )


class TestDeclineIsCharged:
    """The applied growth rate used to be ``max(0.0, min(raw, cap))``.

    The cap refuses to credit growth — conservative. The floor refused
    to charge for shrinkage, which is the opposite, and it hit the one
    population that most needs charging: over a 1,101-ticker sample of
    the live universe, 109 of the 239 DCF-valuable names (46%) had
    shrinking FCF and every one was valued as flat, at 1.95x the
    intrinsic value a charged projection gives.
    """

    def test_a_shrinking_business_is_projected_shrinking(
        self, mild_decline_cache: EdgarCache
    ) -> None:
        result = intrinsic_value(mild_decline_cache, "FADING", date(2024, 6, 30))

        assert result is not None
        assert result.growth_rate_pct == pytest.approx(-5.0, abs=0.3)

    def test_the_decline_lowers_the_valuation(
        self, mild_decline_cache: EdgarCache, steady_fcf_cache: EdgarCache
    ) -> None:
        # Same machinery, same discount rate and terminal multiple. The
        # only difference is the sign of the growth term, and it has to
        # move the answer down — under the old floor it did not move it
        # at all.
        fading = intrinsic_value(mild_decline_cache, "FADING", date(2024, 6, 30))
        steady = intrinsic_value(steady_fcf_cache, "STEADY", date(2024, 6, 30))

        assert fading is not None and steady is not None
        per_dollar_fading = fading.intrinsic_value_usd / fading.avg_fcf_usd
        per_dollar_steady = steady.intrinsic_value_usd / steady.avg_fcf_usd
        assert per_dollar_fading < per_dollar_steady

    def test_the_note_records_the_decline(
        self, mild_decline_cache: EdgarCache
    ) -> None:
        result = intrinsic_value(mild_decline_cache, "FADING", date(2024, 6, 30))

        assert result is not None
        assert any("shrinking" in n for n in result.notes)

    def test_a_steep_decline_is_refused_not_clamped(
        self, steep_decline_cache: EdgarCache
    ) -> None:
        # Clamping up to the floor would RAISE the intrinsic value —
        # the exact failure being fixed. At -30%/yr the ten-year
        # terminal FCF is 2.8% of base and the terminal multiple does
        # all the work, so there is no defensible number to return.
        assert (
            intrinsic_value(steep_decline_cache, "SINKING", date(2024, 6, 30))
            is None
        )

    def test_negative_latest_fcf_is_refused(
        self, collapsed_fcf_cache: EdgarCache
    ) -> None:
        # The five-year average is still positive here, so the avg>0
        # guard passes it through. There is no positive base to compound
        # from: a DCF is a category error, not a low valuation.
        records = historical_fcf(collapsed_fcf_cache, "BURNING", date(2024, 6, 30))
        assert average_fcf(records) is not None and average_fcf(records) > 0

        assert (
            intrinsic_value(collapsed_fcf_cache, "BURNING", date(2024, 6, 30))
            is None
        )

    def test_the_refusal_threshold_is_configurable(
        self, steep_decline_cache: EdgarCache
    ) -> None:
        # A caller willing to project a 30% decline gets a number, and
        # it is charged for the decline rather than floored.
        result = intrinsic_value(
            steep_decline_cache,
            "SINKING",
            date(2024, 6, 30),
            min_growth_pct=-40.0,
        )

        assert result is not None
        assert result.growth_rate_pct == pytest.approx(-30.0, abs=0.5)

    def test_growth_is_still_capped(self, steady_fcf_cache: EdgarCache) -> None:
        # Removing the floor must not have removed the ceiling.
        result = intrinsic_value(
            steady_fcf_cache, "STEADY", date(2024, 6, 30), max_growth_pct=1.0
        )

        assert result is not None
        assert result.growth_rate_pct == 1.0
        assert any("capped" in n for n in result.notes)

    def test_the_floor_is_minus_twenty(self) -> None:
        assert DEFAULT_MIN_GROWTH_PCT == -20.0


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
        # Buffett uses 13x; Klarman uses 10x.
        assert DEFAULT_TERMINAL_MULTIPLE == 10.0
