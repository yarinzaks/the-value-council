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
reopened the same day looks untouched.

What the exit price is
----------------------

The decision journal (``data/decisions/<agent>/<day>.json``) records
every executed close as "Closed live position: entry $X, exit $Y", and
Y is the price the sale got — the cash in the next book agrees with it,
and the book's last mark does not (Buffett's CPB on 2026-11-09: mark
23.13, journal and cash 22.70). So the exit is Y when the journal has
exactly one for that name that day, and the last mark only otherwise.
Each row's ``tags`` says which it is, and names the history that makes
a row unlike a fill:

* ``exit-from-journal`` / ``exit-at-last-mark`` — where the price came from.
* ``cash-merger`` — settled by hand at the merger consideration, not
  traded (THR, Lynch, 2026-08-07: $63.89, commit ``f897dc47``).
* ``rename`` — the symbol changed and the lot carried on under the new
  one, so nothing was realized here (ASGN -> EFOR, same commit). The
  lot's gain or loss is realized when the successor is sold.
* ``frozen-price`` — the exit or the entry equals the last close of a
  symbol with no bar for more than ``MAX_CARRY_FORWARD_DAYS`` before
  that day, per ``data/prices``: a price nobody could have traded at
  (Marks' ASGN round trips at 19.08, the 2026-06-18 close).
* ``fractional-era`` — dated 2026-08-07..2026-10-30, when the books held
  fractional shares the broker cannot hold (from the open run
  ``1421cc78`` to the floor in ``76405a9d``).
* ``closed-session`` — dated to a day the US market was shut. A row is
  dated by the commit that changed the book, and a commit on a holiday
  or a weekend is not a session (audit RK-06). Kept, not dropped:
  removing history is the owner's call.

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
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from core.live.trade_ledger import LedgerEntry
from core.logger import get_logger
from core.market_calendar import why_closed
from core.paths import trade_ledger_dir

logger = get_logger("scripts.backfill_trade_ledger")

REPO_PATH = "data/portfolios"
#: Read from the working tree, like the books are read from its history.
DECISIONS_PATH = "data/decisions"
PRICES_PATH = "data/prices"

#: ``core.backtest.data_loader.MAX_CARRY_FORWARD_DAYS``, the age past
#: which the runner stops trusting a close. Copied rather than imported
#: so this script does not load the backtest package; a test pins the two.
MAX_CARRY_FORWARD_DAYS = 5

#: First and last session whose books held fractional shares (CE-1b /
#: ENG-1b): the open run ``1421cc78`` through the floor in ``76405a9d``.
FRACTIONAL_ERA = ("2026-08-07", "2026-10-30")

#: The tag on a row dated to a day the market was shut. Shared with
#: ``scripts.tag_closed_sessions``, which wrote it onto the rows this
#: script reconstructed before it knew the rule.
CLOSED_SESSION_TAG = "closed-session"

#: Commits that settled a corporate action by hand instead of trading.
#: Keyed by full SHA, then ticker: (tag, settlement price or None). A
#: rename has no price — nothing was sold. ``f897dc47`` is documented in
#: its own message: THR cash merger at $63.89 (CECO Environmental), and
#: the ASGN -> EFOR rename across Graham, Schloss, Marks and Klarman.
SETTLEMENTS: dict[str, dict[str, tuple[str, float | None]]] = {
    "f897dc47460e7baac1b2dd34a143c4396ae33112": {
        "THR": ("cash-merger", 63.89),
        "ASGN": ("rename", None),
    },
}

_EXIT = re.compile(r"Closed live position: entry \$[\d,.]+, exit \$([\d,]+(?:\.\d+)?)")


def _book_at(sha: str, path: str) -> dict | None:
    result = subprocess.run(
        ["git", "show", f"{sha}:{path}"],
        capture_output=True, text=True,
    )
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _history(agent: str) -> list[tuple[str, str]]:
    """Every commit of the agent's book, oldest first, with its path then.

    ``--follow`` with ``--reverse`` stops at the first rename and returns
    a single commit (RK-02: ``mohnish_pabrai.json`` was ``the_council.json``
    until ``dccdee27``), so the log is read newest first and reversed
    here, and each commit keeps the name the file had in it.
    """
    out = subprocess.run(
        ["git", "log", "--follow", "--name-only", "--format=%x00%H", "--",
         f"{REPO_PATH}/{agent}.json"],
        capture_output=True, text=True, check=True,
    ).stdout
    path = f"{REPO_PATH}/{agent}.json"
    newest_first: list[tuple[str, str]] = []
    for chunk in out.split("\0"):
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        # A commit listed without a file (a merge) keeps the name the
        # file had in the next newer commit.
        path = lines[1] if len(lines) > 1 else path
        newest_first.append((lines[0], path))
    return newest_first[::-1]


def _journal_exits(agent: str, day: str, decisions_dir: Path) -> dict[str, float]:
    """Exit prices the runner wrote down that day, by ticker.

    A ticker with two different exits that day is left out: picking one
    would be a guess, and the mark is at least labelled as one.
    """
    path = decisions_dir / agent / f"{day}.json"
    if not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"unreadable journal {path}: {exc}; exits fall back to marks")
        return {}
    seen: dict[str, set[float]] = defaultdict(set)
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("decision") not in ("SELL", "EXIT"):
            continue
        match = _EXIT.search(str(row.get("rationale") or ""))
        if match:
            seen[str(row.get("ticker", "")).upper()].add(
                float(match.group(1).replace(",", ""))
            )
    return {t: next(iter(p)) for t, p in seen.items() if len(p) == 1}


