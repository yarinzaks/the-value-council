"""The journal: every decision written before its outcome is known.

Why this is the load-bearing part
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Decision quality and outcome quality are different things and nothing
else can separate them. A good decision that lost money is still good. A
lucky win from a sloppy process is the more damaging of the two, because
the lesson feels earned.

That separation is only possible if the reasoning was recorded *before*
the result existed. Written afterwards, every decision was obviously
right or obviously wrong, and the record teaches nothing.

Two rules are enforced by construction rather than by discipline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**A thesis with no kill criteria is not a thesis.** It is an opinion with
formatting. ``Thesis`` will not construct without three of them, so
recording one is impossible rather than discouraged.

**The punch card is derived, never stored.** Twenty Understanding
positions for the agent's lifetime, counted from the entries themselves.
A separate counter could disagree with the journal, and then one of them
would be wrong with no way to tell which.

Calibration
~~~~~~~~~~~

Brier score and a reliability curve over resolved entries. If the agent
says 70% and is right 55% of the time, the measured shrinkage is applied
to future probabilities before they reach any sizing decision. It will be
overconfident — everything is — and this is the only thing that says by
how much.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Final

from core.logger import get_logger
from core.paths import DATA_ROOT

logger = get_logger("agents.council.journal")

#: Understanding positions available for the agent's entire lifetime.
#: Not per year. Scarcity is the only mechanism that reliably makes a
#: decision-maker selective.
PUNCH_CARD_TOTAL: Final[int] = 20

#: A thesis needs this many falsifiable kill criteria to exist at all.
REQUIRED_KILL_CRITERIA: Final[int] = 3

JOURNAL_DIR: Final[Path] = DATA_ROOT / "council" / "journal"


class Classification(StrEnum):
    UNDERSTANDING = "understanding"
    STATISTICAL = "statistical"


class Outcome(StrEnum):
    OPEN = "open"
    RIGHT = "right"
    WRONG = "wrong"


class JournalError(Exception):
    """A record that would violate the journal's own rules."""


@dataclass(frozen=True)
class KillCriterion:
    """One falsifiable condition, written before entry."""

    condition: str
    measured_in: str
    action: str

    def __post_init__(self) -> None:
        if not self.condition.strip():
            raise JournalError("a kill criterion needs a condition")
        if not self.action.strip():
            raise JournalError(f"'{self.condition}' has no action")


@dataclass(frozen=True)
class Thesis:
    """One decision, as it was understood at the time it was made."""

    ticker: str
    opened: date
    classification: Classification
    #: The one-sentence falsifiable claim.
    claim: str
    #: What structurally limits the downside. Part 2's test.
    structural_floor: str
    #: What is believed that is not in the price.
    second_order_belief: str
    #: Stated probability the claim proves right, before shrinkage.
    probability: float
    #: Weight of the book at entry.
    size: float
    kill_criteria: tuple[KillCriterion, ...]
    #: Which seats dissented, and on what.
    dissent: tuple[str, ...] = ()
    outcome: Outcome = Outcome.OPEN
    resolved: date | None = None
    #: Written when the entry resolves, never edited before.
    outcome_note: str = ""

    def __post_init__(self) -> None:
        if len(self.kill_criteria) < REQUIRED_KILL_CRITERIA:
            raise JournalError(
                f"{self.ticker}: {len(self.kill_criteria)} kill criteria, "
                f"{REQUIRED_KILL_CRITERIA} required. A thesis without them is "
                "an opinion with formatting."
            )
        if not 0.0 <= self.probability <= 1.0:
            raise JournalError(
                f"{self.ticker}: probability {self.probability} is not in [0, 1]"
            )
        if not self.structural_floor.strip():
            raise JournalError(
                f"{self.ticker}: no structural floor named. Part 2 makes the "
                "position size zero."
            )
        if self.outcome is not Outcome.OPEN and self.resolved is None:
            raise JournalError(f"{self.ticker}: resolved outcome with no date")

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["classification"] = str(self.classification)
        d["outcome"] = str(self.outcome)
        d["opened"] = self.opened.isoformat()
        d["resolved"] = self.resolved.isoformat() if self.resolved else None
        d["kill_criteria"] = [asdict(k) for k in self.kill_criteria]
        d["dissent"] = list(self.dissent)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Thesis:
        return cls(
            ticker=str(d["ticker"]),
            opened=date.fromisoformat(str(d["opened"])),
            classification=Classification(d["classification"]),
            claim=str(d["claim"]),
            structural_floor=str(d["structural_floor"]),
            second_order_belief=str(d.get("second_order_belief", "")),
            probability=float(d["probability"]),
            size=float(d["size"]),
            kill_criteria=tuple(
                KillCriterion(**k) for k in d.get("kill_criteria", [])
            ),
            dissent=tuple(d.get("dissent", [])),
            outcome=Outcome(d.get("outcome", "open")),
            resolved=(
                date.fromisoformat(str(d["resolved"])) if d.get("resolved") else None
            ),
            outcome_note=str(d.get("outcome_note", "")),
        )


