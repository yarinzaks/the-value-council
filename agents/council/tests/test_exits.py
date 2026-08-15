"""A position must be able to end, and only for the reasons on the list.

Two properties carry this module. The exit block has to be refused when
it is not a live rule for its sleeve — that is what makes an eternal
position impossible — and the evaluation order has to hold, because a
company that is both delisting and merely expensive must exit for the
delisting.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents.council.exits import (
    FAIR_VALUE_EXIT_MULTIPLE,
    REQUIRED_KILL_CRITERIA,
    REVIEW_DEADLINE_DAYS,
    REVIEW_LIQUIDATION_DAYS,
    TIME_STOP_QUARTERS,
    TRIM_TARGET_WEIGHT,
    TRIM_TRIGGER_WEIGHT,
    Action,
    CoreExit,
    EventExit,
    InvalidExitBlockError,
    PositionState,
    Sleeve,
    StatisticalExit,
    entries_blocked,
    evaluate_book,
    evaluate_position,
    validate_exit_block,
)

AS_OF = date(2026, 8, 14)

KILLS = ("net cash below 15% of market cap", "margin below 20%", "CEO departs")
CORE_BLOCK = CoreExit(kill_criteria=KILLS, fair_value_low=80.0, fair_value_high=120.0)


def core(**kw) -> PositionState:
    base = dict(
        ticker="C",
        sleeve=Sleeve.CORE,
        opened=date(2026, 1, 5),
        weight=0.20,
        exit_block=CORE_BLOCK,
    )
    return PositionState(**{**base, **kw})


def statistical(**kw) -> PositionState:
    base = dict(
        ticker="S",
        sleeve=Sleeve.STATISTICAL,
        opened=date(2026, 1, 5),
        weight=0.0225,
        exit_block=StatisticalExit(),
    )
    return PositionState(**{**base, **kw})


def event(**kw) -> PositionState:
    base = dict(
        ticker="E",
        sleeve=Sleeve.EVENT,
        opened=date(2026, 1, 5),
        weight=0.03,
        exit_block=EventExit(expiry=date(2027, 1, 5)),
    )
    return PositionState(**{**base, **kw})


# ------------------------------------------------------------ the invariant


class TestValidateExitBlock:
    """Execution refuses a BUY whose exit block is not live for its sleeve."""

    def test_a_missing_block_is_refused(self) -> None:
        with pytest.raises(InvalidExitBlockError, match="no exit block"):
            validate_exit_block(Sleeve.CORE, None)

    def test_a_block_from_another_sleeve_is_refused(self) -> None:
        with pytest.raises(InvalidExitBlockError):
            validate_exit_block(Sleeve.CORE, StatisticalExit())
        with pytest.raises(InvalidExitBlockError):
            validate_exit_block(Sleeve.STATISTICAL, CORE_BLOCK)

    def test_a_valid_block_passes_quietly(self) -> None:
        validate_exit_block(Sleeve.STATISTICAL, StatisticalExit())
        validate_exit_block(Sleeve.EVENT, EventExit(expiry=date(2027, 1, 1)))
        validate_exit_block(Sleeve.CORE, CORE_BLOCK)

    def test_a_zero_rank_buffer_would_sell_on_the_day_it_bought(self) -> None:
        with pytest.raises(InvalidExitBlockError, match="day it bought"):
            validate_exit_block(Sleeve.STATISTICAL, StatisticalExit(rank_buffer=0))

    def test_core_needs_three_kill_criteria(self) -> None:
        with pytest.raises(InvalidExitBlockError, match="kill criteria"):
            validate_exit_block(
                Sleeve.CORE,
                CoreExit(
                    kill_criteria=KILLS[: REQUIRED_KILL_CRITERIA - 1],
                    fair_value_low=80.0,
                    fair_value_high=120.0,
                ),
            )

    def test_a_blank_kill_criterion_does_not_count(self) -> None:
        with pytest.raises(InvalidExitBlockError, match="blank"):
            validate_exit_block(
                Sleeve.CORE,
                CoreExit(
                    kill_criteria=("real", "  ", "also real"),
                    fair_value_low=80.0,
                    fair_value_high=120.0,
                ),
            )

    def test_an_inverted_fair_value_band_is_refused(self) -> None:
        with pytest.raises(InvalidExitBlockError, match="ordered positive range"):
            validate_exit_block(
                Sleeve.CORE,
                CoreExit(kill_criteria=KILLS, fair_value_low=120.0, fair_value_high=80.0),
            )

    def test_a_stopless_event_block_is_refused(self) -> None:
        with pytest.raises(InvalidExitBlockError, match="not a stop"):
            validate_exit_block(
                Sleeve.EVENT,
                EventExit(expiry=date(2027, 1, 1), trail_atr_multiple=0.0),
            )


# ------------------------------------------------------------------ E1


class TestCircuitBreaker:
    def test_a_quiet_book_may_buy(self) -> None:
        blocked, _ = entries_blocked(-0.04)
        assert blocked is False

    def test_past_the_breaker_nothing_may_be_opened(self) -> None:
        blocked, reason = entries_blocked(-0.30)
        assert blocked
        assert "exits still run" in reason

    def test_at_exactly_minus_twenty_five_it_trips(self) -> None:
        assert entries_blocked(-0.25)[0] is True

    def test_an_unreadable_nav_blocks(self) -> None:
        """An unreadable drawdown is not a safe one."""
        blocked, reason = entries_blocked(None)
        assert blocked
        assert "unreadable" in reason

    def test_the_breaker_does_not_stop_exits(self) -> None:
        """E1's own row: exits below it still run."""
        v = evaluate_position(core(terminal_filing="8-K 1.03 bankruptcy"), AS_OF)
        assert v.action is Action.SELL


