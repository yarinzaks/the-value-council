"""The would-be list, written on one run and executed on a later one.

§7 wires the statistical rebalance to the runs that already exist, and
buries the important half in a subordinate clause: *"execute the
statistical rebalance from a list published at least one run earlier —
the cooling-off rule holds even for the machine."*

Part 7 states the rule for people and gives its justification in one
sentence: *"Nothing is bought in the run in which it is identified, or
the run in which it is read... It costs almost nothing in expected
return and removes an entire class of error."* A mechanical sleeve that
ranks and buys in the same breath has that class of error back. A
formula producing the list does not make the list less freshly-minted,
and the discipline is worth no less for being applied to a machine.

So the monthly close recomputes the rank on paper and writes it here,
and the quarterly council executes whatever was written *before today*.
:func:`executable_list` is that comparison, and it is deliberately the
only way the rebalance gets a list — there is no path that ranks and
executes in one run, because a fallback of that shape would arrive
dressed as robustness and quietly delete the rule.

One artefact per publication date, under ``data/council/published/``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from core.logger import get_logger

logger = get_logger("agents.council.published_list")


@dataclass(frozen=True)
class PublishedList:
    """A rank computed on paper, with the date it was computed on.

    Frozen because the whole value of the artefact is that it cannot
    have been edited between publication and execution.
    """

    #: The run that wrote it. What :func:`executable_list` compares.
    published_on: date
    #: The data date the rank was computed against. Usually the same,
    #: and separate because §9.7 binds the rank to facts filed on or
    #: before it — a republication does not move the point in time.
    as_of: date
    tickers: tuple[str, ...]
    weights: dict[str, float]
    note: str = ""

    def __post_init__(self) -> None:
        if set(self.weights) != set(self.tickers):
            missing = set(self.tickers) ^ set(self.weights)
            raise ValueError(
                f"weights and tickers disagree on {sorted(missing)} — a list "
                "that cannot be sized is not a list that can be executed"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "published_on": self.published_on.isoformat(),
            "as_of": self.as_of.isoformat(),
            "tickers": list(self.tickers),
            "weights": {k: round(v, 6) for k, v in sorted(self.weights.items())},
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PublishedList:
        """Parse one artefact, raising rather than guessing.

        ``latest_published`` catches what this raises and skips the file:
        a list that cannot be read is one the rebalance must not act on,
        and half-parsing it into defaults would produce a list that is
        executable and wrong.
        """
        return cls(
            published_on=date.fromisoformat(str(data["published_on"])),
            as_of=date.fromisoformat(str(data["as_of"])),
            tickers=tuple(str(t) for t in data.get("tickers", ())),
            weights={str(k): float(v) for k, v in (data.get("weights") or {}).items()},
            note=str(data.get("note", "")),
        )


def publish(published: PublishedList, *, directory: Path) -> Path:
    """Write ``published`` under its publication date.

    Re-publishing the same date replaces the file: a monthly close that
    ran twice produced one corrected list, not two competing ones.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{published.published_on.isoformat()}.json"
    path.write_text(json.dumps(published.to_dict(), indent=1, sort_keys=True))
    logger.info(
        f"published {len(published.tickers)} name(s) for "
        f"{published.published_on}: {path.name}"
    )
    return path


def latest_published(directory: Path, *, before: date) -> PublishedList | None:
    """The most recent list published strictly before ``before``.

    Strictly, because "published at least one run earlier" is the rule
    and a list written this morning has not aged at all.

    A file that cannot be read is skipped rather than raised on. One
    corrupt artefact should cost the quarter its most recent list, not
    strand the rebalance entirely — and the loss is visible in the log.
    """
    if not directory.is_dir():
        return None

    best: PublishedList | None = None
    for path in sorted(directory.glob("*.json")):
        try:
            stamp = date.fromisoformat(path.stem)
        except ValueError:
            continue  # not a published list; something else lives here
        if stamp >= before:
            continue
        try:
            candidate = PublishedList.from_dict(json.loads(path.read_text()))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(f"skipping unreadable published list {path.name}: {exc}")
            continue
        if best is None or candidate.published_on > best.published_on:
            best = candidate
    return best


def executable_list(directory: Path, *, as_of: date) -> PublishedList | None:
    """The list the rebalance on ``as_of`` may execute, if there is one.

    ``None`` means do not rebalance. That is the correct answer when
    nothing was published in time: the alternative — rank now, execute
    now — is the exact breach the cooling-off rule forbids.
    """
    return latest_published(directory, before=as_of)
