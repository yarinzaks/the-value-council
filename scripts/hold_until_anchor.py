"""Wait until the moment a scheduled run is supposed to start.

GitHub treats scheduled events as best-effort. Measured over 51 runs of
daily-paper-trading.yml the delay was a median of 51 minutes, a minimum
of 4 and a maximum of 109 — and on 2026-08-14 the 14:00 run never fired
at all. A cron cannot be trusted to say *when*, only *roughly when*.

So the cron is demoted to a wake-up call fired an hour or two early, and
this holds the job until the anchor: the moment the work should actually
happen. Whatever the scheduler did, the scan starts at the anchor, which
makes both the market correctness and the arrival time deterministic
instead of a lottery.

Anchors are New York time, never UTC. A fixed UTC anchor is wrong for
five months of the year: 14:00 UTC is 10:00 ET in summer but 09:00 EST
in winter, half an hour before the market opens, and 20:30 UTC is 16:30
ET in summer but 15:30 EST in winter, half an hour before the bell. Both
are the exact failures those anchors exist to prevent.

Usage::

    python -m scripts.hold_until_anchor --anchor 10:00
"""

from __future__ import annotations

import argparse
import sys
import time as _time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

#: Every anchor in this project is a New York market time.
MARKET_TZ = ZoneInfo("America/New_York")

#: Longest hold that can be legitimate. The lead from a cron to its
#: anchor is 60 minutes under EDT and 120 under EST, so anything past
#: this means the cron and the anchor have drifted apart in the workflow
#: file. Failing loudly beats idling for hours on a typo.
#: scripts/tests/test_workflow_schedule.py catches that drift before it
#: ships; this is the belt to its braces.
MAX_HOLD = timedelta(minutes=150)


def seconds_until(anchor: str, now: datetime) -> int:
    """Seconds from ``now`` until ``anchor`` on the same New York day.

    Negative when the anchor has already passed, which is the ordinary
    outcome when the scheduler was late enough to overrun the lead.

    ``now`` is required rather than defaulted so that callers — and
    tests above all — are explicit about which instant they mean.
    """
    hour, minute = (int(part) for part in anchor.split(":"))
    local = now.astimezone(MARKET_TZ)
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # Same New York day by construction, so this cannot straddle
    # midnight, and no DST transition can fall between the two: those
    # happen at 02:00 on a Sunday and every anchor here is a weekday
    # between 09:30 and 16:30.
    return int((target - local).total_seconds())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--anchor",
        required=True,
        help="HH:MM in New York time — the moment the work should begin",
    )
    args = parser.parse_args(argv)

    wait = seconds_until(args.anchor, datetime.now(MARKET_TZ))

    if wait <= 0:
        print(
            f"::notice::already {-wait // 60} min past {args.anchor} ET "
            f"— starting now"
        )
        return 0

    if wait > MAX_HOLD.total_seconds():
        print(
            f"::error::{args.anchor} ET is {wait // 60} min away — the cron "
            f"and the anchor disagree",
            file=sys.stderr,
        )
        return 1

    print(f"::notice::holding {wait // 60} min until {args.anchor} ET")
    # Flushed before sleeping so the Actions log shows why the step is
    # sitting there, rather than going quiet for an hour.
    sys.stdout.flush()
    _time.sleep(wait)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