# ------------------------------------------------------------------ E2


class TestTerminalFilings:
    def test_a_terminal_filing_sells_immediately(self) -> None:
        v = evaluate_position(core(terminal_filing="8-K item 4.02"), AS_OF)
        assert (v.action, v.rule) == (Action.SELL, "E2")
        assert "no discussion" in v.reason

    def test_it_applies_to_every_sleeve(self) -> None:
        for position in (core(), statistical(), event()):
            state = PositionState(
                **{**position.__dict__, "terminal_filing": "delisting notice"}
            )
            assert evaluate_position(state, AS_OF).rule == "E2"

    def test_stale_filings_sell(self) -> None:
        v = evaluate_position(statistical(filing_age_days=401), AS_OF)
        assert (v.action, v.rule) == (Action.SELL, "E2")

    def test_at_four_hundred_days_it_is_still_held(self) -> None:
        v = evaluate_position(statistical(filing_age_days=400), AS_OF)
        assert v.action is Action.NONE

    def test_it_outranks_everything_below(self) -> None:
        """Delisting and expensive at once exits for the delisting."""
        v = evaluate_position(
            core(
                terminal_filing="Form 25",
                price=200.0,
                weight=0.50,
                quarterly_reviews_without_progress=12,
            ),
            AS_OF,
        )
        assert v.rule == "E2"


# ------------------------------------------------------------------ E3


class TestEventExits:
    def test_expiry_sells(self) -> None:
        v = evaluate_position(
            event(exit_block=EventExit(expiry=AS_OF)), AS_OF
        )
        assert (v.action, v.rule) == (Action.SELL, "E3")

    def test_before_expiry_it_holds(self) -> None:
        v = evaluate_position(event(), AS_OF)
        assert v.action is Action.NONE

    def test_the_trailing_stop_reads_a_close(self) -> None:
        v = evaluate_position(event(close=18.0, trail_stop_level=20.0), AS_OF)
        assert (v.action, v.rule) == (Action.SELL, "E3")
        assert "ATR" in v.reason

    def test_above_the_stop_it_holds(self) -> None:
        v = evaluate_position(event(close=22.0, trail_stop_level=20.0), AS_OF)
        assert v.action is Action.NONE

    def test_core_and_statistical_have_no_price_stop(self) -> None:
        """Kaminski-Lo: a stop on a mean-reversion bet pays to be removed."""
        for position in (core(close=1.0, trail_stop_level=999.0),
                         statistical(close=1.0, trail_stop_level=999.0)):
            assert evaluate_position(position, AS_OF).action is Action.NONE


