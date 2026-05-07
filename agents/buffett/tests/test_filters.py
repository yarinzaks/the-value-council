"""Unit tests for Buffett filters (Berkshire Acquisition Criteria)."""

from __future__ import annotations

from datetime import date

import pytest

from agents.buffett.filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_AVG_ROE_PCT,
    DEFAULT_MIN_MARKET_CAP_USD,
    EXCLUDED_SIC2,
    apply_quality_gates,
    avg_roe_5yr,
    debt_to_equity,
    has_consistent_earnings,
    has_consistent_ocf,
    is_simple_business,
    passes_quality_gates,
)
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit


class TestDebtToEquity:
    def test_uses_total_debt_when_present(self) -> None:
        f = make_pit("X", total_debt=1_000_000_000, long_term_debt=500_000_000)
        assert debt_to_equity(f) == pytest.approx(0.20)

    def test_falls_back_to_long_term_debt(self) -> None:
        f = make_pit("X", total_debt=None, long_term_debt=2_000_000_000)
        assert debt_to_equity(f) == pytest.approx(0.40)

    def test_zero_equity_none(self) -> None:
        f = make_pit("X", total_equity=0)
        assert debt_to_equity(f) is None

    def test_none_input(self) -> None:
        assert debt_to_equity(None) is None


class TestIsSimpleBusiness:
    def test_buffett_friendly_sic_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Beverages (SIC 2080) — passes.
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        f = make_pit("KO")
        assert is_simple_business(f) is True

    def test_excluded_sic_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Airlines (SIC 4512) — excluded.
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 4512
        )
        f = make_pit("AAL")
        assert is_simple_business(f) is False

    def test_unknown_sic_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No SIC: benefit of the doubt — LLM will catch in live mode.
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: None
        )
        f = make_pit("UNKNOWN")
        assert is_simple_business(f) is True

    def test_none_input_rejected(self) -> None:
        assert is_simple_business(None) is False


class TestExcludedSic2Coverage:
    def test_airlines_excluded(self) -> None:
        assert 45 in EXCLUDED_SIC2

    def test_oil_gas_excluded(self) -> None:
        assert 13 in EXCLUDED_SIC2

    def test_beverages_not_excluded(self) -> None:
        assert 20 not in EXCLUDED_SIC2  # SIC 2080 = beverages

    def test_banks_not_excluded(self) -> None:
        # SIC 60-67 are finance — Buffett heavily weights banks.
        for sic2 in (60, 61, 62, 63, 64, 67):
            assert sic2 not in EXCLUDED_SIC2


class TestHasConsistentEarnings:
    def test_clean_history_passes(self, buffett_quality_cache: EdgarCache) -> None:
        assert has_consistent_earnings(
            buffett_quality_cache, "WONDERFUL", date(2024, 6, 30), years=10
        )

    def test_no_history_fails(self, empty_cache: EdgarCache) -> None:
        assert not has_consistent_earnings(
            empty_cache, "NOTHING", date(2024, 6, 30), years=10
        )


class TestHasConsistentOcf:
    def test_clean_history_passes(self, buffett_quality_cache: EdgarCache) -> None:
        assert has_consistent_ocf(
            buffett_quality_cache, "WONDERFUL", date(2024, 6, 30), years=5
        )

    def test_no_history_fails(self, empty_cache: EdgarCache) -> None:
        assert not has_consistent_ocf(
            empty_cache, "NOTHING", date(2024, 6, 30), years=5
        )


class TestAvgRoe5yr:
    def test_clean_history(self, buffett_quality_cache: EdgarCache) -> None:
        roe = avg_roe_5yr(
            buffett_quality_cache, "WONDERFUL", date(2024, 6, 30), years=5
        )
        # NI ~1.5-1.7B / equity ~5.5-6.4B → 25%+ in recent years
        assert roe is not None
        assert roe > 15.0
        assert roe < 50.0  # sanity

    def test_insufficient_history_returns_none(
        self, empty_cache: EdgarCache
    ) -> None:
        assert (
            avg_roe_5yr(empty_cache, "NOTHING", date(2024, 6, 30), years=5)
            is None
        )


