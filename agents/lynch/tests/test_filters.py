"""Unit tests for Lynch filters."""

from __future__ import annotations

from datetime import date

import pytest

from agents.lynch.filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    apply_quality_gates,
    debt_to_equity,
    dividend_yield_pct,
    has_consistent_earnings,
    latest_free_cash_flow,
    passes_quality_gates,
)
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit


class TestDebtToEquity:
    def test_basic(self) -> None:
        f = make_pit("X", total_equity=1_000, total_debt=300)
        assert debt_to_equity(f) == pytest.approx(0.3)

    def test_zero_equity_none(self) -> None:
        f = make_pit("X", total_equity=0)
        assert debt_to_equity(f) is None


class TestDividendYieldPct:
    def test_basic(self) -> None:
        f = make_pit("X", dividends_paid=-50_000_000)
        # mcap $1B → yield 5%
        assert dividend_yield_pct(1_000_000_000.0, f) == pytest.approx(5.0)

    def test_no_div(self) -> None:
        f = make_pit("X", dividends_paid=None)
        assert dividend_yield_pct(1_000_000_000.0, f) == 0.0

    def test_zero_mcap_none(self) -> None:
        f = make_pit("X")
        assert dividend_yield_pct(0, f) is None


class TestHasConsistentEarnings:
    def test_clean_history_passes(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        assert has_consistent_earnings(
            fast_grower_cache, "FASTY", date(2024, 6, 30)
        )

    def test_no_history_fails(self, empty_cache: EdgarCache) -> None:
        assert not has_consistent_earnings(
            empty_cache, "NOTHING", date(2024, 6, 30)
        )


class TestLatestFreeCashFlow:
    def test_positive(self, fast_grower_cache: EdgarCache) -> None:
        fcf = latest_free_cash_flow(
            fast_grower_cache, "FASTY", date(2024, 6, 30)
        )
        assert fcf is not None and fcf > 0


class TestPassesQualityGates:
    def test_clean_passes(self, fast_grower_cache: EdgarCache) -> None:
        f = make_pit("FASTY", shares=100_000_000)
        result = passes_quality_gates(
            f,
            market_cap_usd=2_000_000_000.0,
            cache=fast_grower_cache,
            as_of=date(2024, 6, 30),
        )
        assert result.passed, result.rejection_reason

    def test_share_class_rejected(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        f = make_pit("BRK-B")
        result = passes_quality_gates(
            f,
            market_cap_usd=900_000_000_000.0,
            cache=fast_grower_cache,
            as_of=date(2024, 6, 30),
        )
        assert not result.passed
        assert "share class" in (result.rejection_reason or "")

    def test_below_market_cap_rejected(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        f = make_pit("TINY")
        result = passes_quality_gates(
            f,
            market_cap_usd=100_000_000.0,  # $100M < $300M floor
            cache=fast_grower_cache,
            as_of=date(2024, 6, 30),
        )
        assert not result.passed
        assert "market cap" in (result.rejection_reason or "")

    def test_high_debt_rejected(self, fast_grower_cache: EdgarCache) -> None:
        f = make_pit("LEVERED", total_equity=1_000, total_debt=800)
        result = passes_quality_gates(
            f,
            market_cap_usd=1_000_000_000.0,
            cache=fast_grower_cache,
            as_of=date(2024, 6, 30),
        )
        assert not result.passed
        assert "D/E" in (result.rejection_reason or "")


class TestApplyQualityGates:
    def test_only_passers(self, fast_grower_cache: EdgarCache) -> None:
        good = make_pit("FASTY", shares=100_000_000)
        bad = make_pit("TINY")
        triples = [
            (good, 2_000_000_000.0, 20.0),
            (bad, 100_000_000.0, 1.0),
        ]
        out = apply_quality_gates(
            triples, cache=fast_grower_cache, as_of=date(2024, 6, 30)
        )
        assert len(out) == 1
        assert out[0][0].ticker == "FASTY"


class TestDefaults:
    def test_defaults(self) -> None:
        assert DEFAULT_MIN_MARKET_CAP_USD == 300_000_000.0
        assert DEFAULT_MAX_DE == 0.5