# ------------------------------------------------------------------ E4


class TestKillCriterionDeadman:
    def test_a_fresh_trigger_waits_for_the_review(self) -> None:
        v = evaluate_position(
            core(kill_triggered=KILLS[0], trading_days_since_kill=2), AS_OF
        )
        assert v.action is Action.NONE

    def test_no_review_by_day_five_sells_half(self) -> None:
        v = evaluate_position(
            core(
                kill_triggered=KILLS[0],
                trading_days_since_kill=REVIEW_DEADLINE_DAYS,
            ),
            AS_OF,
        )
        assert (v.action, v.rule) == (Action.SELL_HALF, "E4")

    def test_no_review_by_day_ten_sells_all(self) -> None:
        v = evaluate_position(
            core(
                kill_triggered=KILLS[0],
                trading_days_since_kill=REVIEW_LIQUIDATION_DAYS,
            ),
            AS_OF,
        )
        assert (v.action, v.rule) == (Action.SELL, "E4")

    def test_a_review_clears_the_deadman(self) -> None:
        v = evaluate_position(
            core(
                kill_triggered=KILLS[0],
                trading_days_since_kill=20,
                reviewed_since_kill=True,
            ),
            AS_OF,
        )
        assert v.action is Action.NONE

    def test_it_is_core_only(self) -> None:
        v = evaluate_position(
            statistical(kill_triggered="x", trading_days_since_kill=99), AS_OF
        )
        assert v.rule != "E4"


# ------------------------------------------------------------------ E5


class TestFairValue:
    def test_reaching_fair_value_trims_half(self) -> None:
        v = evaluate_position(core(price=120.0), AS_OF)
        assert (v.action, v.rule) == (Action.SELL_HALF, "E5")

    def test_below_fair_value_it_holds(self) -> None:
        assert evaluate_position(core(price=119.0), AS_OF).action is Action.NONE

    def test_well_past_fair_value_exits_fully(self) -> None:
        v = evaluate_position(
            core(price=120.0 * FAIR_VALUE_EXIT_MULTIPLE), AS_OF
        )
        assert (v.action, v.rule) == (Action.SELL, "E5")

    def test_a_fresh_underwriting_keeps_it(self) -> None:
        """Allowed, but it is a new decision and journaled as one."""
        v = evaluate_position(
            core(price=200.0, re_underwritten=True), AS_OF
        )
        assert v.rule != "E5"

    def test_a_profit_alone_is_not_a_reason(self) -> None:
        """Up a lot inside the band is not an exit."""
        assert evaluate_position(core(price=110.0), AS_OF).action is Action.NONE


# ---------------------------------------------------------------- E6, E7


class TestTrimAndTimeStop:
    def test_appreciation_past_thirty_five_trims_to_twenty_five(self) -> None:
        v = evaluate_position(core(weight=TRIM_TRIGGER_WEIGHT + 0.01), AS_OF)
        assert (v.action, v.rule) == (Action.TRIM, "E6")
        assert v.target_weight == TRIM_TARGET_WEIGHT

    def test_at_the_trigger_it_holds(self) -> None:
        v = evaluate_position(core(weight=TRIM_TRIGGER_WEIGHT), AS_OF)
        assert v.action is Action.NONE

    def test_eight_quiet_reviews_hit_the_time_stop(self) -> None:
        v = evaluate_position(
            core(quarterly_reviews_without_progress=TIME_STOP_QUARTERS), AS_OF
        )
        assert (v.action, v.rule) == (Action.SELL, "E7")

    def test_seven_is_not_yet(self) -> None:
        v = evaluate_position(
            core(quarterly_reviews_without_progress=TIME_STOP_QUARTERS - 1), AS_OF
        )
        assert v.action is Action.NONE


