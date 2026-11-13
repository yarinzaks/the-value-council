"""Recover what closed trades can be recovered, and label the rest honestly.

Usage::

    .venv/bin/python -m scripts.backfill_trade_ledger --dry-run
    .venv/bin/python -m scripts.backfill_trade_ledger --apply

The ledger starts empty, so every book that traded before it existed
reports its whole realized gain as unattributed. The books were
committed daily, so diffing consecutive commits recovers part of it.

How much is part
----------------

On Graham it recovers $1,241 of a realized $3,239 — 38%. The rest
cannot be recovered by any amount of care: a name bought and sold
between two commits leaves no trace at all, a position closed and
reopened the same day looks untouched, and an exit can only be valued
at the last mark the book carried rather than at the price the sale
actually got.

So every entry this writes is marked ``reconstructed``, the breakdown
keeps reporting the remainder as unattributed, and the page shows the
two differently. A reconstruction presented as a record would be worse
than no reconstruction, because it would look complete.

Recorded days are never overwritten. Once the runner has written a day,
that day is exact and this script leaves it alone. A day this script
wrote earlier is its own to rewrite, and ``--refresh-reconstructed``
does so — the first reconstruction predates the ledger's entry fields,
so every row it left is a sale with no beginning.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict

from core.live.trade_ledger import LedgerEntry
from core.logger import get_logger
from core.paths import trade_ledger_dir

logger = get_logger("scripts.backfill_trade_ledger")

REPO_PATH = "data/portfolios"


def _book_at(sha: str, agent: str) -> dict | None:
    result = subprocess.run(
        ["git", "show", f"{sha}:{REPO_PATH}/{agent}.json"],
        capture_output=True, text=True,
    )
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _history(agent: str) -> list[str]:
    return subprocess.run(
        ["git", "log", "--format=%H", "--reverse", "--follow", "--",
         f"{REPO_PATH}/{agent}.json"],
        capture_output=True, text=True,
    ).stdout.split()


def reconstruct(agent: str) -> dict[str, list[LedgerEntry]]:
    """Closed and trimmed positions, by the date they disappeared."""
    by_day: dict[str, list[LedgerEntry]] = defaultdict(list)
    previous: dict | None = None

    for sha in _history(agent):
        book = _book_at(sha, agent)
        if book is None:
            continue
        if previous is not None:
            before = {p["ticker"]: p for p in previous.get("positions", [])}
            after = {p["ticker"]: p for p in book.get("positions", [])}
            day = book.get("last_open_date") or book.get("last_updated", "")[:10]
            if day:
                for ticker, held in before.items():
                    still = after.get(ticker)
                    sold = held["shares"] - (still["shares"] if still else 0.0)
                    if sold <= 1e-6:
                        continue
                    # The last mark the book carried. Not the execution
                    # price, which was never written down.
                    exit_price = (
                        (still or held).get("current_price")
                        or held["current_price"]
                        or held["entry_price"]
                    )
                    by_day[day].append(
                        LedgerEntry(
                            date=day,
                            agent=agent,
                            ticker=ticker,
                            side="SELL",
                            shares=sold,
                            price=exit_price,
                            gross_value=sold * exit_price,
                            cost_paid=0.0,
                            realized_pnl_usd=(exit_price - held["entry_price"]) * sold,
                            source="reconstructed",
                            # The book knew when and at what cost it
                            # opened the line; a reconstruction can
                            # carry that even though it cannot carry
                            # the execution price.
                            entry_date=held.get("entry_date") or None,
                            entry_price=held.get("entry_price"),
                        )
                    )
        previous = book
    return by_day


def _all_reconstructed(path) -> bool:
    """Whether every row in a ledger day is this script's own work."""
    try:
        rows = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return bool(rows) and all(r.get("source") == "reconstructed" for r in rows)


def write_days(root, agent: str, by_day: dict[str, list[LedgerEntry]],
               *, refresh_reconstructed: bool = False) -> tuple[int, int]:
    """Write the reconstructed days. Returns (written, kept).

    A day the runner recorded is exact and is never touched. A day this
    script wrote before is overwritten only when asked, because the
    first reconstruction predates the entry fields.
    """
    written = kept = 0
    for day, entries in sorted(by_day.items()):
        path = root / agent / f"{day}.json"
        if path.exists() and not (
            refresh_reconstructed and _all_reconstructed(path)
        ):
            kept += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([e.to_dict() for e in entries], indent=1))
        written += 1
    return written, kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write the files. Without it, nothing is written.")
    parser.add_argument("--refresh-reconstructed", action="store_true",
                        help="Rewrite days this script wrote before; recorded "
                             "days are still kept.")
    parser.add_argument("--agent", action="append", default=None,
                        help="Limit to one agent; repeatable.")
    args = parser.parse_args(argv)

    root = trade_ledger_dir()
    agents = args.agent or sorted(
        p.stem for p in __import__("pathlib").Path(REPO_PATH).glob("*.json")
    )

    print(f"{'agent':<26}{'days':>7}{'trades':>8}{'realized $':>14}  status")
    print("-" * 70)
    grand = 0.0
    for agent in agents:
        by_day = reconstruct(agent)
        trades = sum(len(v) for v in by_day.values())
        realized = sum(e.realized_pnl_usd for v in by_day.values() for e in v)
        grand += realized
        written = skipped = 0
        if args.apply:
            written, skipped = write_days(
                root, agent, by_day,
                refresh_reconstructed=args.refresh_reconstructed,
            )
        status = (
            f"wrote {written}, kept {skipped} recorded" if args.apply else "dry run"
        )
        print(f"{agent:<26}{len(by_day):>7}{trades:>8}{realized:>14,.2f}  {status}")
    print("-" * 70)
    print(f"{'TOTAL':<26}{'':>7}{'':>8}{grand:>14,.2f}")
    if not args.apply:
        print("\nNothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
