"""Sizes must be attributable, and the dial must never force a sale.

Two doctrine properties are pinned here. Section 9.1's ceilings gate new
capital only — a sleeve over its ceiling may not add, and is never told
to sell — and every core size records which constraint produced it,
because a size nobody can attribute is a size nobody can audit two years
later.
"""

from __future__ import annotations

import pytest

from agents.council.exits import Sleeve
from agents.council.sizing import (
    CORE_CEILING_WEIGHT,
    EVENT_ENTRY_SIZE,
    MAX_POSITION_CORE,
    MAX_POSITION_EVENT,
    MAX_POSITION_STATISTICAL,
    STATISTICAL_FLOOR_WEIGHT,
    STATISTICAL_NAMES,
    STATISTICAL_START_WEIGHT,
    ceilings_for,
    event_entry_weight,
    kelly_fraction,
    position_cap,
    shrink_probability,
    size_core_entry,
    sleeve_headroom,
    statistical_entry_weight,
)


class TestRegimeCeilings:
    @pytest.mark.parametrize(
        ("dials", "statistical", "event", "cash"),
        [
            (4, 0.45, 0.15, 0.05),
            (3, 0.45, 0.15, 0.05),
            (2, 0.35, 0.10, 0.10),
            (1, 0.25, 0.05, 0.15),
            (0, 0.20, 0.05, 0.20),
        ],
    )
    def test_the_table_is_section_nine_one(
        self, dials: int, statistical: float, event: float, cash: float
    ) -> None:
        c = ceilings_for(dials)
        assert (c.statistical_ceiling, c.event_ceiling, c.cash_floor) == (
            statistical,
            event,
            cash,
        )

    def test_full_size_at_three_and_four_dials(self) -> None:
        assert ceilings_for(4).entry_scale == 1.0
        assert ceilings_for(3).entry_scale == 1.0

    def test_half_size_at_two(self) -> None:
        assert ceilings_for(2).entry_scale == 0.5

    def test_no_mechanical_entry_below_two(self) -> None:
        assert not ceilings_for(1).mechanical_entries_allowed
        assert not ceilings_for(0).mechanical_entries_allowed

    def test_an_unreadable_dial_takes_the_tightest_row(self) -> None:
        """A FRED outage tightens the book; it does not loosen it."""
        assert ceilings_for(None).statistical_ceiling == ceilings_for(0).statistical_ceiling
        assert not ceilings_for(None).mechanical_entries_allowed

    @pytest.mark.parametrize("dials", [-3, 9])
    def test_a_nonsense_count_is_clamped_not_crashed(self, dials: int) -> None:
        assert ceilings_for(dials) in (ceilings_for(0), ceilings_for(4))


class TestPositionCaps:
    def test_each_sleeve_has_its_own(self) -> None:
        assert position_cap(Sleeve.STATISTICAL) == MAX_POSITION_STATISTICAL
        assert position_cap(Sleeve.EVENT) == MAX_POSITION_EVENT
        assert position_cap(Sleeve.CORE) == MAX_POSITION_CORE

    def test_core_is_the_only_one_that_may_be_large(self) -> None:
        assert position_cap(Sleeve.CORE) == 0.25
        assert position_cap(Sleeve.STATISTICAL) < 0.10


class TestStatisticalSizing:
    def test_equal_weight_at_the_bootstrap(self) -> None:
        w = statistical_entry_weight(sleeve_weight=STATISTICAL_START_WEIGHT)
        assert w == pytest.approx(0.0225)

    def test_the_per_name_cap_never_binds_on_the_mechanical_path(self) -> None:
        """45% over twenty names is 2.25%, well under the 5% cap."""
        w = statistical_entry_weight(sleeve_weight=STATISTICAL_START_WEIGHT)
        assert w < MAX_POSITION_STATISTICAL

    def test_the_cap_still_holds_for_a_smaller_sleeve_count(self) -> None:
        w = statistical_entry_weight(sleeve_weight=0.45, names=2)
        assert w == MAX_POSITION_STATISTICAL

    def test_half_size_halves_it(self) -> None:
        full = statistical_entry_weight(sleeve_weight=0.35)
        half = statistical_entry_weight(sleeve_weight=0.35, entry_scale=0.5)
        assert half == pytest.approx(full / 2)

    def test_zero_names_does_not_divide_by_zero(self) -> None:
        w = statistical_entry_weight(sleeve_weight=0.45, names=0)
        assert w == MAX_POSITION_STATISTICAL

    def test_twenty_names_is_the_setting(self) -> None:
        assert STATISTICAL_NAMES == 20


