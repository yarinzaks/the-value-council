"""Publish the exchange calendar as data the dashboard can read.

Why this exists rather than a second implementation
---------------------------------------------------

The dashboard is TypeScript and the calendar is Python, and the obvious
move — porting sixty lines of date arithmetic — gives two copies of a
rule that must agree forever. They would not: Rule 7.2's New Year
exception alone is the kind of detail one copy acquires and the other
does not, and the symptom would be a page quietly disagreeing with the
runner about which days exist.

So the rules stay in one place and their answers are published. The
file is small — a dozen entries a year — and covers enough years that
nobody has to remember to regenerate it.

Usage::

    .venv/bin/python -m scripts.export_market_calendar
    .venv/bin/python -m scripts.export_market_calendar --from 2024 --to 2040
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from core.market_calendar import closures, half_days
from core.paths import cache_dir

#: Wide enough that regenerating is not a maintenance task anyone has to
#: diarise, small enough that the file stays a few kilobytes.
DEFAULT_FROM = 2024
DEFAULT_TO = 2040

FILENAME = "market_calendar.json"


def build(first: int, last: int) -> dict[str, object]:
    shut: dict[str, str] = {}
    short: dict[str, str] = {}
    for year in range(first, last + 1):
        for day, name in closures(year).items():
            if first <= day.year <= last:
                shut[day.isoformat()] = name
        for day, name in half_days(year).items():
            short[day.isoformat()] = name
    return {
        "generated": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source": "core/market_calendar.py",
        "years": [first, last],
        "closures": dict(sorted(shut.items())),
        "half_days": dict(sorted(short.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="first", type=int, default=DEFAULT_FROM)
    parser.add_argument("--to", dest="last", type=int, default=DEFAULT_TO)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.last < args.first:
        raise SystemExit(f"--to {args.last} is before --from {args.first}")

    payload = build(args.first, args.last)
    out = args.out or (cache_dir() / FILENAME)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")

    closed = len(payload["closures"])  # type: ignore[arg-type]
    short = len(payload["half_days"])  # type: ignore[arg-type]
    print(f"wrote {out}: {closed} closures and {short} half-days, "
          f"{args.first}-{args.last}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
