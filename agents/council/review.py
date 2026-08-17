"""What a REVIEW run concluded about one holding, kept so it can be counted.

Part 7 gives the REVIEW run one job — *"re-run the thesis on a holding,
update or kill it"* — on each holding's earnings. §6's E7 then turns
those verdicts into an exit: **eight quarterly reviews with no progress
toward the thesis** and the position goes, whatever the price has done.

E7 could not fire. ``PositionState.quarterly_reviews_without_progress``
existed, defaulted to ``None``, and nothing in the live path ever
supplied it — the rule was written, tested in isolation, and unreachable
in production, because the run it counts produced no record to count.

This is that record. One file per holding per review, verdict and
reasoning kept together, so the count is an observation rather than an
inference.

Why the count is consecutive
----------------------------

E7 is a time stop on a thesis that is not progressing. A quarter of
genuine progress resets it: the position earned another eight quarters,
which is the point of holding for years rather than months. Counting
lifetime no-progress quarters instead would mean a name that stumbled
early could never recover its standing however well it later did, and
the doctrine is explicit that volatility is not loss.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from core.logger import get_logger

logger = get_logger("agents.council.review")

#: §6 E7. Eight quarters is two years of a thesis going nowhere.
TIME_STOP_QUARTERS: Final[int] = 8


class Verdict(StrEnum):
    """What the review concluded."""

    #: The thesis moved forward. Resets E7's count.
    PROGRESS = "progress"
    #: Nothing broke, but nothing advanced either. This is what E7 counts.
    NO_PROGRESS = "no_progress"
    #: The thesis is dead. Not E7's business — an immediate exit.
    KILL = "kill"


@dataclass(frozen=True)
class ReviewRecord:
    """One holding, reviewed once, with the reasoning kept.

    ``note`` is not decoration. E7 sells on a count, and a count with no
    reasoning behind it cannot be argued with later — which is the whole
    difference between a record and a tally.
    """

    ticker: str
    reviewed_on: date
    #: The reporting period this review read. Two reviews of the same
    #: period are one quarter, however many times the run fired.
    period_end: date
    verdict: Verdict
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "reviewed_on": self.reviewed_on.isoformat(),
            "period_end": self.period_end.isoformat(),
            "verdict": str(self.verdict),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReviewRecord:
        return cls(
            ticker=str(data["ticker"]).upper(),
            reviewed_on=date.fromisoformat(str(data["reviewed_on"])),
            period_end=date.fromisoformat(str(data["period_end"])),
            verdict=Verdict(str(data["verdict"])),
            note=str(data.get("note", "")),
        )


def _dir_for(ticker: str, directory: Path) -> Path:
    return directory / ticker.upper()


def record_review(record: ReviewRecord, *, directory: Path) -> Path:
    """Persist one review, keyed by the period it read.

    Keyed by ``period_end`` rather than ``reviewed_on`` so re-running a
    review — a retry, a manual dispatch — corrects that quarter instead
    of adding a second one to the count. E7 sells on eight; letting a
    retry count twice would bring the stop forward by a quarter for
    nothing.
    """
    target = _dir_for(record.ticker, directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{record.period_end.isoformat()}.json"
    path.write_text(json.dumps(record.to_dict(), indent=1, sort_keys=True))
    logger.info(
        f"{record.reviewed_on}: reviewed {record.ticker} "
        f"({record.period_end}) — {record.verdict}"
    )
    return path


def reviews_for(ticker: str, *, directory: Path) -> list[ReviewRecord]:
    """Every review of ``ticker``, oldest period first.

    An unreadable record is skipped with a warning rather than raised
    on. A corrupt file should cost one quarter's evidence, not the
    ability to evaluate the position at all — and skipping it makes the
    count smaller, which delays an exit rather than causing one.
    """
    folder = _dir_for(ticker, directory)
    if not folder.is_dir():
        return []
    out: list[ReviewRecord] = []
    for path in sorted(folder.glob("*.json")):
        try:
            out.append(ReviewRecord.from_dict(json.loads(path.read_text())))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(f"skipping unreadable review {path}: {exc}")
    return sorted(out, key=lambda r: r.period_end)


def quarters_without_progress(ticker: str, *, directory: Path) -> int:
    """Consecutive no-progress reviews, counting back from the latest.

    A KILL stops the count as surely as progress does: it is not E7's
    business. A dead thesis exits on its own rule, immediately, and
    should never be waiting out a time stop.
    """
    count = 0
    for record in reversed(reviews_for(ticker, directory=directory)):
        if record.verdict is not Verdict.NO_PROGRESS:
            break
        count += 1
    return count
