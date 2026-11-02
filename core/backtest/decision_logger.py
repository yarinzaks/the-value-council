"""Append-only per-agent decision logger.

Every agent decision (BUY / SELL / WATCH / REJECT) lands in
``data/decisions/<agent_id>/<YYYY-MM-DD>.json`` as a JSON-array file
to which new entries are appended (read → append → atomic write back).

This is the **substrate the future Strategy Builder will mine**: 12+
months of per-decision criteria_met / criteria_values lets us learn
which actually-reviewed signals correlate with realized return.

## Schema

Each decision is a :class:`Decision`:

* ``ticker`` — symbol evaluated.
* ``decision`` — one of BUY / SELL / WATCH / REJECT / HOLD / TRIM / ADD.
* ``agent`` — agent identity (e.g. ``"greenblatt"``).
* ``timestamp`` — UTC ISO-8601.
* ``criteria_met`` — list of named criteria that passed.
* ``criteria_failed`` — list of named criteria that failed.
* ``criteria_values`` — dict[str, number] of the actual numeric values
  observed (P/B = 0.42, EBIT/EV = 0.18, etc.).
* ``market_conditions`` — dict capturing market context at decision
  time (S&P 500 P/E, VIX, 10Y yield, etc.) — populated by the runner.
* ``confidence`` — 0.0-1.0 self-rated confidence.
* ``entry_price`` — price at which the decision was issued.
* ``target_price`` — optional intrinsic-value target.
* ``exit_trigger`` — natural-language description of what would close
  the position.
* ``rationale`` — optional free-text explanation.

The schema is deliberately broad — different agents (Greenblatt's
mechanical formula vs Klarman's scenario-weighted analysis) populate
different subsets, but every entry shares the same envelope so
downstream analysis is uniform.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from math import isfinite
from pathlib import Path
from typing import Any, Literal

from core.exceptions import ValueCouncilError
from core.logger import get_logger

logger = get_logger("core.backtest.decision_logger")

from core.paths import decisions_dir as _decisions_dir

DEFAULT_DECISIONS_DIR = _decisions_dir()

# BUY/SELL are the strategy's *intent* — what the doctrine selected.
# FILL/EXIT are the runner's *execution* — what the portfolio actually
# did about it, at what price. They were both logged as BUY, so every
# executed purchase appeared twice for the same ticker on the same day
# and no consumer could tell a rejected intent from a filled one.
DecisionType = Literal[
    "BUY", "SELL", "WATCH", "REJECT", "HOLD", "TRIM", "ADD", "FILL", "EXIT"
]
VALID_DECISION_TYPES: tuple[str, ...] = (
    "BUY",
    "SELL",
    "WATCH",
    "REJECT",
    "HOLD",
    "TRIM",
    "ADD",
    "FILL",
    "EXIT",
)


class DecisionLoggerError(ValueCouncilError):
    """Raised on decision logger I/O failure."""


@dataclass(frozen=True)
class Decision:
    """One agent decision with all observable context."""

    ticker: str
    decision: DecisionType
    agent: str
    timestamp: str  # ISO-8601 UTC
    criteria_met: list[str] = field(default_factory=list)
    criteria_failed: list[str] = field(default_factory=list)
    criteria_values: dict[str, float | int | str | None] = field(default_factory=dict)
    market_conditions: dict[str, float | int | str | None] = field(default_factory=dict)
    confidence: float = 0.0
    entry_price: float | None = None
    target_price: float | None = None
    exit_trigger: str | None = None
    rationale: str | None = None

    def __post_init__(self) -> None:
        if self.decision not in VALID_DECISION_TYPES:
            raise DecisionLoggerError(
                f"invalid decision {self.decision!r}; "
                f"valid: {VALID_DECISION_TYPES}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise DecisionLoggerError(
                f"confidence must be in [0, 1]; got {self.confidence}"
            )
        if not self.ticker:
            raise DecisionLoggerError("ticker must be non-empty")
        if not self.agent:
            raise DecisionLoggerError("agent must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Decision:
        return cls(
            ticker=str(d["ticker"]),
            decision=str(d["decision"]),  # type: ignore[arg-type]
            agent=str(d["agent"]),
            timestamp=str(d["timestamp"]),
            criteria_met=list(d.get("criteria_met", [])),
            criteria_failed=list(d.get("criteria_failed", [])),
            criteria_values=dict(d.get("criteria_values", {})),
            market_conditions=dict(d.get("market_conditions", {})),
            confidence=float(d.get("confidence", 0.0)),
            entry_price=_optional_float(d.get("entry_price")),
            target_price=_optional_float(d.get("target_price")),
            exit_trigger=d.get("exit_trigger"),
            rationale=d.get("rationale"),
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    """``value`` with every non-finite float replaced by ``None``.

    ``json.dumps`` writes NaN and the infinities as the bare tokens
    ``NaN``, ``Infinity`` and ``-Infinity``. Python's own loader accepts
    them, so a file written this way round-trips here and looks healthy;
    they are not JSON, and ``JSON.parse`` refuses the whole document.
    The dashboard's loader tolerates a file it cannot read by skipping
    it, so one absent metric cost a reader the entire agent-day — five
    of them on 2026-06-19, where the visible symptom was a position card
    showing six exits against the seven the journal held.

    A metric that could not be computed is absent, and ``null`` is how
    the rest of this schema already says so.
    """
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


class DecisionLogger:
    """Per-agent append-only logger writing to ``<root>/<agent>/<YYYY-MM-DD>.json``.

    File format: one JSON array per (agent, date). Append is implemented
    as read-append-rewrite under a per-file lock to avoid corruption
    when two strategies log on the same date concurrently.

    Reads are unlocked — callers should not write+read interleaved
    from multiple threads, but that pattern is rare in practice.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or DEFAULT_DECISIONS_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[Path, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------
    def path_for(self, agent: str, day: date | datetime) -> Path:
        if isinstance(day, datetime):
            day = day.date()
        agent_dir = self.root / agent
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir / f"{day.isoformat()}.json"

    def _lock_for(self, path: Path) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(path)
            if lock is None:
                lock = threading.Lock()
                self._locks[path] = lock
            return lock

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def log(self, decision: Decision) -> None:
        """Append one decision to the agent's daily file."""
        ts = decision.timestamp
        try:
            day = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise DecisionLoggerError(
                f"invalid timestamp {ts!r}: {exc}"
            ) from exc
        path = self.path_for(decision.agent, day)
        lock = self._lock_for(path)
        with lock:
            entries: list[dict[str, Any]] = []
            if path.exists():
                try:
                    raw = json.loads(path.read_text())
                    if isinstance(raw, list):
                        entries = raw
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        f"corrupt decisions file {path}; starting fresh: {exc}"
                    )
            # A day gets more than one run — the US open and the US
            # close both scan the whole book, and a retry or a manual
            # dispatch adds more. Each run re-emits the strategy's
            # intent for every held name, so the file grew a second
            # byte-identical copy of every row and the journal showed
            # each ticker twice.
            #
            # Equality is on the whole payload, so only a re-emission
            # of the same reading is dropped: a changed price, a
            # changed confidence or the execution that follows an
            # intent all differ somewhere and are kept. Rows already
            # duplicated on disk stay — this stops the logger adding
            # more, it does not edit an audit trail it did not write.
            row = _json_safe(decision.to_dict())
            if row in [_json_safe(e) for e in entries]:
                logger.debug(
                    f"skipped duplicate {decision.agent}.{decision.decision} "
                    f"{decision.ticker} → {path.name}"
                )
                return
            entries.append(row)
            tmp = path.with_suffix(".tmp")
            # allow_nan=False is the guard, not the fix: _json_safe has
            # already removed the non-finite values, and this makes a
            # path that grows a new one fail here rather than write a
            # file no browser can read.
            tmp.write_text(
                json.dumps(
                    _json_safe(entries),
                    indent=2,
                    default=str,
                    allow_nan=False,
                )
            )
            tmp.replace(path)
        logger.debug(
            f"logged {decision.agent}.{decision.decision} {decision.ticker} → {path.name}"
        )

    def log_many(self, decisions: list[Decision]) -> None:
        """Convenience: log a batch (per-decision write — no batching across files)."""
        for d in decisions:
            self.log(d)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def read_day(self, agent: str, day: date | datetime) -> list[Decision]:
        path = self.path_for(agent, day)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise DecisionLoggerError(f"failed to read {path}: {exc}") from exc
        if not isinstance(raw, list):
            return []
        return [Decision.from_dict(d) for d in raw]

    def read_all_for_agent(self, agent: str) -> list[Decision]:
        """All decisions ever logged for ``agent``, chronological."""
        agent_dir = self.root / agent
        if not agent_dir.exists():
            return []
        results: list[Decision] = []
        for path in sorted(agent_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text())
                if isinstance(raw, list):
                    for d in raw:
                        results.append(Decision.from_dict(d))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"skipping corrupt {path}: {exc}")
        return results

    def agents(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {"agents": {}, "total_decisions": 0}
        for agent in self.agents():
            decisions = self.read_all_for_agent(agent)
            by_type: dict[str, int] = {}
            for d in decisions:
                by_type[d.decision] = by_type.get(d.decision, 0) + 1
            out["agents"][agent] = {
                "total": len(decisions),
                "by_decision": by_type,
            }
            out["total_decisions"] += len(decisions)
        return out


# ----------------------------------------------------------------------
# Helpers for strategies
# ----------------------------------------------------------------------
def make_decision(
    *,
    ticker: str,
    decision: DecisionType,
    agent: str,
    criteria_met: list[str] | None = None,
    criteria_failed: list[str] | None = None,
    criteria_values: dict[str, Any] | None = None,
    market_conditions: dict[str, Any] | None = None,
    confidence: float = 0.0,
    entry_price: float | None = None,
    target_price: float | None = None,
    exit_trigger: str | None = None,
    rationale: str | None = None,
    timestamp: str | None = None,
) -> Decision:
    """Build a :class:`Decision` with sensible defaults and a UTC timestamp."""
    return Decision(
        ticker=ticker.upper(),
        decision=decision,
        agent=agent,
        timestamp=timestamp or datetime.now(UTC).isoformat(),
        criteria_met=list(criteria_met or []),
        criteria_failed=list(criteria_failed or []),
        criteria_values={k: v for k, v in (criteria_values or {}).items()},
        market_conditions={k: v for k, v in (market_conditions or {}).items()},
        confidence=confidence,
        entry_price=entry_price,
        target_price=target_price,
        exit_trigger=exit_trigger,
        rationale=rationale,
    )


__all__ = [
    "DEFAULT_DECISIONS_DIR",
    "VALID_DECISION_TYPES",
    "Decision",
    "DecisionLogger",
    "DecisionLoggerError",
    "DecisionType",
    "make_decision",
]
