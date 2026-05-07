"""Unit tests for Klarman MoS-based ranking."""

from __future__ import annotations

from datetime import date

import pytest

from agents.klarman.ranking import (
    KlarmanScore,
    score_candidates,
    select_top_n,
)
from agents.klarman.valuation import DEFAULT_MIN_MARGIN_OF_SAFETY_PCT
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit


class TestScoreEmpty:
    def test_empty(self, empty_cache: EdgarCache) -> None:
        assert (
            score_candidates(
                [], as_of=date(2024, 6, 30), edgar_cache=empty_cache
            )
            == []
        )


class TestScorePipeline:
    def test_undervalued_qualifies(
        self, steady_fcf_cache: EdgarCache
    ) -> None:
        # STEADY: 5yr avg FCF ~$300M+, modest growth. With Klarman's
        # punitive 8% discount + 10× terminal, IV ~$3-5B.
        # Set mcap well below that to clear 30% MoS.
        f = make_pit("STEADY", shares=100_000_000)
        cand = (f, 1_500_000_000.0, 15.0)  # mcap $1.5B
        scores = score_candidates(
            [cand],
            as_of=date(2024, 6, 30),
            edgar_cache=steady_fcf_cache,
        )
        assert len(scores) == 1
        s = scores[0]
        assert s.ticker == "STEADY"
        assert s.margin_of_safety_pct >= DEFAULT_MIN_MARGIN_OF_SAFETY_PCT
        assert s.intrinsic_value_per_share > 15.0

    def test_overvalued_filtered(
        self, steady_fcf_cache: EdgarCache
    ) -> None:
        # Same FCF profile but mcap at $50B — way above any reasonable IV.
        f = make_pit("STEADY", shares=100_000_000)
        cand = (f, 50_000_000_000.0, 500.0)
        scores = score_candidates(
            [cand],
            as_of=date(2024, 6, 30),
            edgar_cache=steady_fcf_cache,
        )
        assert scores == []

    def test_no_history_filtered(self, empty_cache: EdgarCache) -> None:
        f = make_pit("EMPTY")
        cand = (f, 1_000_000_000.0, 10.0)
        scores = score_candidates(
            [cand], as_of=date(2024, 6, 30), edgar_cache=empty_cache
        )
        assert scores == []


class TestSelectTopN:
    def _scores(self, mos_values: list[float]) -> list[KlarmanScore]:
        return [
            KlarmanScore(
                ticker=f"T{i}",
                price=10.0,
                market_cap=1e9,
                intrinsic_value_usd=2e9,
                intrinsic_value_per_share=20.0,
                margin_of_safety_pct=m,
                avg_fcf_usd=1e8,
                growth_rate_pct=3.0,
                discount_rate_pct=8.0,
                debt_to_equity=0.3,
                net_income=1e8,
            )
            for i, m in enumerate(mos_values)
        ]

    def test_returns_first_n(self) -> None:
        scores = self._scores([60.0, 50.0, 40.0, 35.0, 30.0])
        out = select_top_n(scores, n=3)
        assert [s.ticker for s in out] == ["T0", "T1", "T2"]

    def test_zero_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], n=0)