@dataclass
class Journal:
    """Append-only store of theses, on disk as one JSON file per entry."""

    directory: Path = field(default_factory=lambda: JOURNAL_DIR)

    def __post_init__(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    # -- reading -------------------------------------------------------
    def entries(self) -> list[Thesis]:
        out: list[Thesis] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                out.append(Thesis.from_dict(json.loads(path.read_text())))
            except (OSError, ValueError, KeyError, JournalError) as exc:
                # A corrupt entry is surfaced, never skipped silently — a
                # journal that quietly drops records is worse than none.
                logger.error(f"unreadable journal entry {path.name}: {exc}")
        return out

    def open_entries(self) -> list[Thesis]:
        return [t for t in self.entries() if t.outcome is Outcome.OPEN]

    # -- writing -------------------------------------------------------
    def _path_for(self, thesis: Thesis) -> Path:
        return self.directory / f"{thesis.opened.isoformat()}_{thesis.ticker}.json"

    def record(self, thesis: Thesis) -> Path:
        """Write a new thesis. Refuses to overwrite an existing entry.

        Append-only is the whole point: an entry that can be edited after
        the outcome is known cannot be used to judge the decision.
        """
        path = self._path_for(thesis)
        if path.exists():
            raise JournalError(
                f"{path.name} already exists. The journal is append-only — "
                "resolve the entry instead of rewriting it."
            )
        if thesis.classification is Classification.UNDERSTANDING:
            remaining = self.punches_remaining()
            if remaining <= 0:
                raise JournalError(
                    "the punch card is spent: 20 Understanding positions have "
                    "been used and there is no 21st."
                )
        path.write_text(json.dumps(thesis.to_dict(), indent=1, sort_keys=True))
        logger.info(f"recorded {thesis.classification} thesis for {thesis.ticker}")
        return path

    def resolve(
        self, ticker: str, opened: date, *, outcome: Outcome, when: date, note: str = ""
    ) -> Thesis:
        """Close out an entry. The only edit the journal permits."""
        path = self.directory / f"{opened.isoformat()}_{ticker}.json"
        if not path.exists():
            raise JournalError(f"no entry for {ticker} opened {opened}")
        current = Thesis.from_dict(json.loads(path.read_text()))
        if current.outcome is not Outcome.OPEN:
            raise JournalError(f"{ticker} was already resolved as {current.outcome}")
        resolved = Thesis(
            **{
                **current.to_dict(),
                "opened": current.opened,
                "classification": current.classification,
                "kill_criteria": current.kill_criteria,
                "dissent": current.dissent,
                "outcome": outcome,
                "resolved": when,
                "outcome_note": note,
            }
        )
        path.write_text(json.dumps(resolved.to_dict(), indent=1, sort_keys=True))
        return resolved

    # -- the punch card ------------------------------------------------
    def punches_used(self) -> int:
        """Derived from the entries, never stored separately."""
        return sum(
            1
            for t in self.entries()
            if t.classification is Classification.UNDERSTANDING
        )

    def punches_remaining(self) -> int:
        return max(0, PUNCH_CARD_TOTAL - self.punches_used())


# ----------------------------------------------------------------------
# Calibration
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Calibration:
    """How well stated probabilities matched what happened."""

    resolved: int
    brier: float | None
    #: (bucket_low, bucket_high, stated_mean, actual_rate, count)
    buckets: tuple[tuple[float, float, float, float, int], ...]
    #: Multiply future probabilities' distance from 0.5 by this.
    shrinkage: float

    def to_dict(self) -> dict[str, object]:
        return {
            "resolved": self.resolved,
            "brier": self.brier,
            "shrinkage": self.shrinkage,
            "buckets": [
                {
                    "from": lo,
                    "to": hi,
                    "stated_mean": stated,
                    "actual_rate": actual,
                    "count": n,
                }
                for lo, hi, stated, actual, n in self.buckets
            ],
        }


def calibrate(entries: Iterable[Thesis], *, buckets: int = 10) -> Calibration:
    """Brier score and a reliability curve over resolved entries.

    Shrinkage is the ratio of realised skill to claimed skill, measured
    as distance from 0.5. An agent that says 70% and is right 55% of the
    time claimed 0.20 of edge and delivered 0.05, so its future
    probabilities are pulled three quarters of the way back to a coin
    flip. Clamped to [0, 1]: no amount of past accuracy licenses
    *inflating* a stated probability.
    """
    resolved = [t for t in entries if t.outcome is not Outcome.OPEN]
    if not resolved:
        return Calibration(resolved=0, brier=None, buckets=(), shrinkage=1.0)

    outcomes = [(t.probability, 1.0 if t.outcome is Outcome.RIGHT else 0.0)
                for t in resolved]
    brier = sum((p - o) ** 2 for p, o in outcomes) / len(outcomes)

    width = 1.0 / buckets
    curve: list[tuple[float, float, float, float, int]] = []
    for i in range(buckets):
        lo, hi = i * width, (i + 1) * width
        inside = [
            (p, o) for p, o in outcomes if (lo <= p < hi or (i == buckets - 1 and p == hi))
        ]
        if not inside:
            continue
        curve.append(
            (
                lo,
                hi,
                sum(p for p, _ in inside) / len(inside),
                sum(o for _, o in inside) / len(inside),
                len(inside),
            )
        )

    claimed = sum(abs(p - 0.5) for p, _ in outcomes) / len(outcomes)
    realised = sum(abs(o - 0.5) if (p - 0.5) * (o - 0.5) >= 0 else -abs(o - 0.5)
                   for p, o in outcomes) / len(outcomes)
    shrinkage = 1.0 if claimed == 0 else max(0.0, min(1.0, realised / claimed))

    return Calibration(
        resolved=len(resolved),
        brier=brier,
        buckets=tuple(curve),
        shrinkage=shrinkage,
    )


def shrink(probability: float, calibration: Calibration) -> float:
    """Pull a stated probability toward 0.5 by the measured amount."""
    return 0.5 + (probability - 0.5) * calibration.shrinkage


def summary(journal: Journal) -> dict[str, object]:
    """What the dashboard shows: the punch card and the calibration."""
    entries = journal.entries()
    cal = calibrate(entries)
    return {
        "punch_card": {
            "total": PUNCH_CARD_TOTAL,
            "used": journal.punches_used(),
            "remaining": journal.punches_remaining(),
        },
        "entries": len(entries),
        "open": sum(1 for t in entries if t.outcome is Outcome.OPEN),
        "calibration": cal.to_dict(),
    }


__all__ = [
    "JOURNAL_DIR",
    "PUNCH_CARD_TOTAL",
    "REQUIRED_KILL_CRITERIA",
    "Calibration",
    "Classification",
    "Journal",
    "JournalError",
    "KillCriterion",
    "Outcome",
    "Thesis",
    "calibrate",
    "shrink",
    "summary",
]
