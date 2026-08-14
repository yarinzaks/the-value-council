"""The two runs that need no model: heartbeat and close.

Part 7 of the doctrine makes what the agent *may do* depend on which run
it is in, and that table is what stops a schedule running many times a
day from becoming a high-turnover strategy. These two are the deterministic
half of it:

    heartbeat  check risk, drawdown, kill triggers, breaking filings
               — may not open, add to, or resize anything
    close      mark, read the regime dial, walk the hunting grounds,
               queue candidates — may not open a position

Neither may trade. That is not a limitation of this implementation, it is
the rule: nothing is bought in the run in which it is identified.

The reading and council runs are the other half and they need a model to
read filings and argue. They are not here.

Honest about the gaps
~~~~~~~~~~~~~~~~~~~~~

Where something cannot be computed it says so rather than defaulting to
clear. A kill criterion is free text written by whoever set the thesis —
"gross margin below 65% for two quarters" is not evaluable generically,
so it is reported as recorded and marked for review. Silence would read
as "not triggered", which is exactly the failure this run exists to
prevent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final

from agents.council import events as events_mod
from agents.council import limits as limits_mod
from agents.council.journal import Journal, Outcome
from agents.council.regime import Regime, read_regime
from core.logger import get_logger

logger = get_logger("agents.council.runs")

#: What the doctrine calls the agent's own book on the dashboard.
AGENT_SLUG: Final[str] = "the_council"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Book:
    """The state a run reads. Deliberately not a live portfolio object.

    Keeping this a plain snapshot means a run can be tested, replayed and
    reasoned about without a broker, a database or a clock.
    """

    nav: float
    cash: float
    peak_nav: float
    positions: Sequence[Mapping[str, Any]] = field(default_factory=list)
    #: Median daily dollar volume per ticker, where measured.
    adv: Mapping[str, float] = field(default_factory=dict)
    #: Correlation-cluster label per ticker, where assigned.
    clusters: Mapping[str, str] = field(default_factory=dict)

    @property
    def tickers(self) -> list[str]:
        return [str(p["ticker"]) for p in self.positions]

    @property
    def cash_weight(self) -> float:
        return self.cash / self.nav if self.nav > 0 else 0.0


def heartbeat(
    book: Book,
    *,
    journal: Journal | None = None,
    since: date | None = None,
    fetch=events_mod.fetch_submissions,
    resolve_cik=events_mod.cik_for,
) -> dict[str, Any]:
    """Risk, drawdown, kill triggers, breaking filings. Opens nothing."""
    positions = limits_mod.positions_from_portfolio(
        book.positions, nav=book.nav, adv=book.adv, clusters=book.clusters
    )
    checks = limits_mod.check_all(
        positions,
        cash_weight=book.cash_weight,
        nav=book.nav,
        peak_nav=book.peak_nav,
    )
    breached = limits_mod.breaches(checks)
    unknown = limits_mod.unknowns(checks)

    drawdown_check = next(
        (c for c in checks if c.limit == "drawdown_circuit_breaker"), None
    )
    drawdown = drawdown_check.observed if drawdown_check else None
    circuit_breaker = bool(
        drawdown_check and drawdown_check.state is limits_mod.LimitState.BREACH
    )

    open_theses = journal.open_entries() if journal else []
    kill_criteria = [
        {
            "ticker": t.ticker,
            "criterion": k.condition,
            "measured_in": k.measured_in,
            "action": k.action,
            # Free text set at entry; not generically evaluable here.
            "state": "NOT_EVALUATED",
        }
        for t in open_theses
        for k in t.kill_criteria
    ]

    flagged = events_mod.scan(
        book.tickers, since=since, fetch=fetch, resolve_cik=resolve_cik
    )

    all_clear = not breached and not flagged and not unknown
    result = {
        "run": "heartbeat",
        "at": _now(),
        "limits": [c.to_dict() for c in checks],
        "breaches": [c.to_dict() for c in breached],
        "unknown_limits": [c.to_dict() for c in unknown],
        "drawdown_from_peak": drawdown,
        "circuit_breaker": circuit_breaker,
        "kill_criteria": kill_criteria,
        "filings_flagged": [e.to_dict() for e in flagged],
        # This run may not trade. A forced exit is proposed, never taken.
        "forced_exits_proposed": [
            c.to_dict() for c in breached if c.forces_action
        ],
        "all_clear": all_clear,
    }
    logger.info(
        "heartbeat: "
        + (
            "all clear"
            if all_clear
            else f"{len(breached)} breach(es), {len(flagged)} filing(s), "
            f"{len(unknown)} unmeasurable"
        )
    )
    return result


def close(
    book: Book,
    *,
    journal: Journal | None = None,
    regime: Regime | None = None,
    since: date | None = None,
    fetch=events_mod.fetch_submissions,
    resolve_cik=events_mod.cik_for,
) -> dict[str, Any]:
    """Mark, read the dial, walk the grounds, queue. Opens nothing."""
    dial = regime if regime is not None else read_regime()
    flagged = events_mod.scan(
        book.tickers, since=since, fetch=fetch, resolve_cik=resolve_cik
    )

    # Only the grounds that are observable without reading a filing are
    # walked here. The rest — hidden assets, unit-economics inflections —
    # are a reading assignment by construction, and claiming to have
    # checked them would be the dishonest kind of completeness.
    grounds: list[dict[str, Any]] = []
    for event in flagged:
        if event.severity is events_mod.Severity.CRITICAL:
            grounds.append(
                {
                    "ground": "holding_event",
                    "ticker": event.ticker,
                    "note": f"{event.code}: {event.meaning}",
                }
            )

    queued = sorted({g["ticker"] for g in grounds})

    punch = (
        {
            "used": journal.punches_used(),
            "remaining": journal.punches_remaining(),
        }
        if journal
        else None
    )

    open_count = (
        sum(1 for t in journal.entries() if t.outcome is Outcome.OPEN)
        if journal
        else 0
    )

    result = {
        "run": "close",
        "at": _now(),
        "nav": book.nav,
        "cash_weight": book.cash_weight,
        "positions": len(book.positions),
        "regime": dial.to_dict(),
        "grounds_triggered": grounds,
        "queued_for_reading": queued,
        "filings_flagged": [e.to_dict() for e in flagged],
        "punch_card": punch,
        "open_theses": open_count,
    }
    logger.info(
        f"close: regime {dial.risk_on_count}/4 risk-on, "
        f"{len(grounds)} ground(s) triggered, {len(queued)} queued"
    )
    return result


def write_result(result: Mapping[str, Any], *, directory) -> None:
    """Persist one run's record, one file per run per day."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = str(result.get("at", _now()))[:10]
    path = directory / f"{stamp}_{result['run']}.json"
    path.write_text(json.dumps(result, indent=1, sort_keys=True))


__all__ = ["AGENT_SLUG", "Book", "close", "heartbeat", "write_result"]
