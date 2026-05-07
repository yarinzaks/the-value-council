"""Unit tests for cycle-aware ranking."""

from __future__ import annotations

from datetime import date

import pytest

from agents.marks.ranking import MarksScore, score_candidates, select_top_n
from agents.marks.temperature import (
    TemperatureAssessment,
    TemperatureSignals,
    assess_market_temperature,
)
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit


def _temperature(posture: str, score: float) -> TemperatureAssessment:
    """Build a TemperatureAssessment with a fixed posture for tests."""
    return TemperatureAssessment(
        as_of=date(2024, 6, 30),
        score=score,
        posture=posture,  # type: ignore[arg-type]
        signals=TemperatureSignals(
            universe_size=20,
            median_pe=18.0,
            frac_negative_ni=0.10,
            median_de=0.5,
            frac_high_de=0.20,
            median_yield_pct=2.0,
        ),
        votes={},
    )


class TestScoreEmpty:
    def test_empty(self, empty_cache: EdgarCache) -> None:
        out = score_candidates(
            [],
            as_of=date(2024, 6, 30),
            edgar_cache=empty_cache,
            temperature=_temperature("Neutral", 0.0),
        )
        assert out == []


class TestScoreNeutral:
    def test_qualifies_with_value_signature(
        self, fcf_cache: EdgarCache
    ) -> None:
        # PE = 5 → earnings yield 20%, very high — qualifies easily.
        f = make_pit(
            "QUALITY",
            eps_diluted=4.0,
            shares=100_000_000,
            total_equity=1_500_000_000,
            total_debt=300_000_000,  # D/E 0.2
            dividends_paid=-50_000_000,
        )
        # mcap = $2B; price = $20 → PE = 5
        cand = (f, 2_000_000_000.0, 20.0)
        scores = score_candidates(
            [cand],
            as_of=date(2024, 6, 30),
            edgar_cache=fcf_cache,
            temperature=_temperature("Neutral", 0.0),
        )
        assert len(scores) == 1
        s = scores[0]
        assert s.ticker == "QUALITY"
        assert s.earnings_yield_pct == pytest.approx(20.0)
        # FCF $200M / mcap $2B = 10% FCF yield
        assert s.fcf_yield_pct == pytest.approx(10.0)
        # Dividend yield: $50M / $2B = 2.5%
        assert s.dividend_yield_pct == pytest.approx(2.5)
        assert s.posture_at_score == "Neutral"
        assert s.total_score > 0


class TestScorePostureSensitivity:
    def test_hot_posture_rejects_high_de(self, empty_cache: EdgarCache) -> None:
        # In Hot posture, D/E > 0.6 hard-rejects.
        f = make_pit(
            "LEV",
            eps_diluted=2.0,
            shares=100_000_000,
            total_equity=1_000_000_000,
            total_debt=900_000_000,  # D/E 0.9
        )
        cand = (f, 1_000_000_000.0, 10.0)  # PE 5
        scores = score_candidates(
            [cand],
            as_of=date(2024, 6, 30),
            edgar_cache=empty_cache,
            temperature=_temperature("Hot", 6.0),
        )
        assert scores == []

    def test_cold_posture_accepts_high_de(self, empty_cache: EdgarCache) -> None:
        # Same candidate — Cold posture has no D/E hard reject; accepts.
        f = make_pit(
            "LEV",
            eps_diluted=2.0,
            shares=100_000_000,
            total_equity=1_000_000_000,
            total_debt=900_000_000,  # D/E 0.9
        )
        cand = (f, 1_000_000_000.0, 10.0)
        scores = score_candidates(
            [cand],
            as_of=date(2024, 6, 30),
            edgar_cache=empty_cache,
            temperature=_temperature("Cold", -6.0),
        )
        assert len(scores) == 1
        # The score will reflect the D/E penalty but not zero out.
        assert scores[0].total_score > 0

    def test_low_earnings_yield_filtered_in_hot(
        self, empty_cache: EdgarCache
    ) -> None:
        # Earnings yield 5% (PE 20) — below Hot's 6.5% floor.
        f = make_pit(
            "AVG",
            eps_diluted=1.0,
            shares=100_000_000,
            total_equity=1_000_000_000,
            total_debt=200_000_000,
        )
        cand = (f, 2_000_000_000.0, 20.0)  # PE 20 → eyld 5%
        scores = score_candidates(
            [cand],
            as_of=date(2024, 6, 30),
            edgar_cache=empty_cache,
            temperature=_temperature("Hot", 6.0),
        )
        assert scores == []

    def test_low_earnings_yield_accepted_in_cold(
        self, empty_cache: EdgarCache
    ) -> None:
        # Same candidate — Cold posture's eyld floor is 4% → passes.
        f = make_pit(
            "AVG",
            eps_diluted=1.0,
            shares=100_000_000,
            total_equity=1_000_000_000,
            total_debt=200_000_000,
        )
        cand = (f, 2_000_000_000.0, 20.0)  # PE 20 → eyld 5%
        scores = score_candidates(
            [cand],
            as_of=date(2024, 6, 30),
            edgar_cache=empty_cache,
            temperature=_temperature("Cold", -6.0),
        )
        assert len(scores) == 1


class TestSelectTopN:
    def _scores(self, totals: list[float]) -> list[MarksScore]:
        return [
            MarksScore(
                ticker=f"T{i}",
                price=10.0,
                market_cap=1e9,
                pe=10.0,
                earnings_yield_pct=10.0,
                fcf_yield_pct=8.0,
                dividend_yield_pct=2.0,
                debt_to_equity=0.4,
                net_income=1e8,
                posture_at_score="Neutral",
                total_score=t,
            )
            for i, t in enumerate(totals)
        ]

    def test_returns_first_n(self) -> None:
        scores = self._scores([20.0, 15.0, 10.0, 5.0, 1.0])
        out = select_top_n(scores, n=3)
        assert [s.ticker for s in out] == ["T0", "T1", "T2"]

    def test_zero_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], n=0)
