"""Mark every snapshot and ledger row dated to a day the market was shut.

Usage::

    .venv/bin/python -m scripts.tag_closed_sessions            # report only
    .venv/bin/python -m scripts.tag_closed_sessions --apply    # write

Why
~~~

Before the runner learned the market calendar, the schedule ran Monday
to Friday and a holiday was an ordinary day to it; a run that crossed
midnight, or a hand-run catch-up, could date a book to a weekend. The
audit (RK-06) counted 55 committed snapshots on 2026-05-25, 06-19,
07-03, 08-15, 08-16 and 09-07, and 44 ledger rows on the same days —
every one reconstructed by ``scripts.backfill_trade_ledger`` from a
commit that happened to land on a shut day.

Deleting them is not this script's decision: it is more than five files
of history, and the owner decides that. So they are kept and marked:

* a snapshot gets ``"closed_session": true`` (``DailySnapshot`` knows
  the field, so every reader still loads the day);
* a ledger row gets the tag ``closed-session``, the same tag the
  backfill now writes itself, so a rerun of it keeps the mark.

Idempotent: a file already marked is not rewritten. A file that cannot
be read is reported and left exactly as it was, and the run exits 1.
Only ``<YYYY-MM-DD>.json`` is read; a sync-collision copy
(``"<day> 2.json"``, gitignored) is not a day of its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from core.market_calendar import why_closed
from core.paths import data_root, trade_ledger_dir
from scripts.backfill_trade_ledger import CLOSED_SESSION_TAG


@dataclass
class TagReport:
    #: ``agent/<day>.json`` for every snapshot marked (or that would be).
    snapshots: list[str] = field(default_factory=list)
    #: Ledger rows marked (or that would be).
    ledger_rows: int = 0
    #: ``agent/<day>.json: reason`` for every file that could not be read.
    errors: list[str] = field(default_factory=list)
    #: ``<day>: <why shut>`` for each closed day seen, for the report.
    days: dict[str, str] = field(default_factory=dict)


def _closed_days(root: Path) -> list[tuple[Path, str, str]]:
    """(path, day, why shut) for every day-file under *root* on a shut day."""
    out: list[tuple[Path, str, str]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*/*.json")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue  # not "<YYYY-MM-DD>.json": a sync copy, or not ours
        reason = why_closed(day)
        if reason is not None:
            out.append((path, path.stem, reason))
    return out


def _name(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def tag(snapshots_root: Path, trades_root: Path, *, apply: bool) -> TagReport:
    """Mark the closed-session files under both roots; write only if *apply*."""
    report = TagReport()

    for path, day, reason in _closed_days(snapshots_root):
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise TypeError("not a JSON object")
        except (OSError, ValueError, TypeError) as exc:
            report.errors.append(f"{_name(path)}: {exc}")
            continue
        report.days[day] = reason
        if data.get("closed_session") is True:
            continue
        data["closed_session"] = True
        report.snapshots.append(_name(path))
        if apply:
            # The writer's own format: ``save_snapshot`` dumps indent=2.
            path.write_text(json.dumps(data, indent=2))

    for path, day, reason in _closed_days(trades_root):
        try:
            rows = json.loads(path.read_text())
            if not isinstance(rows, list) or not all(
                isinstance(r, dict) for r in rows
            ):
                raise TypeError("not a JSON list of rows")
        except (OSError, ValueError, TypeError) as exc:
            report.errors.append(f"{_name(path)}: {exc}")
            continue
        report.days[day] = reason
        changed = 0
        for row in rows:
            tags = list(row.get("tags") or [])
            if CLOSED_SESSION_TAG in tags:
                continue
            row["tags"] = [*tags, CLOSED_SESSION_TAG]
            changed += 1
        report.ledger_rows += changed
        if changed and apply:
            # The writer's own format: ``record_day`` dumps indent=1.
            path.write_text(json.dumps(rows, indent=1))

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write the marks. Without it, nothing is written.")
    parser.add_argument("--snapshots", type=Path, default=None,
                        help="snapshot root; defaults to the resolved data root's")
    parser.add_argument("--trades", type=Path, default=None,
                        help="ledger root; defaults to the resolved data root's")
    args = parser.parse_args(argv)

    snapshots_root = args.snapshots or data_root() / "snapshots"
    trades_root = args.trades or trade_ledger_dir()
    report = tag(snapshots_root, trades_root, apply=args.apply)

    verb = "marked" if args.apply else "would mark"
    for day, reason in sorted(report.days.items()):
        print(f"{day}: no US session ({reason})")
    print(f"{verb} {len(report.snapshots)} snapshot(s) and "
          f"{report.ledger_rows} ledger row(s) as closed-session")
    for entry in report.errors:
        print(f"ERROR unreadable, left as it was: {entry}", file=sys.stderr)
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
