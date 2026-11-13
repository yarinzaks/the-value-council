"""Post-run assertion that the daily paper-trading job actually traded.

Why this exists
~~~~~~~~~~~~~~~

The workflow's previous sanity check asked whether *every* portfolio was
still at its $10K seed with zero positions. Once the agents held anything
at all — which has been true since the first successful run — that
condition could never fire again, so the step passed unconditionally and
the watchdog downstream read ``success``.

Meanwhile ``DailyRunner.run`` catches per-agent exceptions and returns an
``AgentRunResult`` carrying the error rather than raising. An agent that
blows up mid-scan never reaches ``portfolio.save()``, so its state file
keeps yesterday's contents and the run still exits 0.

The reliable signal is therefore per-agent freshness: ``last_open_run``
(or ``last_close_run``) is stamped immediately before each save, so a
portfolio whose stamp is not from today did not complete this run.

Unpriced holdings
~~~~~~~~~~~~~~~~~

A fresh stamp says the run finished, not that what it wrote is true.
LEG was acquired on 2026-08-26 and sat in Klarman's book at its last
close for 78 days, through runs that all passed this gate (audit
CE-1a). The runner now flags such a holding (``unpriced_since``); this
gate checks independently, from the price cache, that every holding
more than ``UNPRICED_AFTER_SESSIONS`` sessions past its last bar carries
the flag, and fails the run when one does not. A flagged one passes,
with a warning, because the flag is the correct output until the
holding is settled from the filings.

A ledger day for every run that moved shares
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The runner writes ``data/trades/<agent>/<day>.json`` best-effort and
swallows a failure, and until 9558bc0f the workflow never staged that
directory: 2,235 share changes reached the repo and not one ``executed``
ledger row did (audit ENG-7, RK-01). With ``--baseline-ref``, this gate
reads each book as it stood at that commit — in CI the checkout, which
is the state before this run — and fails the run when a book's holdings
moved but no ledger file for the day exists. A day without trades writes
no ledger file by design, so unchanged holdings need none.

A stock split is the one share change with no order behind it: the
runner restates the holding's count and journals a ``SPLIT`` row, but
writes no ledger day, because cash in lieu is not a trade (audit item
22). Without the baseline it needs nothing; with it, a change that the
day's ``SPLIT`` rows account for exactly — shares before to shares
after, chained through every split of that name — is reported as a
notice, not an error. Any other change in the same book still needs
its ledger day. Before this, a split on a day the holder placed no
order failed the gate, so the run's state was never committed and the
next run split the uncommitted book again.

Usage::

    python -m scripts.verify_run_state --mode open
    python -m scripts.verify_run_state --mode close --as-of 2026-08-05
    python -m scripts.verify_run_state --mode open --baseline-ref HEAD
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from core.live.portfolio import unpriced_since_for
from core.market_calendar import why_closed
from core.paths import decisions_dir, portfolios_dir, prices_db, trade_ledger_dir

#: Newest cached bar for a ticker, or ``None`` when the cache has none.
LastBar = Callable[[str], date | None]

#: Shares per ticker in a book file as it stood before the run; ``{}``
#: for a book the baseline does not hold.
PreviousShares = Callable[[Path], dict[str, float]]

#: Share counts are compared at the precision the book stores them.
_SHARE_DECIMALS = 6

# Which timestamp each mode is expected to refresh. ``run()`` stamps
# ``last_open_run``; ``run_mark_to_market()`` stamps ``last_close_run``.
STAMP_FIELD = {"open": "last_open_run", "close": "last_close_run"}


@dataclass(frozen=True)
class AgentState:
    """One portfolio's post-run condition."""

    agent: str
    nav: float
    positions: int
    stamp: str
    fresh: bool


