"""Unit tests for the market temperature assessment."""

from __future__ import annotations

from datetime import date

import pytest

from agents.marks.temperature import (
    Posture,
    PostureProfile,
    TemperatureAssessment,
    _vote_de,
    _vote_high_de_frac,
    _vote_neg_ni_frac,
    _vote_pe,
    _vote_yield,
    assess_market_temperature,
    profile_for,
)

from .conftest import make_pit


# ---- Per-vote function tests ----------------------------------------------
class TestVotePe:
    def test_strongly_cold(self) -> None:
        assert _vote_pe(10.0) == -2

    def test_cold(self) -> None:
        assert _vote_pe(13.0) == -1

    def test_neutral(self) -> None:
        assert _vote_pe(18.0) == 0

    def test_hot(self) -> None:
        assert _vote_pe(23.0) == 1

    def test_strongly_hot(self) -> None:
        assert _vote_pe(30.0) == 2


class TestVoteNegNiFrac:
    def test_strongly_cold_high_distress(self) -> None:
        # 50% of universe negative — widespread distress.
        assert _vote_neg_ni_frac(0.50) == -2

    def test_cold(self) -> None:
        assert _vote_neg_ni_frac(0.32) == -1

    def test_neutral(self) -> None:
        assert _vote_neg_ni_frac(0.20) == 0

    def test_hot(self) -> None:
        assert _vote_neg_ni_frac(0.08) == 1

    def test_strongly_hot_no_distress(self) -> None:
        assert _vote_neg_ni_frac(0.03) == 2


class TestVoteDe:
    def test_strongly_cold_deleveraged(self) -> None:
        assert _vote_de(0.20) == -2

    def test_strongly_hot_loose_lending(self) -> None:
        assert _vote_de(1.00) == 2


class TestVoteHighDeFrac:
    def test_strongly_cold(self) -> None:
        assert _vote_high_de_frac(0.05) == -2

    def test_strongly_hot(self) -> None:
        assert _vote_high_de_frac(0.50) == 2


class TestVoteYield:
    def test_strongly_cold_high_yields(self) -> None:
        assert _vote_yield(5.0) == -2

    def test_cold(self) -> None:
        assert _vote_yield(4.0) == -1

    def test_strongly_hot_compressed(self) -> None:
        assert _vote_yield(0.5) == 2


# ---- assess_market_temperature pipeline -----------------------------------
class TestAssessMarketTemperature:
    def test_empty_universe_neutral(self) -> None:
        result = assess_market_temperature([], as_of=date(2024, 6, 30))
        assert result.posture == "Neutral"
        assert result.score == 0.0
        assert result.signals.universe_size == 0

    def test_cold_universe(self) -> None:
        # Build a universe with VERY cold signals:
        #   - low PE (5)
        #   - many negative NI (50%)
        #   - low D/E (0.1)
        #   - low high-D/E fraction (none > 1.0)
        #   - high yields (5%+)
        candidates = []
        # 10 healthy cheap names with low PE + high yield
        for i in range(10):
            f = make_pit(
                f"CHEAP{i}",
                eps_diluted=4.0,  # PE = 20/4 = 5 at price 20
                total_equity=1_000_000_000,
                total_debt=100_000_000,
                dividends_paid=-50_000_000,  # 5% yield on $1B mcap
            )
            candidates.append((f, 1_000_000_000.0, 20.0))
        # 10 unprofitable names — distress signal
        for i in range(10):
            f = make_pit(
                f"DISTRESS{i}",
                eps_diluted=-1.0,
                net_income=-100_000_000,
                total_equity=500_000_000,
                total_debt=50_000_000,
                dividends_paid=0.0,
            )
            candidates.append((f, 500_000_000.0, 5.0))

        result = assess_market_temperature(
            candidates, as_of=date(2024, 6, 30)
        )
        assert result.posture in ("Cold", "Cool")
        assert result.score < 0
        assert result.signals.frac_negative_ni == pytest.approx(0.5)

    def test_hot_universe(self) -> None:
        # All names: high PE, no distress, leveraged, low yields
        candidates = []
        for i in range(20):
            f = make_pit(
                f"FROTHY{i}",
                eps_diluted=1.0,  # PE = 30 at $30
                net_income=100_000_000,
                total_equity=300_000_000,
                total_debt=300_000_000,  # D/E = 1.0
                dividends_paid=-3_000_000,  # 0.1% yield on $3B
            )
            candidates.append((f, 3_000_000_000.0, 30.0))
        result = assess_market_temperature(
            candidates, as_of=date(2024, 6, 30)
        )
        assert result.posture in ("Warm", "Hot")
        assert result.score > 0


# ---- profile_for ----------------------------------------------------------
class TestProfileFor:
    def test_cold_largest_size(self) -> None:
        p = profile_for("Cold")
        assert isinstance(p, PostureProfile)
        assert p.posture == "Cold"
        assert p.portfolio_size == 25
        assert p.deployed_fraction == 0.95

    def test_hot_smallest_size(self) -> None:
        p = profile_for("Hot")
        assert p.portfolio_size == 10
        assert p.deployed_fraction == 0.50

    def test_neutral_middle(self) -> None:
        p = profile_for("Neutral")
        assert p.portfolio_size == 18
        assert 0.7 < p.deployed_fraction < 0.9

    def test_size_decreases_as_posture_warms(self) -> None:
        sizes = [
            profile_for(p).portfolio_size
            for p in ("Cold", "Cool", "Neutral", "Warm", "Hot")
        ]
        # Strictly monotone decreasing.
        assert sizes == sorted(sizes, reverse=True)
        assert len(set(sizes)) == 5

    def test_deployment_decreases_as_posture_warms(self) -> None:
        deploys = [
            profile_for(p).deployed_fraction
            for p in ("Cold", "Cool", "Neutral", "Warm", "Hot")
        ]
        assert deploys == sorted(deploys, reverse=True)
