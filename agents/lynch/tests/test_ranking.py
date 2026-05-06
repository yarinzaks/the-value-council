"""Unit tests for Lynch PEG-based ranking + select_top_n."""

from __future__ import annotations

from datetime import date

import pytest

from agents.lynch.ranking import (
    FAST_GROWER_MAX_PEG,
    LynchScore,
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
    def test_fast_grower_low_peg_qualifies(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        # FASTY EPS at FY2023 ≈ 0.5 × 1.25^11 ≈ 5.96. P/E at $60 ≈ 10.
        # 25% growth → PEG ≈ 0.40 → strong buy zone.
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,  # no dividend; pure Fast Grower
        )
        cand = (f, 6_000_000_000.0, 60.0)
        scores = score_candidates(
            [cand], as_of=date(2024, 6, 30), edgar_cache=fast_grower_cache
        )
        assert len(scores) == 1
        s = scores[0]
        assert s.ticker == "FASTY"
        assert s.lynch_category == "Fast Grower"
        assert s.peg < FAST_GROWER_MAX_PEG
        assert s.peg_zone in ("strong_buy", "buy")
        assert s.suggested_position_size_pct == 5.0

    def test_fast_grower_high_peg_filtered(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        # Same EPS but priced at $300 → P/E ~50, growth 25 → PEG=2.0.
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,
        )
        cand = (f, 30_000_000_000.0, 300.0)
        scores = score_candidates(
            [cand], as_of=date(2024, 6, 30), edgar_cache=fast_grower_cache
        )
        assert scores == []

    def test_stalwart_classified(
        self, stalwart_cache: EdgarCache
    ) -> None:
        # STEADY EPS FY2023 ≈ 2.0 × 1.12^11 ≈ 6.94. Price $70 → P/E 10.
        # Growth 12% → PEG 0.83 → Stalwart territory at $5B+ mcap.
        f = make_pit(
            "STEADY",
            eps_diluted=6.94,
            shares=100_000_000,
            dividends_paid=-30_000_000,  # 0.43% yield, immaterial
        )
        cand = (f, 7_000_000_000.0, 70.0)  # > $5B → Stalwart
        scores = score_candidates(
            [cand], as_of=date(2024, 6, 30), edgar_cache=stalwart_cache
        )
        assert len(scores) == 1
        assert scores[0].lynch_category == "Stalwart"

    def test_no_growth_history_filtered(
        self, empty_cache: EdgarCache
    ) -> None:
        f = make_pit("EMPTY")
        cand = (f, 1_000_000_000.0, 10.0)
        scores = score_candidates(
            [cand], as_of=date(2024, 6, 30), edgar_cache=empty_cache
        )
        assert scores == []


class TestSelectTopN:
    def _scores(self, pegs: list[float]) -> list[LynchScore]:
        return [
            LynchScore(
                ticker=f"T{i}",
                price=10.0,
                market_cap=1e9,
                pe=20.0,
                growth_rate_5yr_pct=20.0,
                growth_rate_3yr_pct=22.0,
                growth_acceleration_pct=2.0,
                dividend_yield_pct=1.0,
                peg=p,
                pegy=p,
                debt_to_equity=0.3,
                net_income=1e8,
                lynch_category="Fast Grower",
                peg_zone="buy",
                suggested_position_size_pct=5.0,
            )
            for i, p in enumerate(pegs)
        ]

    def test_returns_first_n(self) -> None:
        scores = self._scores([0.4, 0.5, 0.7, 0.9, 1.0])
        out = select_top_n(scores, n=3)
        assert [s.ticker for s in out] == ["T0", "T1", "T2"]

    def test_zero_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], n=0)

    def test_negative_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], n=-1)