def _last_bar(ticker: str, prices_dir: Path,
              cache: dict[str, tuple[date, float] | None]) -> tuple[date, float] | None:
    """The last (date, close) in ``data/prices/<ticker>.json``, if any."""
    if ticker not in cache:
        found: tuple[date, float] | None = None
        path = prices_dir / f"{ticker}.json"
        if path.is_file():
            try:
                points = json.loads(path.read_text()).get("points") or []
                if points:
                    found = (date.fromisoformat(points[-1]["d"]),
                             float(points[-1]["c"]))
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                logger.warning(f"unreadable prices {path}: {exc}")
        cache[ticker] = found
    return cache[ticker]


def _frozen(day: str | None, price: float | None,
            last: tuple[date, float] | None) -> bool:
    """A price equal to a close that was already stale on ``day``."""
    if not day or price is None or last is None:
        return False
    try:
        when = date.fromisoformat(day[:10])
    except ValueError:
        return False
    return (when - last[0] > timedelta(days=MAX_CARRY_FORWARD_DAYS)
            and abs(price - last[1]) < 0.005)


def reconstruct(
    agent: str,
    *,
    decisions_dir: Path | None = None,
    prices_dir: Path | None = None,
) -> dict[str, list[LedgerEntry]]:
    """Closed and trimmed positions, by the date they disappeared."""
    decisions_dir = decisions_dir or Path(DECISIONS_PATH)
    prices_dir = prices_dir or Path(PRICES_PATH)
    bars: dict[str, tuple[date, float] | None] = {}
    by_day: dict[str, list[LedgerEntry]] = defaultdict(list)
    previous: dict | None = None

    for sha, path in _history(agent):
        book = _book_at(sha, path)
        if book is None:
            continue
        if previous is not None:
            before = {p["ticker"]: p for p in previous.get("positions", [])}
            after = {p["ticker"]: p for p in book.get("positions", [])}
            day = book.get("last_open_date") or book.get("last_updated", "")[:10]
            if day:
                journal = _journal_exits(agent, day, decisions_dir)
                settled = SETTLEMENTS.get(sha, {})
                for ticker, held in before.items():
                    still = after.get(ticker)
                    sold = held["shares"] - (still["shares"] if still else 0.0)
                    if sold <= 1e-6:
                        continue
                    # The last mark the book carried — the fallback when
                    # nothing recorded what the sale actually got.
                    exit_price = (
                        (still or held).get("current_price")
                        or held["current_price"]
                        or held["entry_price"]
                    )
                    realized = True
                    tags: list[str] = []
                    if ticker in settled:
                        kind, settlement = settled[ticker]
                        tags.append(kind)
                        if settlement is not None:
                            exit_price = settlement
                        realized = kind != "rename"
                    elif ticker in journal:
                        exit_price = journal[ticker]
                        tags.append("exit-from-journal")
                    else:
                        tags.append("exit-at-last-mark")
                    last = _last_bar(ticker, prices_dir, bars)
                    if _frozen(day, exit_price, last) or _frozen(
                        held.get("entry_date"), held.get("entry_price"), last
                    ):
                        tags.append("frozen-price")
                    if FRACTIONAL_ERA[0] <= day <= FRACTIONAL_ERA[1]:
                        tags.append("fractional-era")
                    if why_closed(date.fromisoformat(day)) is not None:
                        tags.append(CLOSED_SESSION_TAG)
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
                            realized_pnl_usd=(
                                (exit_price - held["entry_price"]) * sold
                                if realized else 0.0
                            ),
                            source="reconstructed",
                            # The book knew when and at what cost it
                            # opened the line; a reconstruction can
                            # carry that even though it cannot carry
                            # the execution price.
                            entry_date=held.get("entry_date") or None,
                            entry_price=held.get("entry_price"),
                            tags=tuple(tags),
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