@dataclass(frozen=True)
class VerifyReport:
    rows: list[AgentState] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    #: ``agent:TICKER (...)`` for every unpriced holding without the flag.
    unflagged: list[str] = field(default_factory=list)
    #: ``agent:TICKER (...)`` for every unpriced holding that carries it.
    flagged: list[str] = field(default_factory=list)
    #: ``agent (changes)`` for every book whose holdings moved with no
    #: ledger file for the day.
    unledgered: list[str] = field(default_factory=list)
    #: ``agent (changes)`` for every book whose only unledgered moves
    #: are accounted for by the day's journaled splits.
    split_adjusted: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only when at least one portfolio was found, every one of
        them is readable and freshly stamped, no unpriced holding is
        left unflagged, and no book moved shares without a ledger day."""
        return (
            bool(self.rows)
            and not self.stale
            and not self.unreadable
            and not self.unflagged
            and not self.unledgered
        )


def last_bar_reader(db: Path) -> LastBar:
    """Read the newest bar per ticker from the price cache, read-only.

    Opened with ``mode=ro`` so a missing file raises rather than being
    created empty — an empty cache would make every holding look
    unpriced, and creating it would hide that the run never wrote one.
    """

    def last_bar(ticker: str) -> date | None:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT MAX(trade_date) FROM prices WHERE ticker = ?",
                (ticker.upper(),),
            ).fetchone()
        finally:
            conn.close()
        if not row or row[0] is None:
            return None
        return date.fromisoformat(str(row[0])[:10])

    return last_bar


def _holdings(positions: object) -> dict[str, float]:
    """Ticker → shares for a book's ``positions`` list.

    Raises ``ValueError``/``TypeError``/``KeyError`` on a malformed
    list, which the caller reports as an unreadable book.
    """
    if not isinstance(positions, list):
        raise TypeError(f"positions is {type(positions).__name__}, not a list")
    return {
        str(p["ticker"]).upper(): round(float(p["shares"]), _SHARE_DECIMALS)
        for p in positions
    }


def git_baseline_reader(directory: Path, ref: str) -> PreviousShares | None:
    """Read each book in *directory* as committed at *ref*.

    ``None`` when *ref* does not resolve to a commit in the repository
    holding *directory* (or there is no repository): the caller must
    then refuse rather than compare against nothing. A book file the
    commit does not hold reads as ``{}`` — a new book starts empty.
    """
    # A ref is never an option; refusing a leading dash keeps git from
    # reading one as such.
    if not ref or ref.startswith("-"):
        return None
    probe = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--verify", "--quiet",
         f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return None
    commit = probe.stdout.strip()

    def previous(path: Path) -> dict[str, float]:
        # "<rev>:./<name>" resolves against -C's directory, so no
        # repository-relative path has to be computed.
        shown = subprocess.run(
            ["git", "-C", str(directory), "show", f"{commit}:./{path.name}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if shown.returncode != 0:
            return {}
        committed = json.loads(shown.stdout)
        if not isinstance(committed, dict):
            raise TypeError(f"committed {path.name} is not a JSON object")
        return _holdings(committed.get("positions", []))

    return previous


#: A journaled split, as the gate reads it: ex-date, shares before, after.
_SplitRow = tuple[str, float, float]


def _journaled_splits(path: Path) -> dict[str, list[_SplitRow]]:
    """Ticker → the ``SPLIT`` rows in one agent's journal day file.

    ``{}`` when the day has no journal file. Raises ``ValueError``/
    ``TypeError``/``KeyError`` on a malformed file or ``SPLIT`` row,
    which the caller reports as unreadable rather than as no split.
    """
    if not path.is_file():
        return {}
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise TypeError(f"{path.name} is {type(rows).__name__}, not a list")
    splits: dict[str, list[_SplitRow]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("decision") != "SPLIT":
            continue
        conditions = row["market_conditions"]
        splits.setdefault(str(row["ticker"]).upper(), []).append(
            (
                str(conditions["ex_date"]),
                round(float(conditions["shares_before"]), _SHARE_DECIMALS),
                round(float(conditions["shares_after"]), _SHARE_DECIMALS),
            )
        )
    return splits


def _explained_by_splits(
    before: dict[str, float],
    after: dict[str, float],
    splits: dict[str, list[_SplitRow]],
) -> set[str]:
    """Tickers whose move from *before* to *after* the splits account for.

    Walks each name's rows oldest ex-date first, taking a row only when
    its ``shares_before`` is the count reached so far, so two splits in
    one run chain and a row left by an earlier attempt that day is
    passed over. A line the split closed (a reverse split leaving less
    than one share) is absent from *after* and counts as zero.
    """
    explained: set[str] = set()
    for ticker, rows in splits.items():
        start = before.get(ticker)
        if start is None:
            continue
        end = after.get(ticker, 0.0)
        reached = start
        for _ex_date, shares_before, shares_after in sorted(rows):
            if shares_before == reached:
                reached = shares_after
        if start != end and reached == end:
            explained.add(ticker)
    return explained


def _only(holdings: dict[str, float], tickers: set[str]) -> dict[str, float]:
    return {t: s for t, s in holdings.items() if t in tickers}


def _describe(before: dict[str, float], after: dict[str, float]) -> str:
    """``+NEW, -GONE, KEPT 54→42`` — what moved, sorted by ticker."""
    parts: list[str] = []
    for ticker in sorted(before.keys() | after.keys()):
        old, new = before.get(ticker), after.get(ticker)
        if old is None:
            parts.append(f"+{ticker}")
        elif new is None:
            parts.append(f"-{ticker}")
        elif old != new:
            parts.append(f"{ticker} {old:g}→{new:g}")
    return ", ".join(parts)


def _stamp_date(stamp: str) -> str:
    """Date portion of an ISO stamp, or ``""`` when absent or malformed.

    ``now_iso()`` writes ``2026-08-04T14:42:03+00:00``; a portfolio that
    has never run carries ``""``.
    """
    if not stamp:
        return ""
    try:
        return datetime.fromisoformat(stamp).date().isoformat()
    except ValueError:
        return ""


def check_portfolios(
    directory: Path,
    *,
    mode: str,
    as_of: date,
    last_bar: LastBar | None = None,
    previous: PreviousShares | None = None,
    ledger_dir: Path | None = None,
    journal_dir: Path | None = None,
) -> VerifyReport:
    """Assert every portfolio in *directory* was written by today's run.

    With *last_bar*, also assert every unpriced holding is flagged as
    such; without it that check is not run.

    With *previous* and *ledger_dir*, also assert that every book whose
    holdings differ from *previous* has a ledger file for *as_of*;
    without them that check is not run. With *journal_dir* as well, a
    change the day's journaled ``SPLIT`` rows account for exactly needs
    no ledger file and is reported in ``split_adjusted``.

    Raises ``KeyError`` for an unknown *mode* rather than guessing which
    timestamp to read, and ``ValueError`` when only one of *previous*
    and *ledger_dir* is given, or *journal_dir* without them.
    """
    if (previous is None) != (ledger_dir is None):
        raise ValueError("previous and ledger_dir are given together or not at all")
    if journal_dir is not None and previous is None:
        raise ValueError("journal_dir needs previous and ledger_dir")
    field_name = STAMP_FIELD[mode]
    expected = as_of.isoformat()

    rows: list[AgentState] = []
    stale: list[str] = []
    unreadable: list[str] = []
    unflagged: list[str] = []
    flagged: list[str] = []
    unledgered: list[str] = []
    split_adjusted: list[str] = []

    for path in sorted(directory.glob("*.json")):
        before: dict[str, float] = {}
        after: dict[str, float] = {}
        try:
            data = json.loads(path.read_text())
            agent = str(data["agent"])
            nav = float(data["total_nav"])
            if previous is not None:
                before = previous(path)
                after = _holdings(data.get("positions", []))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            unreadable.append(f"{path.name}: {exc}")
            continue

        stamp = str(data.get(field_name, ""))
        fresh = _stamp_date(stamp) == expected
        rows.append(
            AgentState(
                agent=agent,
                nav=nav,
                positions=len(data.get("positions", [])),
                stamp=stamp,
                fresh=fresh,
            )
        )
        if not fresh:
            stale.append(agent)

        if (
            previous is not None
            and ledger_dir is not None
            and before != after
            and not (ledger_dir / agent / f"{expected}.json").is_file()
        ):
            moved = {
                t for t in before.keys() | after.keys()
                if before.get(t) != after.get(t)
            }
            split: set[str] = set()
            if journal_dir is not None:
                journal = journal_dir / agent / f"{expected}.json"
                try:
                    split = _explained_by_splits(
                        before, after, _journaled_splits(journal)
                    )
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    unreadable.append(f"{agent} journal {journal.name}: {exc}")
            if split:
                split_adjusted.append(
                    f"{agent} ({_describe(_only(before, split), _only(after, split))})"
                )
            rest = moved - split
            if rest:
                unledgered.append(
                    f"{agent} ({_describe(_only(before, rest), _only(after, rest))})"
                )

        if last_bar is None:
            continue
        for pos in data.get("positions", []):
            ticker = str(pos.get("ticker", ""))
            bar = last_bar(ticker)
            if not unpriced_since_for(
                bar, as_of=as_of, entry_date=str(pos.get("entry_date", ""))
            ):
                continue
            flag = str(pos.get("unpriced_since", ""))
            if flag:
                flagged.append(f"{agent}:{ticker} (unpriced since {flag})")
            else:
                seen = f"no bar since {bar}" if bar is not None else "no bar at all"
                unflagged.append(f"{agent}:{ticker} ({seen})")

    return VerifyReport(
        rows=rows,
        stale=stale,
        unreadable=unreadable,
        unflagged=unflagged,
        flagged=flagged,
        unledgered=unledgered,
        split_adjusted=split_adjusted,
    )


def format_report(report: VerifyReport, *, mode: str, as_of: date) -> str:
    """Render the report as GitHub-Actions-annotated text."""
    lines = [f"portfolio state after mode={mode} run for {as_of}:"]
    for r in report.rows:
        flag = "ok  " if r.fresh else "STALE"
        lines.append(
            f"  [{flag}] {r.agent}: nav=${r.nav:,.2f} "
            f"positions={r.positions} stamp={r.stamp or '(never)'}"
        )

    for entry in report.unreadable:
        lines.append(f"::error::unreadable portfolio — {entry}")

    if not report.rows and not report.unreadable:
        lines.append(
            "::error::no portfolio files found in the state directory — "
            "the run wrote nothing"
        )

    if report.stale:
        lines.append(
            f"::error::{len(report.stale)} agent(s) did not complete this "
            f"{mode} run: {', '.join(report.stale)}. Their state predates "
            f"{as_of}, which means the scan raised before saving."
        )

    for entry in report.unflagged:
        lines.append(
            f"::error::unpriced holding not flagged — {entry}. The run "
            f"carried it at its last mark without saying so."
        )

    for entry in report.unledgered:
        lines.append(
            f"::error::holdings moved with no ledger day — {entry}. The "
            f"run changed shares but wrote no trades file for {as_of}, "
            f"so what it executed, and at what price, is not on record."
        )

    for entry in report.split_adjusted:
        lines.append(
            f"::notice::holdings restated for a stock split — {entry}. No "
            f"order was placed; the day's SPLIT journal rows account for it."
        )

    for entry in report.flagged:
        lines.append(
            f"::warning::unpriced holding — {entry}. Carried at its last "
            f"mark until settled from the filings."
        )

    if report.ok:
        lines.append(f"all {len(report.rows)} agents completed the {mode} run")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=sorted(STAMP_FIELD), required=True)
    parser.add_argument(
        "--as-of",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=None,
        help="date the run belongs to; defaults to today in UTC",
    )
    parser.add_argument(
        "--portfolios",
        type=Path,
        default=None,
        help="state directory; defaults to the resolved data root",
    )
    parser.add_argument(
        "--prices-db",
        type=Path,
        default=None,
        help="price cache to date each holding's last bar against; "
        "defaults to the resolved data root's",
    )
    parser.add_argument(
        "--trades",
        type=Path,
        default=None,
        help="trade ledger directory; defaults to the resolved data root's",
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        default=None,
        help="decision journal directory, read for the day's SPLIT rows; "
        "defaults to the resolved data root's",
    )
    parser.add_argument(
        "--baseline-ref",
        default=None,
        help="git ref holding the books as they stood before this run "
        "(in CI: HEAD). When given, a book whose holdings moved without a "
        "ledger file for the day fails the run; omitted, that check is "
        "not run",
    )
    args = parser.parse_args(argv)

    as_of = args.as_of or datetime.now(UTC).date()
    directory = args.portfolios or portfolios_dir()
    db = args.prices_db or prices_db()

    # A day with no session has nothing to verify. The runner declines
    # it on purpose — prices on a shut day are the previous close, and
    # booking them under today's date is how four 2026 holidays acquired
    # 21 trades that never happened — so every stamp is yesterday's and
    # this check would read a correct refusal as a failed run. Both read
    # the same calendar, which is what keeps them from disagreeing.
    shut = why_closed(as_of)
    if shut is not None:
        print(f"no US session on {as_of} ({shut}); nothing to verify.")
        return 0

    # The run reads and writes this cache on every pass, so its absence
    # means the run did not do what it says — and without it no holding
    # can be dated, which is not the same as every holding being priced.
    if not db.is_file():
        print(
            f"::error::no price cache at {db} — cannot check that every "
            f"holding is priced"
        )
        return 1

    previous: PreviousShares | None = None
    ledger_dir: Path | None = None
    journal_dir: Path | None = None
    if args.baseline_ref is not None:
        previous = git_baseline_reader(directory, args.baseline_ref)
        # Asked to compare and unable to is not the same as nothing
        # moved: without the baseline no share change can be seen.
        if previous is None:
            print(
                f"::error::cannot read the baseline ref {args.baseline_ref!r} "
                f"from the repository holding {directory} — cannot check "
                f"that every run that moved shares wrote a ledger day"
            )
            return 1
        ledger_dir = args.trades or trade_ledger_dir()
        journal_dir = args.decisions or decisions_dir()
    else:
        print("ledger check not run: no --baseline-ref given")

    report = check_portfolios(
        directory,
        mode=args.mode,
        as_of=as_of,
        last_bar=last_bar_reader(db),
        previous=previous,
        ledger_dir=ledger_dir,
        journal_dir=journal_dir,
    )
    print(format_report(report, mode=args.mode, as_of=as_of))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
