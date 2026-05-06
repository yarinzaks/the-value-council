"""Unit tests for PEG / PEGY math + growth-rate sourcing."""

from __future__ import annotations

from datetime import date

import pytest

from agents.lynch.peg import (
    PEG_BUY,
    PEG_HOLD,
    PEG_STRONG_BUY,
    acceleration_pct,
    peg_buy_zone,
    peg_for,
    peg_ratio,
    pegy_ratio,
    trailing_eps_cagr_pct,
)
from core.data.edgar_cache import EdgarCache


class TestPegRatio:
    def test_pe_equals_growth_is_one(self) -> None:
        assert peg_ratio(20.0, 20.0) == pytest.approx(1.0)

    def test_pe_below_growth_is_under_one(self) -> None:
        assert peg_ratio(10.0, 20.0) == pytest.approx(0.5)

    def test_pe_above_growth_is_over_one(self) -> None:
        assert peg_ratio(30.0, 15.0) == pytest.approx(2.0)

    def test_negative_growth_returns_none(self) -> None:
        assert peg_ratio(20.0, -5.0) is None

    def test_zero_growth_returns_none(self) -> None:
        assert peg_ratio(20.0, 0.0) is None

    def test_zero_pe_returns_none(self) -> None:
        assert peg_ratio(0.0, 10.0) is None

    def test_none_inputs(self) -> None:
        assert peg_ratio(None, 10.0) is None
        assert peg_ratio(20.0, None) is None


class TestPegyRatio:
    def test_includes_dividend_yield(self) -> None:
        # P/E 20, growth 8%, yield 4% → PEGY = 20/12 ≈ 1.67
        assert pegy_ratio(20.0, 8.0, 4.0) == pytest.approx(20.0 / 12.0)

    def test_zero_yield_equals_peg(self) -> None:
        assert pegy_ratio(20.0, 10.0, 0.0) == pytest.approx(2.0)

    def test_none_yield_treated_as_zero(self) -> None:
        assert pegy_ratio(20.0, 10.0, None) == pytest.approx(2.0)

    def test_negative_combined_returns_none(self) -> None:
        # growth -3% + yield 1% = -2% → undefined
        assert pegy_ratio(20.0, -3.0, 1.0) is None


class TestPegBuyZone:
    def test_strong_buy(self) -> None:
        assert peg_buy_zone(0.3) == "strong_buy"

    def test_buy_at_one(self) -> None:
        assert peg_buy_zone(1.0) == "buy"

    def test_hold(self) -> None:
        assert peg_buy_zone(1.5) == "hold"

    def test_avoid(self) -> None:
        assert peg_buy_zone(2.5) == "avoid"

    def test_none(self) -> None:
        assert peg_buy_zone(None) == "n/a"


class TestPegFor:
    def test_full_record(self) -> None:
        result = peg_for(pe=20.0, growth_rate_pct=10.0, dividend_yield_pct=2.0)
        assert result is not None
        assert result.peg == 2.0
        assert result.pegy == pytest.approx(20.0 / 12.0)

    def test_negative_growth_returns_none(self) -> None:
        assert (
            peg_for(pe=20.0, growth_rate_pct=-5.0, dividend_yield_pct=2.0)
            is None
        )


class TestTrailingEpsCagrPct:
    def test_fast_grower_25pct(self, fast_grower_cache: EdgarCache) -> None:
        cagr = trailing_eps_cagr_pct(
            fast_grower_cache, "FASTY", date(2024, 6, 30), years=5
        )
        # FASTY: EPS grows 25% per year by construction.
        assert cagr is not None
        assert 23.0 < cagr < 27.0

    def test_stalwart_12pct(self, stalwart_cache: EdgarCache) -> None:
        cagr = trailing_eps_cagr_pct(
            stalwart_cache, "STEADY", date(2024, 6, 30), years=5
        )
        assert cagr is not None
        assert 10.0 < cagr < 14.0

    def test_no_history_none(self, empty_cache: EdgarCache) -> None:
        assert (
            trailing_eps_cagr_pct(
                empty_cache, "EMPTY", date(2024, 6, 30), years=5
            )
            is None
        )


class TestAcceleration:
    def test_constant_growth_acceleration_zero(
        self, stalwart_cache: EdgarCache
    ) -> None:
        # Constant 12% growth → 3yr ≈ 5yr → accel ≈ 0
        accel = acceleration_pct(
            stalwart_cache, "STEADY", date(2024, 6, 30)
        )
        assert accel is not None
        assert abs(accel) < 1.0


class TestThresholds:
    def test_buy_zones(self) -> None:
        assert PEG_STRONG_BUY == 0.5
        assert PEG_BUY == 1.0
        assert PEG_HOLD == 2.0
