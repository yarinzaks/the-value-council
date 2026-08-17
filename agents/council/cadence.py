"""Which run today is, and what that run is allowed to do.

Doctrine Part 7 makes the agent's permissions depend on the run it is
in, and calls that table *"what stops a high-frequency schedule from
becoming a high-turnover strategy — the failure that kills retail
systems far more often than bad stock picking."* The table was written
down and never wired: ``DailyRunner`` ran once per calendar day and did
the same thing every time, so the one path that trades had no run type
at all.

Two halves live here. :class:`RunType` and :func:`permissions_for` are
Part 7's table, transcribed. The calendar predicates answer §7's
scheduling questions — is this the first council of the week, the first
run of the month, the quarterly rebalance.

Nothing here reads the book, the clock, or storage. Every function is a
pure question about dates, which is what makes the rebalance calendar
testable at all.

Why the predicates take the previous run date
---------------------------------------------

"The first COUNCIL of the month" cannot be answered from one date
without assuming which days the market is open. Written as "is this the
first Monday", it is wrong on a Monday the market is closed: no run
happens, and the month's council is skipped rather than moved. Asked
against the last run that actually happened, the answer is exact, needs
no holiday calendar, and fails in the right direction — the first run
*after* a boundary is the one that carries it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final


class RunType(StrEnum):
    """The six runs of Part 7.

    ``heartbeat`` and ``close`` keep the exact strings ``runs.py``
    already writes into ``data/council/runs/<date>_<mode>.json`` and the
    workflow already passes on the command line. Renaming either would
    orphan every record on disk.
    """

    HEARTBEAT = "heartbeat"
    CLOSE = "close"
    READING = "reading"
    COUNCIL = "council"
    REVIEW = "review"
    CALIBRATION = "calibration"


@dataclass(frozen=True)
class Permissions:
    """One row of Part 7's table.

    Every field is a "may", never a "must": the table is a ceiling on
    what a run can do, and a run that does less than it is permitted is
    behaving normally. Part 7 is explicit that whole quarters in which
    the Council trades nothing are the expected case.
    """

    #: Start a position that does not exist. Council only — the
    #: cooling-off rule ("nothing is bought in the run in which it is
    #: identified, or the run in which it is read") is enforceable only
    #: because exactly one run type can open.
    may_open: bool
    #: Add to a position already held.
    may_add: bool
    #: Trim or grow a position toward a different weight.
    may_resize: bool
    #: Sell: forced exits, kill triggers, E1-E9. Distinct from resizing
    #: because the heartbeat may do this and nothing else — a run that
    #: could see a delisting notice and not act on it would be worse
    #: than no run.
    may_exit: bool
    #: Execute the statistical sleeve's quarterly rebalance.
    may_rebalance: bool
    #: Queue a candidate for the reading run. Identifying is not buying.
    may_queue_reading: bool
    #: Change a number in COUNCIL_SELECTION. §9.2 gives this to the
    #: calibration run alone, and only in parameter season.
    may_change_parameters: bool


def _row(
    *,
    open_: bool = False,
    add: bool = False,
    resize: bool = False,
    exit_: bool = False,
    rebalance: bool = False,
    queue: bool = False,
    parameters: bool = False,
) -> Permissions:
    return Permissions(
        may_open=open_,
        may_add=add,
        may_resize=resize,
        may_exit=exit_,
        may_rebalance=rebalance,
        may_queue_reading=queue,
        may_change_parameters=parameters,
    )


#: Part 7's table, and §7's row for the calibration run.
_PERMISSIONS: Final[dict[RunType, Permissions]] = {
    # "check risk limits, drawdown, kill triggers, breaking 8-Ks"
    # / may not "open, add to, or resize anything"
    RunType.HEARTBEAT: _row(exit_=True),
    # "mark the book, scan the hunting grounds, queue candidates for
    # reading" / may not "open a position"
    RunType.CLOSE: _row(exit_=True, queue=True),
    # "read filings on queued candidates; write up findings" / may not
    # "trade" — and that means at all, not merely may-not-open.
    RunType.READING: _row(),
    # "decide on candidates read at least one full day earlier"
    RunType.COUNCIL: _row(
        open_=True, add=True, resize=True, exit_=True, rebalance=True, queue=True
    ),
    # "re-run the thesis on a holding, update or kill it" / may not
    # "open a new position"
    RunType.REVIEW: _row(exit_=True, resize=True),
    # "No trading of any kind." Its output is a score of the process.
    RunType.CALIBRATION: _row(parameters=True),
}


def permissions_for(run: RunType) -> Permissions:
    """Part 7's row for ``run``."""
    return _PERMISSIONS[run]


#: §7: "Rebalance months are Feb / May / Aug / Nov because by then the
#: prior quarter's 10-Qs have overwhelmingly been *filed* — and the rank
#: may use only facts with ``filed <=`` the rebalance date."
REBALANCE_MONTHS: Final[frozenset[int]] = frozenset({2, 5, 8, 11})


def is_first_run_of_week(as_of: date, previous_run: date | None) -> bool:
    """Whether ``as_of`` opens a new ISO week.

    This is the COUNCIL trigger: §7 puts the council weekly, and the
    first run of a week is the one that carries it. No run at all
    happened between the two dates, so a closed Monday moves the council
    to Tuesday rather than losing the week.
    """
    if previous_run is None:
        return True
    return as_of.isocalendar()[:2] != previous_run.isocalendar()[:2]


def is_first_run_of_month(as_of: date, previous_run: date | None) -> bool:
    """Whether ``as_of`` opens a new month.

    §7's monthly CLOSE recomputes the full rank on paper and publishes
    the would-be list, trading nothing. That published list is what the
    quarterly rebalance later executes from, which is how the
    cooling-off rule reaches the machine as well as the Council.
    """
    if previous_run is None:
        return True
    return (as_of.year, as_of.month) != (previous_run.year, previous_run.month)


def is_rebalance_run(
    as_of: date,
    *,
    previous_run: date | None,
    last_rebalance: date | None,
) -> bool:
    """Whether ``as_of`` is the quarterly statistical rebalance.

    §7: *the first COUNCIL in Feb / May / Aug / Nov*. Three conditions,
    and all three are load-bearing:

    * a rebalance month, so the prior quarter's 10-Qs are filed;
    * a COUNCIL run, i.e. the first run of its week, because no other
      run type may rebalance;
    * not already done for this month, or four consecutive Mondays in
      February would each rebalance the sleeve.

    A quarter whose rebalance never ran is not made up later — the next
    one is a single rebalance, not two. Turnover skipped is turnover
    saved, and a doubled trade list would be the opposite of what the
    calendar is for.
    """
    if as_of.month not in REBALANCE_MONTHS:
        return False
    if not is_first_run_of_week(as_of, previous_run):
        return False
    already_done_this_month = last_rebalance is not None and (
        last_rebalance.year,
        last_rebalance.month,
    ) == (as_of.year, as_of.month)
    return not already_done_this_month


def is_parameter_season(as_of: date) -> bool:
    """§7: parameter season is once a year, Q1 only.

    Outside it, :attr:`Permissions.may_change_parameters` is necessary
    but not sufficient — the calibration run still scores the process,
    it simply may not move a number while doing so.
    """
    return as_of.month <= 3
