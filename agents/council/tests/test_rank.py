"""The rank has to mean the same thing in every quarter.

Two properties carry most of the weight here. Percentiles are computed
over the whole universe rather than the passers, so a thin quarter does
not rescale the composite; and a missing component drops out of the
weighted mean rather than scoring zero, so a company is not punished for
a metric nobody in its industry reports.
"""

from __future__ import annotations

import pytest

from agents.council.rank import (
    DEFAULT_SECTOR_CAP,
    F_SCORE_MAX,
    KNIFE_GUARD_PERCENTILE,
    WEIGHT_MOMENTUM,
    WEIGHT_QUALITY,
    WEIGHT_VALUE,
    RankInputs,
    percentiles,
    rank_universe,
    select_basket,
)


def row(ticker: str, **kw) -> RankInputs:
    base = dict(
        ebit_to_ev=0.10,
        fcf_to_ev=0.08,
        net_cash_to_market_cap=0.0,
        roic=0.12,
        f_score=6,
        momentum_12_1=0.10,
        sic2=35,
    )
    return RankInputs(ticker=ticker, **{**base, **kw})


# ----------------------------------------------------------- percentiles


class TestPercentiles:
    def test_higher_is_better(self) -> None:
        p = percentiles([1.0, 2.0, 3.0])
        assert p[2] > p[1] > p[0]

    def test_ties_share_a_mid_rank(self) -> None:
        """Otherwise list order decides who wins a tie."""
        p = percentiles([5.0, 5.0, 5.0, 5.0])
        assert p[0] == p[1] == p[2] == p[3] == 0.5

    def test_none_is_preserved_not_scored(self) -> None:
        p = percentiles([1.0, None, 3.0])
        assert p[1] is None

    def test_none_is_excluded_from_the_denominator(self) -> None:
        """A metric most companies do not report must not compress the scale."""
        with_gaps = percentiles([1.0, 2.0, None, None, None])
        without = percentiles([1.0, 2.0])
        assert with_gaps[0] == without[0]
        assert with_gaps[1] == without[1]

    def test_all_none_returns_all_none(self) -> None:
        assert percentiles([None, None]) == [None, None]

    def test_a_single_value_sits_in_the_middle(self) -> None:
        assert percentiles([7.0]) == [0.5]

    def test_empty_input(self) -> None:
        assert percentiles([]) == []

    def test_negative_values_rank_below_positive(self) -> None:
        p = percentiles([-5.0, 0.0, 5.0])
        assert p[0] < p[1] < p[2]


# ------------------------------------------------------------- composite


class TestComposite:
    def test_the_weights_are_forty_five_thirty_five_twenty(self) -> None:
        assert (WEIGHT_VALUE, WEIGHT_QUALITY, WEIGHT_MOMENTUM) == (0.45, 0.35, 0.20)

    def test_a_cheaper_name_outranks_an_expensive_one(self) -> None:
        ranked = rank_universe(
            [row("CHEAP", ebit_to_ev=0.30), row("RICH", ebit_to_ev=0.01)]
        )
        assert [r.ticker for r in ranked] == ["CHEAP", "RICH"]

    def test_a_missing_quality_renormalises_rather_than_scoring_zero(self) -> None:
        """A company with no ROIC and no F-score is not a zero-quality one."""
        ranked = rank_universe(
            [
                row("A", roic=None, f_score=None),
                row("B", roic=None, f_score=None),
            ]
        )
        # Both have identical V and M, so the composite is the
        # renormalised mean of those two and not dragged toward zero.
        assert ranked[0].quality is None
        assert ranked[0].composite > 0.4

    def test_f_score_is_divided_not_ranked(self) -> None:
        ranked = rank_universe(
            [row("FULL", roic=None, f_score=F_SCORE_MAX), row("ZERO", roic=None, f_score=0)]
        )
        by_ticker = {r.ticker: r for r in ranked}
        assert by_ticker["FULL"].quality == 1.0
        assert by_ticker["ZERO"].quality == 0.0

    def test_a_cash_box_with_no_ebit_still_ranks(self) -> None:
        """Mean of available: net cash and FCF carry it."""
        ranked = rank_universe(
            [
                row("BOX", ebit_to_ev=None, net_cash_to_market_cap=0.6),
                row("OTHER", net_cash_to_market_cap=0.0),
            ]
        )
        assert "BOX" in {r.ticker for r in ranked}

    def test_no_value_component_at_all_is_dropped(self) -> None:
        """Not mechanically investable, and that is deliberate."""
        ranked = rank_universe(
            [
                row("NOVALUE", ebit_to_ev=None, fcf_to_ev=None,
                    net_cash_to_market_cap=None),
                row("REAL"),
            ]
        )
        assert [r.ticker for r in ranked] == ["REAL"]

    def test_the_order_is_deterministic_on_a_tie(self) -> None:
        ranked = rank_universe([row("ZZZ"), row("AAA")])
        assert [r.ticker for r in ranked] == ["AAA", "ZZZ"]

    def test_an_insider_cluster_breaks_an_equal_composite(self) -> None:
        ranked = rank_universe(
            [row("AAA"), row("ZZZ", insider_cluster=True)]
        )
        assert [r.ticker for r in ranked] == ["ZZZ", "AAA"]

    def test_an_empty_universe_is_not_an_error(self) -> None:
        assert rank_universe([]) == []


# ------------------------------------------------------------ knife guard


