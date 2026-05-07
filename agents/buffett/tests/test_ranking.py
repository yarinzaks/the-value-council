"""Unit tests for Buffett ranking (margin-of-safety scoring + select)."""

from __future__ import annotations

from datetime import date

import pytest

from agents.buffett.ranking import (
    BuffettScore,
    DEFAULT_MIN_MARGIN_OF_SAFETY_PCT,
    score_candidates,
    select_top_n,
)
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit


class TestScoreCandidatesEmpty:
    def test_empty_returns_empty(self, empty_cache: EdgarCache) -> None:
        assert (
            score_candidates(
                [], as_of=date(2024, 6, 30), edgar_cache=empty_cache
            )
            == []
        )


class TestScoreCandidatesPipeline:
    def test_undervalued_candidate_qualifies(
        self, buffett_quality_cache: EdgarCache
    ) -> None:
        # WONDERFUL has 12 years of growing OE; mcap set well below
        # any reasonable IV → strong MoS → qualifies.
        fin = make_pit("WONDERFUL", shares=1_000_000_000)
        # Price $20 × 1B shares = $20B mcap. With OE ~$1.5B+ and 5%
        # growth + 13× terminal, IV will be far above $20B.
        cand = (fin, 20_000_000_000.0, 20.0)
        scores = score_candidates(
            [cand],
            as_of=date(2024, 6, 30),
            edgar_cache=buffett_quality_cache,
        )
        assert len(scores) == 1
        s = scores[0]
        assert s.ticker == "WONDERFUL"
        assert s.margin_of_safety_pct >= DEFAULT_MIN_MARGIN_OF_SAFETY_PCT
        assert s.intrinsic_value_usd > 20_000_000_000.0
        assert s.intrinsic_value_per_share > 20.0
        assert s.avg_owner_earnings_usd > 0

    def test_overvalued_candidate_filtered(
        self, buffett_quality_cache: EdgarCache
    ) -> None:
        # Same fundamentals but at $5T mcap — way above any IV.
        fin = make_pit("WONDERFUL", shares=1_000_000_000)
        cand = (fin, 5_000_000_000_000.0, 5_000.0)
        scores = score_candidates(
            [cand],
            as_of=date(2024, 6, 30),
            edgar_cache=buffett_quality_cache,
        )
        assert scores == []

    def test_no_history_filtered(
        self, empty_cache: EdgarCache
    ) -> None:
        fin = make_pit("EMPTY")
        cand = (fin, 10_000_000_000.0, 10.0)
        scores = score_candidates(
            [cand], as_of=date(2024, 6, 30), edgar_cache=empty_cache
        )
        assert scores == []


class TestSelectTopN:
    def _scores(self, mos_values: list[float]) -> list[BuffettScore]:
        return [
            BuffettScore(
                ticker=f"T{i}",
                price=10.0,
                market_cap=1e9,
                intrinsic_value_usd=2e9,
                intrinsic_value_per_share=20.0,
                margin_of_safety_pct=m,
                avg_owner_earnings_usd=1e8,
                growth_rate_pct=5.0,
                discount_rate_pct=5.0,
                avg_roe_5yr_pct=18.0,
                debt_to_equity=0.3,
                net_income=1e8,
            )
            for i, m in enumerate(mos_values)
        ]

    def test_returns_first_n(self) -> None:
        scores = self._scores([50.0, 40.0, 30.0, 20.0, 15.0])
        out = select_top_n(scores, n=3)
        assert [s.ticker for s in out] == ["T0", "T1", "T2"]

    def test_n_larger_than_available(self) -> None:
        scores = self._scores([50.0, 40.0])
        assert len(select_top_n(scores, n=10)) == 2

    def test_zero_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], n=0)

    def test_negative_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], n=-3)
