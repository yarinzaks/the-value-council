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

Usage::

    python -m scripts.verify_run_state --mode open
    python -m scripts.verify_run_state --mode close --as-of 2026-08-05
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from core.paths import portfolios_dir

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

    @property
    def ok(self) -> bool:
        """True only when at least one portfolio was found and every one
        of them is both readable and freshly stamped."""
        return bool(self.rows) and not self.stale and not self.unreadable


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
) -> VerifyReport:
    """Assert every portfolio in *directory* was written by today's run.

    Raises ``KeyError`` for an unknown *mode* rather than guessing which
    timestamp to read.
    """
    field_name = STAMP_FIELD[mode]
    expected = as_of.isoformat()

    rows: list[AgentState] = []
    stale: list[str] = []
    unreadable: list[str] = []

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

    return VerifyReport(rows=rows, stale=stale, unreadable=unreadable)


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
    args = parser.parse_args(argv)

    as_of = args.as_of or datetime.now(UTC).date()
    directory = args.portfolios or portfolios_dir()

    report = check_portfolios(directory, mode=args.mode, as_of=as_of)
    print(format_report(report, mode=args.mode, as_of=as_of))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
