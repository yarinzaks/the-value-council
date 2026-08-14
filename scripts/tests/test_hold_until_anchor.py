"""The hold has to be right in both offsets, and it has to fail loudly.

Every test here injects ``now`` rather than reading the clock, so the
suite gives the same answer in July as in January and on a runner in any
timezone. That matters more than usual for this module: the whole point
of it is timezone arithmetic, and a test that quietly agreed with the
machine it ran on would prove nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts.hold_until_anchor import MARKET_TZ, MAX_HOLD, main, seconds_until

UTC = ZoneInfo("UTC")
TEL_AVIV = ZoneInfo("Asia/Jerusalem")

#: The four anchors in daily-paper-trading.yml.
SCAN = "10:00"
MARKS = ("12:00", "14:00", "16:30")


def ny(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=MARKET_TZ)


# ------------------------------------------------------- the arithmetic


def test_waits_the_whole_lead_when_the_scheduler_was_punctual() -> None:
    # Cron 13:00 UTC = 09:00 EDT, anchor 10:00 EDT.
    assert seconds_until(SCAN, ny(2026, 8, 10, 9, 0)) == 3600


def test_returns_zero_exactly_at_the_anchor() -> None:
    assert seconds_until(SCAN, ny(2026, 8, 10, 10, 0)) == 0


def test_goes_negative_once_the_anchor_has_passed() -> None:
    """The ordinary outcome when GitHub overran the lead."""
    assert seconds_until(SCAN, ny(2026, 8, 10, 10, 51)) == -51 * 60


# ------------------------------------------------------------------ DST
# The reason this module exists at all.


@pytest.mark.parametrize("anchor", (SCAN, *MARKS))
def test_anchor_lands_at_the_same_market_time_in_both_offsets(
    anchor: str,
) -> None:
    """Summer and winter must put the run at the same point of the session."""
    for day in (datetime(2026, 8, 10), datetime(2026, 12, 7)):
        start = ny(day.year, day.month, day.day, 8, 0)
        arrived = start + timedelta(seconds=seconds_until(anchor, start))
        assert arrived.strftime("%H:%M") == anchor


def test_the_scan_would_have_traded_before_the_open_under_a_utc_anchor() -> None:
    """The bug this replaced, stated as a test so it cannot come back.

    A fixed 14:00 UTC anchor is 10:00 ET in August and 09:00 ET in
    December — half an hour before the market opens.
    """
    december = datetime(2026, 12, 7, 14, 0, tzinfo=UTC).astimezone(MARKET_TZ)
    assert december.strftime("%H:%M") == "09:00"

    start = ny(2026, 12, 7, 8, 0)
    held = start + timedelta(seconds=seconds_until(SCAN, start))
    assert held.strftime("%H:%M") == "10:00"


def test_the_last_mark_would_have_preceded_the_bell_under_a_utc_anchor() -> None:
    """The same bug at the other end of the day.

    20:30 UTC is 16:30 ET in August and 15:30 ET in December, which files
    an intraday quote as the day's closing price.
    """
    december = datetime(2026, 12, 7, 20, 30, tzinfo=UTC).astimezone(MARKET_TZ)
    assert december.strftime("%H:%M") == "15:30"

    start = ny(2026, 12, 7, 14, 0)
    held = start + timedelta(seconds=seconds_until("16:30", start))
    assert held.strftime("%H:%M") == "16:30"


def test_the_israeli_clock_holds_except_in_the_shoulder_windows() -> None:
    """Stated plainly because the dashboard advertises Israeli times.

    Both zones being on summer time, or both on standard time, leaves
    them exactly seven hours apart, so a 10:00 ET anchor is 17:00 in Tel
    Aviv for most of the year. What breaks that is not winter itself but
    the fortnight-ish when only one of them has switched: Israel leaves
    summer time on the last Sunday of October and the US on the first
    Sunday of November, and in spring the US moves first. In those two
    windows the run lands an hour earlier by the Israeli clock — 383 of
    401 days checked are 17:00, the other 18 are 16:00.
    """
    aligned = [
        ny(2026, 8, 10, 10, 0),  # both on summer time
        ny(2026, 12, 7, 10, 0),  # both on standard time
    ]
    for day in aligned:
        assert day.astimezone(TEL_AVIV).strftime("%H:%M") == "17:00"

    shoulder = [
        ny(2026, 10, 28, 10, 0),  # Israel already back, the US not yet
        ny(2027, 3, 18, 10, 0),  # the US already forward, Israel not yet
    ]
    for day in shoulder:
        assert day.astimezone(TEL_AVIV).strftime("%H:%M") == "16:00"


def test_a_caller_in_another_timezone_gets_the_same_answer() -> None:
    """The runner's own timezone must not enter into it."""
    in_ny = ny(2026, 8, 10, 9, 0)
    assert seconds_until(SCAN, in_ny.astimezone(UTC)) == seconds_until(SCAN, in_ny)
    assert seconds_until(SCAN, in_ny.astimezone(TEL_AVIV)) == seconds_until(SCAN, in_ny)


# --------------------------------------------------------------- the CLI


def test_past_the_anchor_returns_cleanly_without_sleeping(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "scripts.hold_until_anchor.datetime",
        _frozen(ny(2026, 8, 10, 10, 30)),
    )
    slept = _no_sleep(monkeypatch)

    assert main(["--anchor", SCAN]) == 0
    assert slept == []
    assert "already 30 min past 10:00 ET" in capsys.readouterr().out


def test_a_real_lead_is_slept_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "scripts.hold_until_anchor.datetime",
        _frozen(ny(2026, 12, 7, 8, 0)),
    )
    slept = _no_sleep(monkeypatch)

    # 08:00 EST to the 10:00 EST anchor — the full winter lead.
    assert main(["--anchor", SCAN]) == 0
    assert slept == [2 * 3600]
    assert "holding 120 min until 10:00 ET" in capsys.readouterr().out


def test_a_lead_past_the_ceiling_aborts_instead_of_idling(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cron moved without its anchor must not park a job for hours."""
    monkeypatch.setattr(
        "scripts.hold_until_anchor.datetime",
        _frozen(ny(2026, 8, 10, 3, 0)),
    )
    slept = _no_sleep(monkeypatch)

    assert main(["--anchor", SCAN]) == 1
    assert slept == []
    assert "::error::" in capsys.readouterr().err


def test_the_ceiling_clears_the_longest_real_hold() -> None:
    """150 min has to sit above the 120-minute winter lead."""
    winter_lead = timedelta(seconds=seconds_until(SCAN, ny(2026, 12, 7, 8, 0)))
    assert winter_lead == timedelta(minutes=120)
    assert winter_lead < MAX_HOLD


# ------------------------------------------------------------- fixtures


def _frozen(instant: datetime) -> type:
    """A datetime whose ``now`` is fixed, leaving the rest of it intact."""

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return instant.astimezone(tz) if tz else instant

    return Frozen


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record what would have been slept rather than sleeping it."""
    calls: list[float] = []
    monkeypatch.setattr(
        "scripts.hold_until_anchor._time.sleep", lambda s: calls.append(s)
    )
    return calls