class TestEventSizing:
    def test_three_percent_fixed(self) -> None:
        assert event_entry_weight() == EVENT_ENTRY_SIZE

    def test_capped_at_four(self) -> None:
        assert event_entry_weight(entry_scale=10.0) == MAX_POSITION_EVENT


class TestShrinkage:
    def test_a_coin_flip_is_unchanged(self) -> None:
        assert shrink_probability(0.5) == 0.5

    def test_confidence_is_pulled_toward_the_middle(self) -> None:
        assert 0.5 < shrink_probability(0.65) < 0.65

    def test_the_runbook_formula(self) -> None:
        assert shrink_probability(0.65) == pytest.approx(0.59)

    def test_it_is_symmetric(self) -> None:
        assert shrink_probability(0.3) == pytest.approx(1 - shrink_probability(0.7))


class TestKelly:
    def test_a_favourable_bet_stakes_something(self) -> None:
        assert kelly_fraction(0.6, win=2.0, loss=1.0) > 0

    def test_an_unfavourable_bet_stakes_nothing(self) -> None:
        assert kelly_fraction(0.2, win=1.0, loss=1.0) == 0.0

    def test_it_never_goes_negative(self) -> None:
        """This book does not short."""
        assert kelly_fraction(0.01, win=1.0, loss=1.0) == 0.0

    def test_a_nonsense_payoff_is_zero_not_an_exception(self) -> None:
        assert kelly_fraction(0.9, win=0.0, loss=1.0) == 0.0
        assert kelly_fraction(0.9, win=1.0, loss=0.0) == 0.0

    def test_more_upside_stakes_more(self) -> None:
        small = kelly_fraction(0.6, win=1.0, loss=0.5)
        large = kelly_fraction(0.6, win=4.0, loss=0.5)
        assert large > small

    def test_a_smaller_downside_stakes_more(self) -> None:
        """The asymmetry the doctrine hunts: a floor lets you size up."""
        shallow = kelly_fraction(0.6, win=2.0, loss=0.2)
        deep = kelly_fraction(0.6, win=2.0, loss=0.8)
        assert shallow > deep

    def test_the_weight_divides_by_the_loss_rather_than_multiplying(self) -> None:
        """The bug this pins understated every core position fourfold.

        A position of weight w risks w x loss, not w, so solving for w
        divides. Multiplying instead turns an 80% Kelly weight into 7%
        on a bet that falls 30% when wrong.
        """
        assert kelly_fraction(0.6, win=1.0, loss=0.5) == pytest.approx(0.8)


