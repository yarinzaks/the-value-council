"""What the analyst layer said, kept whole.

Why this exists
~~~~~~~~~~~~~~~

The decision journal records an agent's conclusion and carries three
fields of the memo behind it — the confidence, the thesis and the
reverse triggers. Everything else the model produced is computed and
dropped: Fisher's fifteen point-by-point verdicts, his tier, his
integrity check, every agent's list of concerns.

That is tolerable while the layer is switched off and intolerable the
moment it is not. An analyst whose reasoning is discarded is an
analyst nobody can audit, and "it is working" is not something a
reader should have to take on trust.

So every memo lands here in full, next to the model that produced it
and what the call cost, in the same per-agent-per-day shape the
decision journal uses.

What is not here
~~~~~~~~~~~~~~~~

The prompt text. It is the agent's playbook — thirty-odd kilobytes,
identical across every call in a run, and already versioned in git —
plus the stock figures, which the journal and the snapshots already
hold. Storing it per call would multiply this directory by an order of
magnitude to re-record what the repository can reconstruct. The
prompt's length and digest are kept instead, so a record can still be
tied to the exact text that produced it.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from core.logger import get_logger
from core.paths import memos_dir

logger = get_logger("core.llm.memo_log")

#: Which agent a memo class belongs to.
#:
#: The analyzers do not know their own slug — they are handed a client
#: and a playbook — and the strategy that does know is not in the call
#: path. So the memo type is the identifier, and a test holds this
#: mapping complete against the analyzer modules so a sixth memo class
#: cannot be added without a home.
#:
#: Callers may pass an agent explicitly, which wins over this.
AGENT_BY_SCHEMA: Mapping[str, str] = {
    "FisherMemo": "philip_fisher",
    "MarksMemo": "howard_marks",
    "KlarmanMemo": "seth_klarman",
    "BuffettMemo": "warren_buffett",
    "LynchMemo": "peter_lynch",
    # The generic memo belongs to no book — it is what the connectivity
    # check asks for, and it must not land in an agent's history.
    "InvestmentMemo": "connectivity_probe",
}

#: The agent becomes a directory name, so it is validated rather than
#: trusted however it arrived.
_SLUG = re.compile(r"\A[a-z0-9_]{1,64}\Z")


class MemoLogError(Exception):
    """A memo could not be written or read."""


@dataclass(frozen=True)
class MemoRecord:
    """One call to the analyst layer, as it happened."""

    agent: str
    ticker: str
    schema: str
    timestamp: str
    model: str
    memo: dict[str, Any] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    prompt_chars: int | None = None
    prompt_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> MemoRecord:
        known = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)  # type: ignore[arg-type]  # keys are the field names


def digest(text: str) -> str:
    """A prompt's identity, without the prompt."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def agent_for(schema_name: str) -> str:
    """The book a memo class belongs to, or the class name itself.

    Falling back to the class name rather than raising is deliberate: a
    new memo type should still have its reasoning recorded somewhere
    findable, and losing the audit trail is worse than filing it under
    an ugly name.
    """
    mapped = AGENT_BY_SCHEMA.get(schema_name)
    if mapped:
        return mapped
    slug = re.sub(r"(?<!^)(?=[A-Z])", "_", schema_name).lower()
    logger.warning(
        f"no agent mapped for memo class {schema_name}; filing under {slug!r}"
    )
    return slug


class MemoLog:
    """Append-only per-agent memo store, one file per day."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else memos_dir()
        self._locks: dict[Path, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def path_for(self, agent: str, day: date) -> Path:
        if not _SLUG.match(agent):
            raise MemoLogError(f"not an agent slug: {agent!r}")
        directory = self.root / agent
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{day.isoformat()}.json"

    def _lock_for(self, path: Path) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(path)
            if lock is None:
                lock = threading.Lock()
                self._locks[path] = lock
            return lock

    def log(self, record: MemoRecord) -> None:
        """Append one record. Read, append, write to a temp, rename."""
        try:
            day = datetime.fromisoformat(
                record.timestamp.replace("Z", "+00:00")
            ).date()
        except ValueError as exc:
            raise MemoLogError(
                f"invalid timestamp {record.timestamp!r}: {exc}"
            ) from exc

        path = self.path_for(record.agent, day)
        with self._lock_for(path):
            entries: list[dict[str, Any]] = []
            if path.exists():
                try:
                    raw = json.loads(path.read_text())
                    if isinstance(raw, list):
                        entries = raw
                except (OSError, json.JSONDecodeError) as exc:
                    # Same choice the decision journal makes: a corrupt
                    # file must not stop today's record being written.
                    logger.warning(
                        f"corrupt memo file {path}; starting fresh: {exc}"
                    )
            entries.append(record.to_dict())
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entries, indent=2, default=str))
            tmp.replace(path)
        logger.debug(f"recorded {record.agent} memo for {record.ticker} → {path.name}")

    def read_day(self, agent: str, day: date) -> list[MemoRecord]:
        path = self.path_for(agent, day)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise MemoLogError(f"unreadable memo file {path}: {exc}") from exc
        if not isinstance(raw, list):
            raise MemoLogError(f"memo file is not a list: {path}")
        return [MemoRecord.from_dict(d) for d in raw if isinstance(d, dict)]

    def read_all_for_agent(self, agent: str) -> list[MemoRecord]:
        """Every record for one agent, oldest file first."""
        directory = self.root / agent
        if not directory.exists():
            return []
        out: list[MemoRecord] = []
        for path in sorted(directory.glob("*.json")):
            try:
                day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            out.extend(self.read_day(agent, day))
        return out

    def agents(self) -> list[str]:
        """Which books have any recorded reasoning at all."""
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "AGENT_BY_SCHEMA",
    "MemoLog",
    "MemoLogError",
    "MemoRecord",
    "agent_for",
    "digest",
    "now_utc_iso",
]
