"""Tests for the three measurements and the rank that combines them.

The arithmetic has to be right before any backtest number means
anything: a ranking that mishandles ties or outliers produces a
plausible-looking result from nothing.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents.composite.factor_scores import (
    FactorScores,
    composite_ranks,
    earnings_yield,
    enterprise_value,
    operating_profitability,
    percentile_ranks,
)
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


def _fin(**kw: float | None) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker="X",
        as_of=date(2024, 12, 31),
        source_filing=FilingMetadata(
            ticker="X",
            cik=None,
            form_type="10-K",
            filing_date=date(2024, 3, 1),
            period_of_report=date(2023, 12, 31),
            accession_number="0000000000-00-000000",
        ),
        **kw,  # type: ignore[arg-type]
    )


class TestEnterpriseValue:
    def test_market_cap_plus_debt_less_cash(self) -> None:
        fin = _fin(total_debt=200.0, cash_and_equivalents=50.0)
        assert enterprise_value(fin, 1_000.0) == pytest.approx(1_150.0)

    def test_it_falls_back_to_long_term_debt(self) -> None:
        fin = _fin(long_term_debt=300.0, cash_and_equivalents=100.0)
        assert enterprise_value(fin, 1_000.0) == pytest.approx(1_200.0)

    def test_missing_debt_is_unknowable_not_zero(self) -> None:
        # Treating an unreported figure as zero flatters the cheapness
        # of exactly the companies that report least.
        fin = _fin(cash_and_equivalents=50.0)
        assert enterprise_value(fin, 1_000.0) is None

    def test_missing_cash_is_unknowable(self) -> None:
        assert enterprise_value(_fin(total_debt=200.0), 1_000.0) is None

    def test_net_cash_exceeding_equity_is_rejected(self) -> None:
        # A negative EV flips the sign of the yield, which would sort the
        # most distressed names to the top of a cheapness ranking.
        fin = _fin(total_debt=0.0, cash_and_equivalents=2_000.0)
        assert enterprise_value(fin, 1_000.0) is None

    def test_a_worthless_market_cap_is_rejected(self) -> None:
        fin = _fin(total_debt=0.0, cash_and_equivalents=0.0)
        assert enterprise_value(fin, 0.0) is None


class TestEarningsYield:
    def test_ebit_over_ev(self) -> None:
        fin = _fin(
            operating_income=115.0, total_debt=200.0, cash_and_equivalents=50.0
        )
        # 115 / 1150 = 10%
        assert earnings_yield(fin, 1_000.0) == pytest.approx(10.0)

    def test_a_loss_ranks_rather_than_disqualifies(self) -> None:
        # A negative yield is a real measurement and belongs at the
        # bottom of the ranking, not outside it.
        fin = _fin(
            operating_income=-115.0, total_debt=200.0, cash_and_equivalents=50.0
        )
        assert earnings_yield(fin, 1_000.0) == pytest.approx(-10.0)

    def test_no_operating_income_is_unknowable(self) -> None:
        fin = _fin(total_debt=200.0, cash_and_equivalents=50.0)
        assert earnings_yield(fin, 1_000.0) is None


class TestOperatingProfitability:
    def test_operating_income_over_assets(self) -> None:
        fin = _fin(operating_income=150.0, total_assets=1_000.0)
        assert operating_profitability(fin) == pytest.approx(15.0)

    def test_it_is_scaled_by_assets_not_equity(self) -> None:
        # Two identical businesses, one levered. Scaling by assets must
        # score them the same; scaling by equity would not.
        lean = _fin(operating_income=150.0, total_assets=1_000.0, total_equity=1_000.0)
        levered = _fin(operating_income=150.0, total_assets=1_000.0, total_equity=200.0)
        assert operating_profitability(lean) == operating_profitability(levered)

    def test_missing_assets_is_unknowable(self) -> None:
        assert operating_profitability(_fin(operating_income=150.0)) is None

    def test_zero_assets_is_unknowable(self) -> None:
        fin = _fin(operating_income=150.0, total_assets=0.0)
        assert operating_profitability(fin) is None


class TestPercentileRanks:
    def test_worst_is_zero_and_best_is_one(self) -> None:
        r = percentile_ranks({"a": 1.0, "b": 2.0, "c": 3.0})
        assert r["a"] == pytest.approx(0.0)
        assert r["c"] == pytest.approx(1.0)
        assert r["b"] == pytest.approx(0.5)

    def test_an_outlier_cannot_flatten_the_field(self) -> None:
        # The reason for ranks over z-scores. One company with an
        # enterprise value near zero produces a 4,000% yield; under a
        # z-score everyone else collapses into indistinguishable noise.
        r = percentile_ranks({"a": 1.0, "b": 2.0, "c": 3.0, "outlier": 4_000.0})
        assert r["a"] < r["b"] < r["c"] < r["outlier"]
        assert r["c"] - r["b"] == pytest.approx(1 / 3)

    def test_ties_share_their_average_position(self) -> None:
        r = percentile_ranks({"a": 5.0, "b": 5.0, "c": 5.0})
        assert r == {"a": 0.5, "b": 0.5, "c": 0.5}

    def test_a_tied_block_does_not_win_on_sort_order(self) -> None:
        r = percentile_ranks({"a": 1.0, "b": 2.0, "c": 2.0, "d": 3.0})
        assert r["b"] == pytest.approx(r["c"])
        assert r["a"] < r["b"] < r["d"]

    def test_a_single_candidate_is_neutral(self) -> None:
        # Not 1.0: being alone is not evidence of being good.
        assert percentile_ranks({"a": 7.0}) == {"a": 0.5}

    def test_an_empty_field_is_empty(self) -> None:
        assert percentile_ranks({}) == {}

    def test_negative_values_rank_normally(self) -> None:
        r = percentile_ranks({"a": -50.0, "b": 0.0, "c": 50.0})
        assert r["a"] == pytest.approx(0.0)
        assert r["c"] == pytest.approx(1.0)


class TestCompositeRanks:
    @staticmethod
    def _s(t: str, v: float, q: float, m: float) -> FactorScores:
        return FactorScores(ticker=t, value=v, quality=q, momentum=m)

    def test_best_on_all_three_wins(self) -> None:
        c = composite_ranks(
            [
                self._s("best", 10.0, 30.0, 40.0),
                self._s("mid", 5.0, 20.0, 20.0),
                self._s("worst", 1.0, 10.0, -5.0),
            ]
        )
        assert c["best"] == pytest.approx(1.0)
        assert c["worst"] == pytest.approx(0.0)

    def test_the_three_legs_weigh_the_same(self) -> None:
        # Cheap-but-unprofitable-and-falling must tie
        # expensive-but-profitable-and-rising on a two-of-three split.
        c = composite_ranks(
            [
                self._s("cheap_only", 10.0, 10.0, -5.0),
                self._s("good_only", 1.0, 30.0, 40.0),
                self._s("middle", 5.0, 20.0, 20.0),
            ]
        )
        # cheap_only: value 1.0, quality 0.0, momentum 0.0 -> 1/3
        # good_only:  value 0.0, quality 1.0, momentum 1.0 -> 2/3
        assert c["cheap_only"] == pytest.approx(1 / 3)
        assert c["good_only"] == pytest.approx(2 / 3)

    def test_one_leg_cannot_carry_a_name_alone(self) -> None:
        # The point of the composite. one_trick is top of the field on
        # value and bottom on the other two, so it scores exactly 1/3 —
        # the same as a name that is bottom on value and middling on
        # both others, and comfortably behind one that is decent
        # everywhere. A spectacular single number buys a third and no
        # more, which is the whole reason to average three ranks rather
        # than sort on one.
        c = composite_ranks(
            [
                self._s("one_trick", 9_999.0, 1.0, -50.0),
                self._s("balanced_a", 5.0, 20.0, 20.0),
                self._s("balanced_b", 6.0, 25.0, 25.0),
            ]
        )
        assert c["one_trick"] == pytest.approx(1 / 3)
        assert c["one_trick"] == pytest.approx(c["balanced_a"])
        assert c["balanced_b"] > c["one_trick"]

    def test_an_empty_field_is_empty(self) -> None:
        assert composite_ranks([]) == {}

    def test_every_candidate_is_scored(self) -> None:
        scores = [self._s(f"t{i}", i, i * 2, i * 3) for i in range(20)]
        c = composite_ranks(scores)
        assert len(c) == 20
        assert all(0.0 <= v <= 1.0 for v in c.values())
