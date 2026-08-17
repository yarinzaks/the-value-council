"""The run-type table, and the calendar that says which run today is.

Part 7 calls the table *"what stops a high-frequency schedule from
becoming a high-turnover strategy"*, and until this module existed the
only path that trades had no notion of a run type at all: ``DailyRunner``
was invoked once per calendar day and did the same thing every time. So
§7's monthly "publish the would-be list, trade nothing" had no artefact,
the Feb/May/Aug/Nov rebalance did not exist, and E7's eight-REVIEW time
stop was denominated in a run no code path produced.

Why the predicates take the previous run date
---------------------------------------------

"First COUNCIL of the month" cannot be computed from a date alone
without assuming which days the market is open. A predicate written as
"is this the first Monday" is wrong on a Monday holiday: no run happens,
and the month's council is then skipped entirely rather than moved. So
the boundary questions are asked against the last run that actually
happened, which is exact, needs no holiday calendar, and degrades
correctly — the first run *after* a boundary is the one that carries it.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents.council.cadence import (
    REBALANCE_MONTHS,
    RunType,
    is_first_run_of_month,
    is_first_run_of_week,
    is_parameter_season,
    is_rebalance_run,
    permissions_for,
)


# ---------------------------------------------------------------- the table
class TestPermissions:
    def test_every_run_type_has_a_row(self) -> None:
        for run in RunType:
            assert permissions_for(run) is not None, f"{run} has no row"

    def test_heartbeat_may_not_touch_a_position(self) -> None:
        """Part 7: 'open, add to, or resize anything' is the whole column."""
        p = permissions_for(RunType.HEARTBEAT)
        assert not p.may_open
        assert not p.may_add
        assert not p.may_resize

    def test_heartbeat_may_still_exit(self) -> None:
        """It is the run that watches kill triggers and breaking 8-Ks.

        §7 gives it E1-E7 and forced exits explicitly. A run that may
        notice a delisting and not act on it would be worse than no run.
        """
        assert permissions_for(RunType.HEARTBEAT).may_exit

    def test_close_marks_and_queues_but_does_not_open(self) -> None:
        p = permissions_for(RunType.CLOSE)
        assert p.may_queue_reading
        assert not p.may_open

    def test_reading_does_not_trade_at_all(self) -> None:
        p = permissions_for(RunType.READING)
        assert not p.may_open
        assert not p.may_exit
        assert not p.may_rebalance

    def test_council_is_the_only_run_that_may_open(self) -> None:
        """The cooling-off rule lives or dies on this being singular."""
        openers = [r for r in RunType if permissions_for(r).may_open]
        assert openers == [RunType.COUNCIL]

    def test_review_may_kill_a_holding_but_not_start_one(self) -> None:
        p = permissions_for(RunType.REVIEW)
        assert p.may_exit
        assert not p.may_open

    def test_calibration_trades_nothing_of_any_kind(self) -> None:
        p = permissions_for(RunType.CALIBRATION)
        assert not p.may_open
        assert not p.may_add
        assert not p.may_resize
        assert not p.may_exit
        assert not p.may_rebalance

    def test_only_calibration_may_change_a_parameter(self) -> None:
        """§9.2: the only run in which any number in the file may move."""
        movers = [r for r in RunType if permissions_for(r).may_change_parameters]
        assert movers == [RunType.CALIBRATION]

    def test_only_council_may_rebalance(self) -> None:
        assert [r for r in RunType if permissions_for(r).may_rebalance] == [
            RunType.COUNCIL
        ]


# ------------------------------------------------------------- week and month
class TestFirstRunOfWeek:
    def test_the_first_run_ever_opens_a_week(self) -> None:
        assert is_first_run_of_week(date(2026, 8, 17), None)

    def test_monday_after_a_friday_opens_a_week(self) -> None:
        assert is_first_run_of_week(date(2026, 8, 17), date(2026, 8, 14))

    def test_tuesday_after_monday_does_not(self) -> None:
        assert not is_first_run_of_week(date(2026, 8, 18), date(2026, 8, 17))

    def test_a_monday_holiday_moves_the_council_it_does_not_skip_it(self) -> None:
        """The reason this takes a previous date rather than a weekday.

        2026-01-19 is a Monday the US market is closed. No run happens,
        so the week's council is the Tuesday — moved, not lost. A
        weekday test would have skipped the week entirely.
        """
        assert is_first_run_of_week(date(2026, 1, 20), date(2026, 1, 16))

    def test_a_gap_of_several_weeks_still_opens_one(self) -> None:
        assert is_first_run_of_week(date(2026, 8, 17), date(2026, 7, 20))

    def test_the_year_boundary_is_not_a_new_week_by_itself(self) -> None:
        """2025-12-30 and 2026-01-01 are the same ISO week."""
        assert not is_first_run_of_week(date(2026, 1, 1), date(2025, 12, 30))


class TestFirstRunOfMonth:
    def test_the_first_run_ever_opens_a_month(self) -> None:
        assert is_first_run_of_month(date(2026, 8, 3), None)

    def test_crossing_into_august_opens_it(self) -> None:
        assert is_first_run_of_month(date(2026, 8, 3), date(2026, 7, 31))

    def test_the_second_run_of_august_does_not(self) -> None:
        assert not is_first_run_of_month(date(2026, 8, 4), date(2026, 8, 3))

    def test_crossing_a_year_opens_a_month(self) -> None:
        assert is_first_run_of_month(date(2026, 1, 2), date(2025, 12, 31))


# ------------------------------------------------------------- the rebalance
class TestRebalanceRun:
    def test_the_months_are_the_ones_the_doctrine_names(self) -> None:
        """Feb/May/Aug/Nov: by then the prior quarter's 10-Qs are filed."""
        assert frozenset({2, 5, 8, 11}) == REBALANCE_MONTHS

    def test_the_first_council_of_august_rebalances(self) -> None:
        assert is_rebalance_run(
            date(2026, 8, 3), previous_run=date(2026, 7, 31), last_rebalance=None
        )

    def test_a_later_council_in_the_same_month_does_not(self) -> None:
        """Once a quarter, not once a week for four weeks."""
        assert not is_rebalance_run(
            date(2026, 8, 10),
            previous_run=date(2026, 8, 7),
            last_rebalance=date(2026, 8, 3),
        )

    def test_a_non_rebalance_month_never_does(self) -> None:
        for month, day in ((1, 5), (3, 2), (6, 1), (9, 7), (12, 7)):
            assert not is_rebalance_run(
                date(2026, month, day),
                previous_run=date(2026, month, day).replace(day=1),
                last_rebalance=None,
            ), f"month {month} is not a rebalance month"

    def test_a_mid_week_run_in_a_rebalance_month_does_not(self) -> None:
        """It has to be a COUNCIL run, and those open the week."""
        assert not is_rebalance_run(
            date(2026, 8, 5), previous_run=date(2026, 8, 4), last_rebalance=None
        )

    def test_the_next_quarter_rebalances_again(self) -> None:
        assert is_rebalance_run(
            date(2026, 11, 2),
            previous_run=date(2026, 10, 30),
            last_rebalance=date(2026, 8, 3),
        )

    def test_a_missed_quarter_does_not_double_up(self) -> None:
        """If August never ran, November is still one rebalance."""
        assert is_rebalance_run(
            date(2026, 11, 2),
            previous_run=date(2026, 10, 30),
            last_rebalance=date(2026, 5, 4),
        )

    def test_the_same_month_a_year_later_is_a_new_rebalance(self) -> None:
        assert is_rebalance_run(
            date(2027, 8, 2),
            previous_run=date(2027, 7, 30),
            last_rebalance=date(2026, 8, 3),
        )


class TestParameterSeason:
    def test_only_the_first_quarter(self) -> None:
        """§7: 'parameter season once a year, Q1 only'."""
        assert is_parameter_season(date(2026, 1, 5))
        assert is_parameter_season(date(2026, 3, 31))

    def test_not_the_rest_of_the_year(self) -> None:
        for month in (4, 5, 8, 9, 11, 12):
            assert not is_parameter_season(date(2026, month, 1)), month


class TestRunTypeValues:
    def test_the_two_implemented_runs_keep_their_wire_names(self) -> None:
        """runs.py already writes these into data/council/runs/.

        Changing either string would orphan every record on disk and
        break the workflow step that passes the mode through.
        """
        assert RunType.HEARTBEAT == "heartbeat"
        assert RunType.CLOSE == "close"

    def test_every_run_type_in_the_doctrine_is_present(self) -> None:
        assert {r.value for r in RunType} == {
            "heartbeat",
            "close",
            "reading",
            "council",
            "review",
            "calibration",
        }

    @pytest.mark.parametrize("bad", ["open", "daily", ""])
    def test_an_unknown_name_is_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            RunType(bad)
