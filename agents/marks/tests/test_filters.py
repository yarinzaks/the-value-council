"""Unit tests for Marks filters."""

from __future__ import annotations

from datetime import date

import pytest

from agents.marks.filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    apply_quality_gates,
    debt_to_equity,
    passes_quality_gates,
)

from .conftest import make_pit


class TestDebtToEquity:
    def test_basic(self) -> None:
        f = make_pit("X", total_equity=1_000, total_debt=300)
        assert debt_to_equity(f) == pytest.approx(0.3)

    def test_zero_equity_none(self) -> None:
        f = make_pit("X", total_equity=0)
        assert debt_to_equity(f) is None


class TestPassesQualityGates:
    def test_clean_passes(self) -> None:
        f = make_pit("ACME")
        result = passes_quality_gates(
            f, market_cap_usd=10_000_000_000.0
        )
        assert result.passed, result.rejection_reason

    def test_share_class_rejected(self) -> None:
        f = make_pit("BRK-B")
        result = passes_quality_gates(
            f, market_cap_usd=900_000_000_000.0
        )
        assert not result.passed
        assert "share class" in (result.rejection_reason or "")

    def test_below_market_cap_rejected(self) -> None:
        f = make_pit("TINY")
        result = passes_quality_gates(
            f, market_cap_usd=100_000_000.0
        )
        assert not result.passed
        assert "market cap" in (result.rejection_reason or "")

    def test_negative_equity_rejected(self) -> None:
        f = make_pit("BURNED", total_equity=-100_000_000)
        result = passes_quality_gates(
            f, market_cap_usd=2_000_000_000.0
        )
        assert not result.passed
        assert "equity" in (result.rejection_reason or "")

    def test_negative_ni_rejected(self) -> None:
        f = make_pit("LOSS", net_income=-50_000_000)
        result = passes_quality_gates(
            f, market_cap_usd=2_000_000_000.0
        )
        assert not result.passed
        assert "net income" in (result.rejection_reason or "")

    def test_high_de_rejected(self) -> None:
        # Marks's loose ceiling is 1.0 — over 1.0 is rejected.
        f = make_pit("LEVERED", total_equity=500, total_debt=600)
        result = passes_quality_gates(
            f, market_cap_usd=2_000_000_000.0
        )
        assert not result.passed
        assert "D/E" in (result.rejection_reason or "")

    def test_de_at_one_passes(self) -> None:
        # D/E = 1.0 should pass the loose Marks ceiling exactly.
        f = make_pit("LEV", total_equity=1_000, total_debt=1_000)
        result = passes_quality_gates(
            f, market_cap_usd=2_000_000_000.0
        )
        assert result.passed


class TestApplyQualityGates:
    def test_only_passers(self) -> None:
        good = make_pit("GOOD")
        bad = make_pit("TINY")
        triples = [
            (good, 10_000_000_000.0, 100.0),
            (bad, 100_000_000.0, 1.0),
        ]
        out = apply_quality_gates(triples, as_of=date(2024, 6, 30))
        assert len(out) == 1
        assert out[0][0].ticker == "GOOD"


class TestDefaults:
    def test_defaults(self) -> None:
        assert DEFAULT_MIN_MARKET_CAP_USD == 500_000_000.0
        assert DEFAULT_MAX_DE == 1.0
