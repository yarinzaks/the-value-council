"""Repair the decision journal: strict JSON, no repeated rows.

Two defects, one pass, because both are rewrites of the same files
and running them separately leaves a tree half-repaired.

Why these exist
---------------

A trading day gets more than one run. The US open and the US close
each scan the whole book, and a retry or a manual dispatch adds more.
Until the logger learned to recognise a row it had already written,
every run appended the strategy's intent for every held name again, so
the day's file grew a second identical copy of every row.

The dashboard renders the journal as written, so Benjamin Graham's
recent-decisions list printed each of his six holdings twice on
2026-11-02, and React warned about two children sharing a key because
two movements had the same date and the same kind.

What this removes
-----------------

Only rows that are byte-identical to an earlier row in the same file,
compared across every field. Two genuinely different events differ
somewhere — a price, a confidence, a rationale — and an execution
(FILL) never equals the intent (BUY) that preceded it, so nothing with
information in it is at risk. The first occurrence always stays, in
place, so ordering is untouched.

The collision copies named "<day> 2.json" are left alone. They are a
file-sync artifact, the loaders already ignore them (see
dashboard/src/lib/day-files.ts), and deleting files is not this
script's business.

Bare NaN
--------

``json.dumps`` writes a non-finite float as the bare token ``NaN``,
which Python reads back happily and ``JSON.parse`` refuses. The
dashboard loader skips a file it cannot parse, so one absent metric
cost a reader the whole agent-day — five of them on 2026-06-19, where
the visible symptom was a position card showing six exits against the
seven the journal held. Those values become ``null``, which is how the
rest of the schema already says "not computed".

The logger no longer writes either defect; this is for the files
already on disk. Note that there are two trees — the runner writes to
``~/Library/Application Support/value-council`` on a Mac and to the
repo's ``data/`` in CI (see core.paths) — so point ``--root`` at each.

Usage::

    python -m scripts.dedupe_decision_rows --dry-run
    python -m scripts.dedupe_decision_rows
    python -m scripts.dedupe_decision_rows --root data/decisions
"""

from __future__ import annotations

import argparse
import json
from math import isfinite
from pathlib import Path

from core.paths import decisions_dir


# A file-sync collision copy: "2026-10-30 2.json" beside the original.
def _is_collision_copy(path: Path) -> bool:
    return path.stem.rsplit(" ", 1)[-1].isdigit() and " " in path.stem


def strict(value: object) -> object:
    """``value`` with every non-finite float replaced by ``None``."""
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, dict):
        return {k: strict(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strict(v) for v in value]
    return value


def count_non_finite(value: object) -> int:
    if isinstance(value, float):
        return 0 if isfinite(value) else 1
    if isinstance(value, dict):
        return sum(count_non_finite(v) for v in value.values())
    if isinstance(value, list):
        return sum(count_non_finite(v) for v in value)
    return 0


def dedupe(rows: list[dict]) -> tuple[list[dict], int]:
    """``rows`` with byte-identical repeats dropped, and how many went.

    The first occurrence of each distinct row keeps its position.
    """
    seen: set[str] = set()
    kept: list[dict] = []
    for row in rows:
        key = json.dumps(row, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept, len(rows) - len(kept)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change and write nothing",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="decisions directory (default: the project's)",
    )
    args = parser.parse_args()

    root = args.root or decisions_dir()
    files = sorted(p for p in root.glob("*/*.json") if not _is_collision_copy(p))

    touched = 0
    removed = 0
    nulls = 0
    for path in files:
        try:
            rows = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  skipped (unreadable) {path}: {exc}")
            continue
        if not isinstance(rows, list):
            continue

        kept, dropped = dedupe(rows)
        nulled = count_non_finite(kept)
        if not dropped and not nulled:
            continue

        touched += 1
        removed += dropped
        nulls += nulled
        note = []
        if dropped:
            note.append(f"{len(rows)} -> {len(kept)} rows")
        if nulled:
            note.append(f"{nulled} non-finite -> null")
        print(f"  {path.relative_to(root)}: {', '.join(note)}")
        if not args.dry_run:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(strict(kept), indent=2, default=str, allow_nan=False)
            )
            tmp.replace(path)

    verb = "would repair" if args.dry_run else "repaired"
    print(f"\n{verb} {touched} of {len(files)} files: "
          f"{removed} duplicate rows dropped, {nulls} non-finite values nulled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
