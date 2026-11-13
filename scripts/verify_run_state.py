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

Usage::

    python -m scripts.verify_run_state --mode open
    python -m scripts.verify_run_state --mode close --as-of 2026-08-05
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from core.live.portfolio import unpriced_since_for
from core.market_calendar import why_closed
from core.paths import portfolios_dir, prices_db

#: Newest cached bar for a ticker, or ``None`` when the cache has none.
LastBar = Callable[[str], date | None]

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

    @property
    def ok(self) -> bool:
        """True only when at least one portfolio was found, every one of
        them is readable and freshly stamped, and no unpriced holding
        is left unflagged."""
        return (
            bool(self.rows)
            and not self.stale
            and not self.unreadable
            and not self.unflagged
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
) -> VerifyReport:
    """Assert every portfolio in *directory* was written by today's run.

    With *last_bar*, also assert every unpriced holding is flagged as
    such; without it that check is not run.

    Raises ``KeyError`` for an unknown *mode* rather than guessing which
    timestamp to read.
    """
    field_name = STAMP_FIELD[mode]
    expected = as_of.isoformat()

    rows: list[AgentState] = []
    stale: list[str] = []
    unreadable: list[str] = []
    unflagged: list[str] = []
    flagged: list[str] = []

    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            agent = str(data["agent"])
            nav = float(data["total_nav"])
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

    report = check_portfolios(
        directory, mode=args.mode, as_of=as_of, last_bar=last_bar_reader(db)
    )
    print(format_report(report, mode=args.mode, as_of=as_of))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