class TestPassesQualityGates:
    def test_clean_passes(
        self,
        buffett_quality_cache: EdgarCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        f = make_pit(
            "WONDERFUL",
            net_income=1_700_000_000,
            total_equity=6_400_000_000,
            total_debt=1_500_000_000,
        )
        result = passes_quality_gates(
            f,
            market_cap_usd=200_000_000_000.0,
            cache=buffett_quality_cache,
            as_of=date(2024, 6, 30),
        )
        assert result.passed, result.rejection_reason
        assert result.pass_size
        assert result.pass_simple_business
        assert result.pass_low_debt
        assert result.pass_earnings_consistency
        assert result.pass_ocf_consistency
        assert result.pass_roe

    def test_below_size_floor_rejected(
        self,
        buffett_quality_cache: EdgarCache,
    ) -> None:
        f = make_pit("SMALL")
        result = passes_quality_gates(
            f,
            market_cap_usd=1_000_000_000.0,  # $1B < $5B floor
            cache=buffett_quality_cache,
            as_of=date(2024, 6, 30),
        )
        assert not result.passed
        assert "market cap" in (result.rejection_reason or "")

    def test_share_class_rejected(
        self,
        buffett_quality_cache: EdgarCache,
    ) -> None:
        f = make_pit("BRK-B")
        result = passes_quality_gates(
            f,
            market_cap_usd=900_000_000_000.0,
            cache=buffett_quality_cache,
            as_of=date(2024, 6, 30),
        )
        assert not result.passed
        assert "share class" in (result.rejection_reason or "")

    def test_high_debt_rejected(
        self,
        buffett_quality_cache: EdgarCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        f = make_pit(
            "LEVERED",
            total_equity=5_000_000_000,
            total_debt=4_000_000_000,  # D/E = 0.8 > 0.5 ceiling
        )
        result = passes_quality_gates(
            f,
            market_cap_usd=50_000_000_000.0,
            cache=buffett_quality_cache,
            as_of=date(2024, 6, 30),
        )
        assert not result.passed
        assert "D/E" in (result.rejection_reason or "")

    def test_excluded_sic_rejected(
        self,
        buffett_quality_cache: EdgarCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Airlines.
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 4512
        )
        f = make_pit("AAL", sic_code="4512")
        result = passes_quality_gates(
            f,
            market_cap_usd=20_000_000_000.0,
            cache=buffett_quality_cache,
            as_of=date(2024, 6, 30),
        )
        assert not result.passed
        assert "SIC" in (result.rejection_reason or "")

    def test_negative_equity_rejected(
        self,
        buffett_quality_cache: EdgarCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        f = make_pit("BURNED", total_equity=-100_000_000)
        result = passes_quality_gates(
            f,
            market_cap_usd=20_000_000_000.0,
            cache=buffett_quality_cache,
            as_of=date(2024, 6, 30),
        )
        assert not result.passed
        assert "equity" in (result.rejection_reason or "")


class TestApplyQualityGates:
    def test_only_passers_returned(
        self,
        buffett_quality_cache: EdgarCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        good = make_pit("WONDERFUL")
        bad = make_pit("SMALL")
        triples = [
            (good, 200_000_000_000.0, 200.0),
            (bad, 1_000_000_000.0, 10.0),  # below size floor
        ]
        out = apply_quality_gates(
            triples, cache=buffett_quality_cache, as_of=date(2024, 6, 30)
        )
        assert len(out) == 1
        assert out[0][0].ticker == "WONDERFUL"


class TestDefaults:
    def test_defaults(self) -> None:
        assert DEFAULT_MIN_MARKET_CAP_USD == 5_000_000_000.0
        assert DEFAULT_MIN_AVG_ROE_PCT == 15.0
        assert DEFAULT_MAX_DE == 0.5
