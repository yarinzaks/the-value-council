"""Unit tests for tier-based ranking."""

from __future__ import annotations

from datetime import date

import pytest

from agents.fisher.quality_score import QualityScore
from agents.fisher.ranking import (
    FisherScore,
    TIER_A_MAX_PE,
    TIER_A_POSITION_PCT,
    TIER_B_MAX_PE,
    TIER_B_POSITION_PCT,
    score_candidates,
    select_top_n,
)
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
    def test_5_5_qualifies_tier_a(
        self, fisher_quality_cache: EdgarCache
    ) -> None:
        # QUALITY ticker passes all 5 quant points.
        f = make_pit(
            "QUALITY",
            revenue=5_000_000_000,
            operating_income=1_000_000_000,
        )
        # P/E = $80 / $4 = 20 (under Tier A's 35 ceiling).
        cand = (f, 16_000_000_000.0, 80.0)
        scores = score_candidates(
            [cand],
            as_of=date(2024, 6, 30),
            edgar_cache=fisher_quality_cache,
        )
        assert len(scores) == 1
        s = scores[0]
        assert s.ticker == "QUALITY"
        assert s.tier == "A"
        assert s.quality_points == 5
        assert s.suggested_position_size_pct == TIER_A_POSITION_PCT

    def test_pe_above_ceiling_filtered(
        self, fisher_quality_cache: EdgarCache
    ) -> None:
        # Same QUALITY but priced at PE = 50 — above Tier A's 35 cap.
        f = make_pit(
            "QUALITY",
            revenue=5_000_000_000,
            operating_income=1_000_000_000,
        )
        cand = (f, 40_000_000_000.0, 200.0)  # PE = 50
        scores = score_candidates(
            [cand],
            as_of=date(2024, 6, 30),
            edgar_cache=fisher_quality_cache,
        )
        assert scores == []

    def test_no_history_below_4_filtered(
        self, empty_cache: EdgarCache
    ) -> None:
        # No history → most points fail → quality_points < 4 → reject.
        f = make_pit(
            "EMPTY",
            revenue=5_000_000_000,
            operating_income=1_000_000_000,
        )
        cand = (f, 16_000_000_000.0, 80.0)
        scores = score_candidates(
            [cand], as_of=date(2024, 6, 30), edgar_cache=empty_cache
        )
        assert scores == []


class TestSelectTopN:
    def _scores(
        self, items: list[tuple[str, str, int, float]]
    ) -> list[FisherScore]:
        out = []
        for tic, tier, points, pe in items:
            qs = QualityScore(
                ticker=tic,
                point_1_market_potential=True,
                point_3_rd_effectiveness=True,
                point_5_profit_margins=True,
                point_6_margin_maintenance=points >= 5,
                point_13_equity_dilution=points >= 4,
                points_passed=points,
                revenue_cagr_5yr_pct=12.0,
                rd_to_revenue_pct=8.0,
                operating_margin_pct=20.0,
                margin_trend_5yr_bps=200.0,
                share_count_change_5yr_pct=0.0,
            )
            out.append(
                FisherScore(
                    ticker=tic,
                    price=10.0,
                    market_cap=1e9,
                    pe=pe,
                    quality_points=points,
                    tier=tier,  # type: ignore[arg-type]
                    suggested_position_size_pct=(
                        TIER_A_POSITION_PCT
                        if tier == "A"
                        else TIER_B_POSITION_PCT
                    ),
                    debt_to_equity=0.3,
                    net_income=1e8,
                    quality_score=qs,
                )
            )
        return out

    def test_returns_first_n(self) -> None:
        scores = self._scores(
            [
                ("A1", "A", 5, 15.0),
                ("A2", "A", 5, 20.0),
                ("B1", "B", 4, 12.0),
                ("B2", "B", 4, 18.0),
            ]
        )
        out = select_top_n(scores, n=2)
        assert [s.ticker for s in out] == ["A1", "A2"]

    def test_zero_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], n=0)


class TestTierConstants:
    def test_constants(self) -> None:
        assert TIER_A_POSITION_PCT == 12.0
        assert TIER_B_POSITION_PCT == 6.0
        assert TIER_A_MAX_PE == 35.0
        assert TIER_B_MAX_PE == 25.0