class TestCoreSizing:
    def test_it_records_which_limit_bound(self) -> None:
        sized = size_core_entry(
            "X", probability=0.9, win=5.0, loss=0.3,
            sleeve_headroom=0.60, cash_headroom=0.60,
        )
        assert sized.binding == "position_cap"
        assert sized.weight == MAX_POSITION_CORE

    def test_kelly_binds_on_a_modest_edge(self) -> None:
        sized = size_core_entry(
            "X", probability=0.52, win=0.5, loss=0.5,
            sleeve_headroom=0.60, cash_headroom=0.60,
        )
        assert sized.binding == "kelly"
        assert 0.0 < sized.weight < MAX_POSITION_CORE

    def test_a_real_edge_is_capped_rather_than_sized_by_kelly(self) -> None:
        """Full Kelly on a genuine edge is enormous; the cap is the answer.

        Doctrine Part 4 says an Understanding position's edge is a fact
        about a company rather than a draw from a distribution, so the
        fractional-Kelly argument does not apply and the position is
        sized like it is meant. The 25% ceiling is what stands between
        that and the whole book.
        """
        sized = size_core_entry(
            "X", probability=0.65, win=2.0, loss=0.4,
            sleeve_headroom=0.60, cash_headroom=0.60,
        )
        assert sized.binding == "position_cap"
        assert sized.weight == MAX_POSITION_CORE

    def test_the_cash_floor_can_be_the_binding_one(self) -> None:
        sized = size_core_entry(
            "X", probability=0.9, win=5.0, loss=0.3,
            sleeve_headroom=0.60, cash_headroom=0.02,
        )
        assert sized.binding == "cash_floor"
        assert sized.weight == 0.02

    def test_a_full_sleeve_sizes_to_nothing(self) -> None:
        sized = size_core_entry(
            "X", probability=0.9, win=5.0, loss=0.3,
            sleeve_headroom=0.0, cash_headroom=0.60,
        )
        assert sized.weight == 0.0

    def test_shrinkage_is_applied_before_kelly(self) -> None:
        """Kelly on an unshrunk probability oversizes every position."""
        shrunk = kelly_fraction(shrink_probability(0.55), win=0.5, loss=0.5)
        unshrunk = kelly_fraction(0.55, win=0.5, loss=0.5)
        assert shrunk < unshrunk
        sized = size_core_entry(
            "X", probability=0.55, win=0.5, loss=0.5,
            sleeve_headroom=0.60, cash_headroom=0.60,
        )
        assert sized.binding == "kelly"
        assert sized.weight == pytest.approx(shrunk)

    def test_a_negative_headroom_is_treated_as_none(self) -> None:
        sized = size_core_entry(
            "X", probability=0.9, win=5.0, loss=0.3,
            sleeve_headroom=-0.10, cash_headroom=0.60,
        )
        assert sized.weight == 0.0


class TestSleeveHeadroom:
    def test_an_empty_sleeve_has_its_whole_ceiling(self) -> None:
        room = sleeve_headroom(
            sleeve=Sleeve.EVENT, held_weight=0.0, ceilings=ceilings_for(4)
        )
        assert room == 0.15

    def test_a_sleeve_over_its_ceiling_may_not_add(self) -> None:
        """Section 9.1: the dial gates new capital, it never forces a sale."""
        room = sleeve_headroom(
            sleeve=Sleeve.EVENT, held_weight=0.30, ceilings=ceilings_for(4)
        )
        assert room == 0.0

    def test_core_fills_at_the_expense_of_statistical(self) -> None:
        empty = sleeve_headroom(
            sleeve=Sleeve.STATISTICAL, held_weight=0.0,
            ceilings=ceilings_for(4), core_weight=0.0,
        )
        crowded = sleeve_headroom(
            sleeve=Sleeve.STATISTICAL, held_weight=0.0,
            ceilings=ceilings_for(4), core_weight=0.20,
        )
        assert crowded < empty

    def test_statistical_never_shrinks_below_its_floor(self) -> None:
        room = sleeve_headroom(
            sleeve=Sleeve.STATISTICAL, held_weight=0.0,
            ceilings=ceilings_for(4), core_weight=CORE_CEILING_WEIGHT,
        )
        assert room == STATISTICAL_FLOOR_WEIGHT

    def test_the_regime_can_bind_before_the_crowding_does(self) -> None:
        room = sleeve_headroom(
            sleeve=Sleeve.STATISTICAL, held_weight=0.0,
            ceilings=ceilings_for(0), core_weight=0.0,
        )
        assert room == 0.20

    def test_core_answers_to_its_own_ceiling_not_the_dial(self) -> None:
        """The moment everyone is forced out is when the Council must act."""
        calm = sleeve_headroom(
            sleeve=Sleeve.CORE, held_weight=0.0, ceilings=ceilings_for(4)
        )
        panic = sleeve_headroom(
            sleeve=Sleeve.CORE, held_weight=0.0, ceilings=ceilings_for(0)
        )
        assert calm == panic == CORE_CEILING_WEIGHT