# ------------------------------------------------------------------ E8


class TestStatisticalRebalance:
    def test_outside_the_buffer_it_sells(self) -> None:
        v = evaluate_position(
            statistical(composite_rank=41), AS_OF, rebalancing=True
        )
        assert (v.action, v.rule) == (Action.SELL, "E8")

    def test_inside_the_buffer_it_holds(self) -> None:
        v = evaluate_position(
            statistical(composite_rank=40), AS_OF, rebalancing=True
        )
        assert v.action is Action.NONE

    def test_the_buffer_exists_to_cut_turnover(self) -> None:
        """Bought into the top 20, held to 40."""
        v = evaluate_position(
            statistical(composite_rank=25), AS_OF, rebalancing=True
        )
        assert v.action is Action.NONE

    def test_failing_a_gate_sells_regardless_of_rank(self) -> None:
        v = evaluate_position(
            statistical(composite_rank=1, still_qualifies=False),
            AS_OF,
            rebalancing=True,
        )
        assert (v.action, v.rule) == (Action.SELL, "E8")

    def test_nothing_happens_outside_a_rebalance(self) -> None:
        """Section 7: statistical trades only on the quarterly clock."""
        v = evaluate_position(statistical(composite_rank=999), AS_OF)
        assert v.action is Action.NONE

    def test_it_does_not_touch_core(self) -> None:
        v = evaluate_position(core(composite_rank=999), AS_OF, rebalancing=True)
        assert v.rule != "E8"


# ------------------------------------------------------------------ E9


class TestLimitBreach:
    def test_a_breach_trims_the_offender(self) -> None:
        v = evaluate_position(statistical(limit_breach="cluster 52%"), AS_OF)
        assert (v.action, v.rule) == (Action.TRIM, "E9")

    def test_it_is_last_in_the_order(self) -> None:
        v = evaluate_position(
            core(limit_breach="cluster 52%", terminal_filing="Form 25"), AS_OF
        )
        assert v.rule == "E2"


# ------------------------------------------------------------------ book


class TestEvaluateBook:
    def test_a_quiet_book_returns_nothing(self) -> None:
        assert evaluate_book([core(), statistical(), event()], AS_OF) == []

    def test_only_triggered_positions_are_returned(self) -> None:
        verdicts = evaluate_book(
            [core(), statistical(filing_age_days=500), event()], AS_OF
        )
        assert [v.ticker for v in verdicts] == ["S"]

    def test_an_empty_book_is_not_an_error(self) -> None:
        assert evaluate_book([], AS_OF) == []

    def test_sells_are_distinguishable_from_trims(self) -> None:
        verdicts = evaluate_book(
            [
                core(terminal_filing="Form 25"),
                statistical(limit_breach="cluster"),
            ],
            AS_OF,
        )
        by_ticker = {v.ticker: v for v in verdicts}
        assert by_ticker["C"].sells
        assert not by_ticker["S"].sells


class TestNothingElseSells:
    """The absences are as much the doctrine as the rules are."""

    def test_a_falling_price_is_not_an_exit(self) -> None:
        assert evaluate_position(core(price=10.0), AS_OF).action is Action.NONE

    def test_dead_money_is_not_an_exit(self) -> None:
        v = evaluate_position(
            core(price=100.0, quarterly_reviews_without_progress=3), AS_OF
        )
        assert v.action is Action.NONE

    def test_a_statistical_name_is_untouched_between_rebalances(self) -> None:
        v = evaluate_position(
            statistical(composite_rank=500, still_qualifies=False), AS_OF
        )
        assert v.action is Action.NONE
