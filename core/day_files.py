"""Which files in a per-day directory to actually read.

Every store under data/ is one JSON file per agent per day, and the
Python readers took the directory listing at face value: glob
``*.json``, sort, read all of them. That is correct until the directory
holds two files for one day.

On the Mac it does. macOS file sync resolves a write collision by
keeping both sides and renaming one of them ``"<day> 2.json"``. The
audit (RK-03) counted 147 under the repo's data/ — 122 in trades, 14
in decisions, 2 in snapshots — and ``read_ledger('david_dreman')``
returned 139 rows where the ledger held 106. ``.gitignore`` hides the
copies from git, so CI never sees them; a reader on the Mac does.

This is the Python twin of ``dashboard/src/lib/day-files.ts`` and keeps
its rule exactly, so the dashboard and the Python readers cannot
disagree about which rows a day holds.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

#: A sync-collision copy: ``"<day> 2"`` as the stem beside ``"<day>"``.
_COLLISION_COPY = re.compile(r" (\d+)$")


def canonical_day_files(paths: Iterable[Path]) -> list[Path]:
    """The files to read from a per-day directory: one per day, sorted.

    A day with both an original and collision copies yields the
    original. A day with only copies yields the lowest-numbered one —
    dropping the day entirely would lose real rows, which is the worse
    of the two failures. Anything not ending in ``.json`` is ignored.
    """
    best: dict[str, tuple[int, Path]] = {}
    for path in paths:
        if path.suffix != ".json":
            continue
        stem = path.name[: -len(".json")]
        copy = _COLLISION_COPY.search(stem)
        # rank 0 is the original; a copy ranks by its own number, so the
        # lowest survives when the original is gone.
        day = stem[: copy.start()] if copy else stem
        rank = int(copy.group(1)) if copy else 0
        held = best.get(day)
        if held is None or rank < held[0]:
            best[day] = (rank, path)
    return sorted((p for _, p in best.values()), key=lambda p: p.name)


__all__ = ["canonical_day_files"]
