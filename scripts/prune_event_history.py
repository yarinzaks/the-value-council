"""Drop dividend and split records a book can no longer consult.

Why
~~~

Audit item 23 / CE-3b. ``LivePortfolio.paid_dividends`` and
``applied_splits`` are meant to stay the size of the book: ``sell()`` and
``apply_split()`` pop a line's records when they close it. The
2026-10-31 whole-share flooring (``scripts.floor_fractional_lots``,
commit 76405a9d) closed sub-share lots by removing them from the list
instead, so their records stayed — 13 keys across four committed books:
seven for names no longer held, and six for names bought back after the
flooring, whose old ex-dates fall before the new entry date.

Neither kind changes a payment. Settlement scans each line from
``max(entry_date, dividend_floor_date)``, so a date before the entry is
never looked up, and a key with no line is never looked up at all. The
records are dead weight that make the book claim a history it no longer
has, and one more trap for whoever reads the map next.

What this does
~~~~~~~~~~~~~~

Runs ``LivePortfolio.prune_event_history`` on every book and writes back
**only** those two maps. Every other byte of the file is left as it was
read — the book is not re-serialised through ``to_dict``, which would
re-round its positions and move fields this has no business moving.

It refuses to write a book whose cash, NAV or cumulative dividends would
differ after the prune. They cannot — the method touches neither — and
the check is there so that stays a fact rather than a claim.

Usage::

    .venv/bin/python -m scripts.prune_event_history --dir data/portfolios
    .venv/bin/python -m scripts.prune_event_history --dir data/portfolios --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from core.live.portfolio import LivePortfolio
from core.paths import portfolios_dir

_MAPS = ("paid_dividends", "applied_splits")


class PruneRefusedError(Exception):
    """The prune would have moved money; nothing was written."""


def _money(book: LivePortfolio) -> tuple[float, float, float]:
    return (book.cash, book.total_nav, book.cumulative_dividends)


def prune_text(text: str) -> tuple[str, dict[str, dict[str, list[str]]]]:
    """Return ``text`` with stale records removed, and what was removed.

    ``text`` is a book as written by ``LivePortfolio.save``. Only the two
    record maps are replaced; when nothing is removed the input is
    returned unchanged. Raises :class:`PruneRefusedError` if the book's money
    would differ afterwards.
    """
    data: dict[str, Any] = json.loads(text)
    book = LivePortfolio.from_dict(data)
    before = _money(book)
    removed = book.prune_event_history()
    if _money(book) != before:
        raise PruneRefusedError(
            f"{book.agent}: cash/NAV/dividends {before} -> {_money(book)}; "
            f"pruning records must not move money"
        )
    if not any(removed[name] for name in _MAPS):
        return text, removed

    data["paid_dividends"] = {
        k: sorted(v) for k, v in sorted(book.paid_dividends.items())
    }
    if "applied_splits" in data or book.applied_splits:
        data["applied_splits"] = {
            k: sorted(v) for k, v in sorted(book.applied_splits.items())
        }
    new_text = json.dumps(data, indent=2, ensure_ascii=False)

    # And the file as it will be written reads back to the same money.
    after = _money(LivePortfolio.from_dict(json.loads(new_text)))
    if after != before:
        raise PruneRefusedError(
            f"{book.agent}: written book reads back {after}, read {before}"
        )
    return new_text, removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dir", type=Path, default=None,
        help="portfolio directory (default: the data root's portfolios/)",
    )
    parser.add_argument("--apply", action="store_true", help="write the books")
    args = parser.parse_args(argv)

    directory: Path = args.dir if args.dir is not None else portfolios_dir()
    paths = sorted(
        p for p in directory.glob("*.json") if not p.stem.startswith(".")
    )
    if not paths:
        print(f"no portfolios under {directory}")
        return 2

    planned: list[tuple[Path, str]] = []
    for path in paths:
        text = path.read_text()
        try:
            new_text, removed = prune_text(text)
        except PruneRefusedError as exc:
            print(f"REFUSED: {exc}")
            return 1
        for name in _MAPS:
            for ticker, dates in removed[name].items():
                print(f"{path.stem:<26}{name:<16}{ticker:<8}{', '.join(dates)}")
        if new_text != text:
            planned.append((path, new_text))

    print()
    print(f"{len(planned)} of {len(paths)} books change")
    if not args.apply:
        print("dry run — nothing written. Re-run with --apply.")
        return 0
    # Every book was checked before any is written, so a refusal above
    # leaves the directory exactly as it was.
    for path, new_text in planned:
        # Atomic, like `LivePortfolio.save`: a crash mid-write must not
        # leave half a book.
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(new_text)
        tmp.replace(path)
    print(f"written to {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
