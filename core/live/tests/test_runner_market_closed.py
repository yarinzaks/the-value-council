"""A day with no session must move no book.

The schedule ran Monday to Friday and knew nothing else, so four 2026
public holidays were ordinary trading days to it: 1,770 journal rows
and 21 executed trades, every one priced at the previous session's
close, because that is what a quote feed returns when there is no
session.

The runner declining is only half of it. The workflow asserts after
every run that each book was refreshed, so a day correctly declined
here would fail the build unless the verifier knows the same thing.
Both read ``core.market_calendar``, and the last test holds them to it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from core.live.runner import DailyRunner
from core.market_calendar import is_trading_day
from scripts.verify_run_state import main as verify_main

#: Dates the agents actually traded on while the exchange was shut.
HOLIDAYS = [
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day observed
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving — the next one
]
WEEKEND = date(2026, 11, 1)
OPEN_DAY = date(2026, 10, 28)   # an ordinary Wednesday
HALF_DAY = date(2026, 11, 27)   # day after Thanksgiving, 13:00 ET


class _Exploding:
    """Any use at all is a failure: a shut day must touch nothing."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the runner reached for {name!r} on a shut day")


def a_runner() -> DailyRunner:
    runner = DailyRunner.__new__(DailyRunner)
    runner.market = "US"
    runner.adapters = _Exploding()          # type: ignore[assignment]
    runner.universe = _Exploding()          # type: ignore[assignment]
    runner.price_loader = _Exploding()      # type: ignore[assignment]
    return runner


class TestTheRunnerDeclines:
    @pytest.mark.parametrize("day", HOLIDAYS)
    def test_a_scan_does_nothing_on_a_holiday(self, day: date) -> None:
        assert a_runner().run(as_of=day) == []

    @pytest.mark.parametrize("day", HOLIDAYS)
    def test_a_mark_does_nothing_either(self, day: date) -> None:
        assert a_runner().run_mark_to_market(as_of=day) == []

    def test_a_weekend_is_declined_too(self) -> None:
        assert a_runner().run(as_of=WEEKEND) == []

    def test_forcing_does_not_override_a_shut_market(self) -> None:
        """Force is for the holding floor, not for inventing a session."""
        assert a_runner().run(as_of=HOLIDAYS[0], force=True) == []

    def test_an_open_day_still_proceeds(self) -> None:
        """The guard must not be the reason nothing ever trades again.

        The stand-in raises on first use, so reaching it proves the
        guard let the day through rather than swallowing it.
        """
        with pytest.raises(AssertionError, match="reached for"):
            a_runner().run(as_of=OPEN_DAY)

    def test_an_early_close_is_a_trading_day(self) -> None:
        """13:00 ET moves the auction; it does not cancel the session."""
        assert is_trading_day(HALF_DAY)
        with pytest.raises(AssertionError, match="reached for"):
            a_runner().run(as_of=HALF_DAY)


class TestTheVerifierAgrees:
    """Otherwise a correctly declined day fails the build."""

    @pytest.mark.parametrize("day", HOLIDAYS + [WEEKEND])
    def test_a_shut_day_is_not_a_failed_run(self, day: date) -> None:
        for mode in ("open", "close"):
            assert verify_main(["--mode", mode, "--as-of", day.isoformat()]) == 0

    def test_the_two_read_the_same_calendar(self, tmp_path: Any) -> None:
        """An open day with no fresh portfolio is still a failure.

        The exemption has to be the calendar's and nothing wider; a
        verifier that returned 0 on any empty directory would make the
        whole check decorative.
        """
        assert verify_main(
            ["--mode", "open", "--as-of", OPEN_DAY.isoformat(),
             "--portfolios", str(tmp_path)]
        ) == 1
