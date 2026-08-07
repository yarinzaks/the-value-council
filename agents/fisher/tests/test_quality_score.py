"""Unit tests for Fisher 5-point quantitative score."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agents.fisher.quality_score import (
    DEFAULT_MAX_SHARE_DILUTION_PCT_5YR,
    DEFAULT_MIN_OPERATING_MARGIN_PCT,
    DEFAULT_MIN_RD_TO_REVENUE_PCT,
    DEFAULT_MIN_REVENUE_CAGR_PCT,
    margin_trend_5yr_bps,
    operating_margin_pct,
    rd_to_revenue_pct,
    revenue_cagr_5yr_pct,
    score_quality,
    share_count_change_5yr_pct,
)
from core.data.edgar_cache import EdgarCache

from .conftest import make_fact, make_pit


class TestRevenueCagr:
    def test_growing_history(self, fisher_quality_cache: EdgarCache) -> None:
        # 12% per year by construction.
        c = revenue_cagr_5yr_pct(
            fisher_quality_cache, "QUALITY", date(2024, 6, 30)
        )
        assert c is not None
        assert 10.0 < c < 14.0

    def test_no_history_none(self, empty_cache: EdgarCache) -> None:
        assert (
            revenue_cagr_5yr_pct(
                empty_cache, "NOTHING", date(2024, 6, 30)
            )
            is None
        )


class TestRdToRevenue:
    def test_value(self, fisher_quality_cache: EdgarCache) -> None:
        # By construction R&D = 8% of revenue.
        r = rd_to_revenue_pct(
            fisher_quality_cache, "QUALITY", date(2024, 6, 30)
        )
        assert r is not None
        assert 7.5 < r < 8.5

    def test_no_data_none(self, empty_cache: EdgarCache) -> None:
        assert (
            rd_to_revenue_pct(empty_cache, "NOTHING", date(2024, 6, 30))
            is None
        )


class TestOperatingMarginPct:
    def test_basic(self) -> None:
        f = make_pit(
            "X", revenue=10_000_000_000, operating_income=2_000_000_000
        )
        assert operating_margin_pct(f) == pytest.approx(20.0)

    def test_zero_revenue_none(self) -> None:
        f = make_pit("X", revenue=0, operating_income=0)
        assert operating_margin_pct(f) is None


class TestMarginTrend:
    def test_expanding(self, fisher_quality_cache: EdgarCache) -> None:
        # Margin grows ~50bps/year by construction → +250bps over 5yr.
        t = margin_trend_5yr_bps(
            fisher_quality_cache, "QUALITY", date(2024, 6, 30)
        )
        assert t is not None
        assert t > 0
        assert 200 < t < 350


class TestShareCountChange:
    def test_flat(self, fisher_quality_cache: EdgarCache) -> None:
        # Constant 200M shares → 0% change.
        c = share_count_change_5yr_pct(
            fisher_quality_cache, "QUALITY", date(2024, 6, 30)
        )
        assert c is not None
        assert abs(c) < 0.001


class TestScoreQuality:
    def test_quality_passes_all_5(
        self, fisher_quality_cache: EdgarCache
    ) -> None:
        f = make_pit(
            "QUALITY",
            revenue=5_000_000_000,
            operating_income=1_000_000_000,  # 20% op margin
        )
        qs = score_quality(
            f, cache=fisher_quality_cache, as_of=date(2024, 6, 30)
        )
        assert qs.points_passed == 5
        assert qs.point_1_market_potential
        assert qs.point_3_rd_effectiveness
        assert qs.point_5_profit_margins
        assert qs.point_6_margin_maintenance
        assert qs.point_13_equity_dilution

    def test_no_history_zero_passes(
        self, empty_cache: EdgarCache
    ) -> None:
        f = make_pit(
            "EMPTY",
            revenue=5_000_000_000,
            operating_income=1_000_000_000,
        )
        qs = score_quality(f, cache=empty_cache, as_of=date(2024, 6, 30))
        # Without history we still score Point 5 (snapshot operating
        # margin) — that one passes off the PIT data alone.
        assert qs.point_5_profit_margins
        # The other 4 require history, so they fail.
        assert not qs.point_1_market_potential
        assert not qs.point_3_rd_effectiveness
        assert not qs.point_6_margin_maintenance
        assert not qs.point_13_equity_dilution
        assert qs.points_passed == 1

    def test_low_op_margin_fails_point_5(
        self, fisher_quality_cache: EdgarCache
    ) -> None:
        f = make_pit(
            "LOWMARGIN",
            revenue=5_000_000_000,
            operating_income=300_000_000,  # 6% op margin < 12% floor
        )
        qs = score_quality(
            f, cache=fisher_quality_cache, as_of=date(2024, 6, 30)
        )
        assert not qs.point_5_profit_margins


class TestDefaults:
    def test_defaults(self) -> None:
        assert DEFAULT_MIN_REVENUE_CAGR_PCT == 8.0
        assert DEFAULT_MIN_RD_TO_REVENUE_PCT == 5.0
        assert DEFAULT_MIN_OPERATING_MARGIN_PCT == 12.0
        assert DEFAULT_MAX_SHARE_DILUTION_PCT_5YR == 5.0


class TestConceptChainResolvesToTheFreshestYear:
    """The chain used to return the first concept that produced anything.

    Filers that moved to the ASC 606 revenue concept in 2018 left the
    old ``Revenues`` fact on file, and it is first in the chain — so a
    first-match resolver froze them at their switching year. Measured on
    a 1,101-ticker sample of the live universe: 224 of 838 resolvable
    revenue reads (26.7%) were stale, median lag 7 fiscal years, with
    NextEra Energy reading FY2012 in 2026.
    """

    def test_the_stale_first_concept_does_not_win(
        self, asc606_switcher_cache: EdgarCache
    ) -> None:
        # SWITCH grows revenue 15%/yr and reports through FY2023. Read
        # from the frozen FY2018 ``Revenues`` tag the series is flat and
        # the CAGR is undefined or zero.
        cagr = revenue_cagr_5yr_pct(
            asc606_switcher_cache, "SWITCH", date(2024, 6, 30)
        )

        assert cagr is not None
        assert cagr == pytest.approx(15.0, abs=0.5)

    def test_the_ratio_uses_the_fresh_year_on_both_sides(
        self, asc606_switcher_cache: EdgarCache
    ) -> None:
        # R&D is 8% of revenue every year. Pairing fresh FY2023 R&D with
        # frozen FY2018 revenue would report roughly double that.
        ratio = rd_to_revenue_pct(
            asc606_switcher_cache, "SWITCH", date(2024, 6, 30)
        )

        assert ratio == pytest.approx(8.0, abs=0.1)

    def test_a_switcher_still_scores(
        self, asc606_switcher_cache: EdgarCache
    ) -> None:
        # The whole point: changing your XBRL tagging is not a business
        # event and must not cost points.
        score = score_quality(
            make_pit("SWITCH"),
            cache=asc606_switcher_cache,
            as_of=date(2024, 6, 30),
        )

        assert score.point_1_market_potential
        assert score.point_3_rd_effectiveness


class TestMismatchedYearsAreRefused:
    def test_a_ratio_across_different_years_is_none(
        self, tmp_path: Path
    ) -> None:
        # R&D reported through FY2023, revenue only through FY2016.
        # Their quotient is not an R&D intensity for either year.
        cache = EdgarCache(cache_dir=tmp_path)
        facts = [
            make_fact(
                concept="Revenues",
                value=1_000_000_000.0,
                period_end=date(2016, 12, 31),
                filed=date(2017, 2, 15),
                accession="GAP-2016",
            ),
            make_fact(
                concept="ResearchAndDevelopmentExpense",
                value=400_000_000.0,
                period_end=date(2023, 12, 31),
                filed=date(2024, 2, 15),
                accession="GAP-2023",
            ),
        ]
        cache.save_facts("GAP", facts)

        assert rd_to_revenue_pct(cache, "GAP", date(2024, 6, 30)) is None

    def test_a_frozen_share_count_is_not_a_clean_buyback_record(
        self, frozen_shares_cache: EdgarCache
    ) -> None:
        # Both endpoints resolve to FY2017, so the change computed as
        # exactly 0.0% — which PASSES the dilution point. Absence of
        # evidence was scoring as an unblemished record.
        change = share_count_change_5yr_pct(
            frozen_shares_cache, "FROZEN", date(2024, 6, 30)
        )

        assert change is None

    def test_that_missing_point_fails_rather_than_passes(
        self, frozen_shares_cache: EdgarCache
    ) -> None:
        score = score_quality(
            make_pit("FROZEN"),
            cache=frozen_shares_cache,
            as_of=date(2024, 6, 30),
        )

        assert score.share_count_change_5yr_pct is None
        assert not score.point_13_equity_dilution

    def test_a_real_buyback_still_passes(
        self, fisher_quality_cache: EdgarCache
    ) -> None:
        # Guard against over-correction: QUALITY files a flat share
        # count every year and must keep the point.
        change = share_count_change_5yr_pct(
            fisher_quality_cache, "QUALITY", date(2024, 6, 30)
        )

        assert change == pytest.approx(0.0)
