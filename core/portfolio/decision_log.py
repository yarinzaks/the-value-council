"""Append-only decision log.

Every buy/sell across every agent gets one line in
``data/decisions.jsonl``. JSONL (one JSON object per line) is chosen
over a single JSON array so we can append without rewriting the whole
file and tail it during a long-running session.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class DecisionLog:
    """Thread-safe append-only writer for decision events.

    Each :meth:`append` call adds a ``timestamp`` field if not present
    and writes one JSON line, fsync-flushed so crashes do not lose
    decisions.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        """Append ``entry`` to the log as one JSON line."""
        record = dict(entry)
        record.setdefault("timestamp", datetime.now(UTC).isoformat())
        line = json.dumps(record, default=str, sort_keys=True)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    def read_all(self) -> list[dict[str, Any]]:
        """Return every record in chronological (file) order."""
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # Skip malformed lines rather than crash the reader.
                    continue
        return out


__all__ = ["DecisionLog"]
