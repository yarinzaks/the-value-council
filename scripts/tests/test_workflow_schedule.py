"""The schedule must say the same thing in every place it is written.

There are four of them: the ``cron:`` lines in daily-paper-trading.yml,
the ``anchor=`` values its dispatch step emits, the comment beside each
cron, and the crons the watchdog audits. Nothing in YAML links them, so
until this file existed they drifted, and on 2026-08-14 the drift showed
up as a day with no run at all: the watchdog was still auditing a cron
that had been renamed out from under it.

These tests read the workflows as text rather than through a YAML
parser. That is deliberate — what is being checked is textual agreement
between parts of the same file, PyYAML would be a new dependency for no
gain, and the shapes matched here are the ones a human edits by hand.

The rules enforced:

* every cron has exactly one dispatch case, and every case a cron
* an anchor is present for US and absent for TASE
* the cron fires before its anchor, by the same lead in both offsets
* the anchor sits where the doctrine says, inside the NYSE session
* the hold step's runaway ceiling is above the largest real lead
* the watchdog audits crons that exist
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.hold_until_anchor import MAX_HOLD

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
DAILY = WORKFLOWS / "daily-paper-trading.yml"
WATCHDOG = WORKFLOWS / "watchdog.yml"

NEW_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

#: A Monday in each offset, for resolving anchors both ways.
EDT_MONDAY = datetime(2026, 8, 10, tzinfo=NEW_YORK)
EST_MONDAY = datetime(2026, 12, 7, tzinfo=NEW_YORK)

#: Regular NYSE session, New York time.
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)

#: The scan trades, so it must be far enough past the open that the
#: widest spreads of the day are behind it. The workflow's own comment
#: puts it at 10:00; this is the floor that comment has to clear.
MIN_SETTLE_AFTER_OPEN = timedelta(minutes=15)

#: Yahoo needs this long after the bell to publish the settled daily
#: bar. A close mark struck sooner re-reads an intraday quote.
MIN_SETTLE_AFTER_CLOSE = timedelta(minutes=30)

# ---------------------------------------------------------------- parsing

CRON_LINE = re.compile(
    r"^\s*- cron: '(?P<cron>[^']+)'(?:\s*#\s*anchor (?P<anchor>\d{2}:\d{2}) ET)?\s*$"
)
CASE_LABEL = re.compile(r'^\s*\*":(?P<cron>[^"]+)"\)\s*$')
CASE_OUTPUT = re.compile(r'^\s*echo "(?P<key>\w+)=(?P<value>[^"]*)" >> "\$GITHUB_OUTPUT"\s*$')


def _crons(text: str) -> dict[str, str | None]:
    """Cron expression -> the anchor written in its trailing comment."""
    out: dict[str, str | None] = {}
    for line in text.splitlines():
        m = CRON_LINE.match(line)
        if m:
            out[m["cron"]] = m["anchor"]
    return out


def _dispatch_cases(text: str) -> dict[str, dict[str, str]]:
    """Cron expression -> the outputs its case emits."""
    out: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line in text.splitlines():
        label = CASE_LABEL.match(line)
        if label:
            current = label["cron"]
            out[current] = {}
            continue
        if current is None:
            continue
        if line.strip() == ";;":
            current = None
            continue
        emit = CASE_OUTPUT.match(line)
        if emit:
            out[current][emit["key"]] = emit["value"]
    return out


def _anchor_utc(anchor: str, day: datetime) -> datetime:
    """The instant ``anchor`` (New York time) falls on ``day``."""
    hh, mm = (int(p) for p in anchor.split(":"))
    return day.replace(hour=hh, minute=mm).astimezone(UTC)


def _cron_utc(cron: str, day: datetime) -> datetime:
    """The instant ``cron`` fires on ``day``. Cron is always UTC."""
    minute, hour = cron.split()[:2]
    return datetime(
        day.year, day.month, day.day, int(hour), int(minute), tzinfo=UTC
    )


DAILY_TEXT = DAILY.read_text()
CRONS = _crons(DAILY_TEXT)
CASES = _dispatch_cases(DAILY_TEXT)
US_CRONS = sorted(c for c, a in CRONS.items() if a is not None)


# ------------------------------------------------------------ the parsing
# works at all. Without these, a regex that silently matched nothing would
# make every test below pass over an empty collection.


def test_parser_finds_every_cron() -> None:
    assert len(CRONS) == 6, CRONS


def test_parser_finds_every_case() -> None:
    # Six crons plus the workflow_dispatch and catch-all labels, which
    # use a different shape and are not matched by CASE_LABEL.
    assert len(CASES) == 6, CASES


def test_four_us_crons_carry_an_anchor() -> None:
    assert len(US_CRONS) == 4, US_CRONS


# ---------------------------------------------------- crons match cases


def test_every_cron_has_a_dispatch_case() -> None:
    assert set(CRONS) == set(CASES), {
        "cron without a case": sorted(set(CRONS) - set(CASES)),
        "case without a cron": sorted(set(CASES) - set(CRONS)),
    }


@pytest.mark.parametrize("cron", sorted(CRONS))
def test_case_emits_all_three_outputs(cron: str) -> None:
    assert set(CASES[cron]) == {"mode", "market", "anchor"}, CASES[cron]


@pytest.mark.parametrize("cron", sorted(CRONS))
def test_comment_anchor_matches_dispatch_anchor(cron: str) -> None:
    """The comment beside the cron is what a reader trusts; make it true."""
    assert CASES[cron]["anchor"] == (CRONS[cron] or "")


@pytest.mark.parametrize("cron", sorted(CRONS))
def test_us_is_anchored_and_tase_is_not(cron: str) -> None:
    if CASES[cron]["market"] == "US":
        assert CRONS[cron] is not None, f"{cron} would start at a random time"
    else:
        assert CRONS[cron] is None, f"{cron} holds the concurrency lock for nothing"


# ------------------------------------------------- crons fire before them


@pytest.mark.parametrize("cron", US_CRONS)
@pytest.mark.parametrize(
    "day, offset", [(EDT_MONDAY, "EDT"), (EST_MONDAY, "EST")]
)
def test_cron_fires_before_its_anchor(cron: str, day: datetime, offset: str) -> None:
    lead = _anchor_utc(CRONS[cron], day) - _cron_utc(cron, day)
    assert lead > timedelta(0), (
        f"{offset}: {cron} fires after its {CRONS[cron]} ET anchor, so the "
        f"hold does nothing and the run starts late"
    )


@pytest.mark.parametrize(
    "day, offset, expected",
    [(EDT_MONDAY, "EDT", timedelta(minutes=60)),
     (EST_MONDAY, "EST", timedelta(minutes=120))],
)
def test_lead_is_uniform_across_the_us_crons(
    day: datetime, offset: str, expected: timedelta
) -> None:
    """One lead for all four, so the hold ceiling can be a single number."""
    leads = {
        cron: _anchor_utc(CRONS[cron], day) - _cron_utc(cron, day)
        for cron in US_CRONS
    }
    assert set(leads.values()) == {expected}, f"{offset}: {leads}"


def test_hold_ceiling_clears_the_largest_lead() -> None:
    """The runaway guard must not trip on a legitimate winter hold.

    The link runs the other way from the rest of this file: MAX_HOLD is
    a constant in the module the workflow calls, and what it has to
    clear is the longest lead any cron here actually asks for.
    """
    worst = max(
        _anchor_utc(CRONS[c], EST_MONDAY) - _cron_utc(c, EST_MONDAY)
        for c in US_CRONS
    )
    assert worst < MAX_HOLD, f"ceiling {MAX_HOLD} would abort a real {worst} hold"


def test_the_workflow_calls_the_module_that_holds() -> None:
    """A hold step that stopped invoking it would fail silently open."""
    assert "scripts.hold_until_anchor" in DAILY_TEXT


# ------------------------------------------------ anchors sit where they
# are supposed to, in both offsets


@pytest.mark.parametrize("cron", US_CRONS)
@pytest.mark.parametrize("day, offset", [(EDT_MONDAY, "EDT"), (EST_MONDAY, "EST")])
def test_scan_runs_inside_the_session(cron: str, day: datetime, offset: str) -> None:
    """The run that trades must be in the session, past the opening spreads."""
    if CASES[cron]["mode"] != "open":
        pytest.skip("mark-only run")
    anchor = _anchor_utc(CRONS[cron], day).astimezone(NEW_YORK).time()
    floor = (
        datetime.combine(day.date(), SESSION_OPEN) + MIN_SETTLE_AFTER_OPEN
    ).time()
    assert floor <= anchor < SESSION_CLOSE, (
        f"{offset}: scan at {anchor} ET is outside {floor}-{SESSION_CLOSE}"
    )


@pytest.mark.parametrize("cron", US_CRONS)
@pytest.mark.parametrize("day, offset", [(EDT_MONDAY, "EDT"), (EST_MONDAY, "EST")])
def test_marks_read_a_price_that_exists(cron: str, day: datetime, offset: str) -> None:
    """Every mark is either inside the session or safely past the bell.

    The gap between them is what this forbids: a mark struck at 15:30 ET
    reads an intraday quote and stores it as the day's close.
    """
    if CASES[cron]["mode"] != "close":
        pytest.skip("the trading scan")
    anchor = _anchor_utc(CRONS[cron], day).astimezone(NEW_YORK).time()
    settled = (
        datetime.combine(day.date(), SESSION_CLOSE) + MIN_SETTLE_AFTER_CLOSE
    ).time()
    assert SESSION_OPEN <= anchor < SESSION_CLOSE or anchor >= settled, (
        f"{offset}: mark at {anchor} ET is neither in-session nor "
        f"{MIN_SETTLE_AFTER_CLOSE} past the {SESSION_CLOSE} bell"
    )


@pytest.mark.parametrize("day, offset", [(EDT_MONDAY, "EDT"), (EST_MONDAY, "EST")])
def test_the_last_mark_of_the_day_is_past_the_bell(
    day: datetime, offset: str
) -> None:
    """The rule the in-session check above is too weak to catch.

    Whichever run goes last sets the mark the dashboard carries until
    the market opens again, so it has to read the settled daily bar.
    Being inside the session is not enough: the old 20:30 UTC anchor
    resolved to 15:30 EST, which is in-session and still wrong, because
    it files an intraday quote as the day's close.
    """
    last = max(_anchor_utc(CRONS[c], day) for c in US_CRONS)
    settled = (
        datetime.combine(day.date(), SESSION_CLOSE) + MIN_SETTLE_AFTER_CLOSE
    ).time()
    assert last.astimezone(NEW_YORK).time() >= settled, (
        f"{offset}: the day's last mark is struck at "
        f"{last.astimezone(NEW_YORK).time()} ET, before the bar settles at {settled}"
    )


# ------------------------------------------------------------ watchdog


def test_watchdog_audits_crons_that_exist() -> None:
    """The bug that produced a whole day without a run.

    The watchdog identifies its target by the cron expression, which
    daily-paper-trading.yml publishes as the run title. Rename a cron
    there and the watchdog silently audits a run that can never appear.
    """
    audited = set(re.findall(r'echo "audit=([^"]+)" >> "\$GITHUB_OUTPUT"', WATCHDOG.read_text()))
    assert audited, "the watchdog no longer names what it audits"
    assert audited <= set(CRONS), sorted(audited - set(CRONS))


def test_watchdog_checks_after_the_run_it_audits() -> None:
    """Asking before the run could have finished only causes false alarms."""
    text = WATCHDOG.read_text()
    watchdog_crons = sorted(_crons(text))
    for audited in sorted(re.findall(r'echo "audit=([^"]+)" >> "\$GITHUB_OUTPUT"', text)):
        anchor = _anchor_utc(CRONS[audited], EDT_MONDAY)
        assert any(
            _cron_utc(w, EDT_MONDAY) >= anchor for w in watchdog_crons
        ), f"nothing audits {audited} after its {CRONS[audited]} ET anchor"


def test_watchdog_can_actually_dispatch() -> None:
    """It could not, for the whole of its existence.

    ``gh workflow run`` resolves the repository from the local git
    remote and this job has no checkout, so every dispatch it attempted
    died on "fatal: not a git repository" — including the one on
    2026-08-14 that had correctly spotted the missing run.
    """
    text = WATCHDOG.read_text()
    dispatch = text.split("Dispatch make-up run", 1)[1]
    assert "--repo" in dispatch, "gh has no repository to dispatch into"