class TestKnifeGuard:
    def test_the_bottom_decile_is_guarded(self) -> None:
        rows = [row(f"T{i}", momentum_12_1=float(i)) for i in range(20)]
        ranked = {r.ticker: r for r in rank_universe(rows)}
        assert ranked["T0"].knife_guarded is True
        assert ranked["T19"].knife_guarded is False

    def test_the_threshold_is_a_decile(self) -> None:
        assert KNIFE_GUARD_PERCENTILE == 0.10

    def test_an_unreadable_momentum_is_guarded_not_waved_through(self) -> None:
        """A missing year of prices is not a reason to buy blind."""
        ranked = rank_universe([row("NOMOM", momentum_12_1=None), row("OK")])
        by_ticker = {r.ticker: r for r in ranked}
        assert by_ticker["NOMOM"].knife_guarded is True

    def test_a_guarded_name_keeps_its_rank(self) -> None:
        """E8's exit buffer still has to be able to read it."""
        rows = [row(f"T{i}", momentum_12_1=float(i), ebit_to_ev=1.0 - i / 100)
                for i in range(20)]
        ranked = rank_universe(rows)
        assert any(r.knife_guarded for r in ranked)
        assert len(ranked) == 20


# ---------------------------------------------------------------- basket


class TestSelectBasket:
    def test_it_takes_the_top_names(self) -> None:
        rows = [row(f"T{i:02d}", ebit_to_ev=1.0 - i / 100, sic2=10 + i)
                for i in range(30)]
        basket = select_basket(rank_universe(rows), size=20)
        assert len(basket) == 20
        assert basket[0].ticker == "T00"

    def test_the_sector_cap_binds(self) -> None:
        # Thirty names, all in one SIC division.
        rows = [row(f"T{i:02d}", ebit_to_ev=1.0 - i / 100, sic2=35)
                for i in range(30)]
        basket = select_basket(rank_universe(rows), size=20)
        assert len(basket) == DEFAULT_SECTOR_CAP

    def test_the_cap_is_per_division_not_overall(self) -> None:
        rows = [
            row(f"A{i}", ebit_to_ev=1.0 - i / 100, sic2=35) for i in range(10)
        ] + [row(f"B{i}", ebit_to_ev=0.5 - i / 100, sic2=20) for i in range(10)]
        basket = select_basket(rank_universe(rows), size=20)
        assert len(basket) == DEFAULT_SECTOR_CAP * 2

    def test_an_unclassified_name_is_not_grouped_with_other_unclassified(
        self,
    ) -> None:
        """Capping them together would invent a sector that does not exist."""
        rows = [row(f"T{i:02d}", ebit_to_ev=1.0 - i / 100, sic2=None)
                for i in range(10)]
        basket = select_basket(rank_universe(rows), size=20)
        assert len(basket) == 10

    def test_guarded_names_are_skipped(self) -> None:
        rows = [row(f"T{i:02d}", momentum_12_1=float(i), sic2=10 + i)
                for i in range(20)]
        basket = select_basket(rank_universe(rows), size=20)
        assert all(not b.knife_guarded for b in basket)

    def test_only_screened_names_are_eligible(self) -> None:
        rows = [row(f"T{i:02d}", ebit_to_ev=1.0 - i / 100, sic2=10 + i)
                for i in range(10)]
        basket = select_basket(
            rank_universe(rows), eligible=["T03", "T07"], size=20
        )
        assert [b.ticker for b in basket] == ["T03", "T07"]

    def test_eligibility_is_case_insensitive(self) -> None:
        rows = [row("AAA", sic2=35)]
        basket = select_basket(rank_universe(rows), eligible=["aaa"])
        assert len(basket) == 1

    def test_a_short_basket_is_a_legitimate_answer(self) -> None:
        """The system is allowed to say nothing is cheap enough."""
        basket = select_basket(rank_universe([row("ONE", sic2=35)]), size=20)
        assert len(basket) == 1

    def test_nothing_eligible_holds_cash(self) -> None:
        rows = [row("AAA", sic2=35)]
        assert select_basket(rank_universe(rows), eligible=[]) == []

    def test_it_never_exceeds_the_requested_size(self) -> None:
        rows = [row(f"T{i:02d}", ebit_to_ev=1.0 - i / 100, sic2=10 + i)
                for i in range(50)]
        assert len(select_basket(rank_universe(rows), size=7)) == 7


class TestRankIsComputedOverTheWholeUniverse:
    """The property that keeps a composite comparable across quarters."""

    def test_a_thin_quarter_does_not_rescale_the_score(self) -> None:
        universe = [row(f"T{i:02d}", ebit_to_ev=i / 100) for i in range(100)]
        ranked = {r.ticker: r for r in rank_universe(universe)}
        # T50 sits mid-universe and must score mid-scale whether or not
        # the other ninety-nine cleared the screen.
        assert 0.4 < ranked["T50"].value <= 0.6

        # Ranking only the survivors would put the same company at the
        # top of the scale, which is the mistake this guards.
        survivors_only = {
            r.ticker: r
            for r in rank_universe([u for u in universe if u.ticker in
                                    {"T48", "T49", "T50"}])
        }
        assert survivors_only["T50"].value > ranked["T50"].value


@pytest.mark.parametrize("size", [0, 1, 20, 33])
def test_basket_size_is_respected(size: int) -> None:
    rows = [row(f"T{i:02d}", ebit_to_ev=1.0 - i / 100, sic2=10 + i)
            for i in range(40)]
    assert len(select_basket(rank_universe(rows), size=size)) == min(size, 40)
