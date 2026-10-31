"""The one file that holds the Hebrew, and the one function that reads it.

Why the table is data and not code
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The same strings are rendered twice: the notifier puts a criterion in a
Telegram message, and the dashboard shows it on the journal and brain
pages. Those are Python and TypeScript, and the two tables had drifted
— 26 of the 50 templates the decision journal holds were still arriving
in English on the dashboard while the notifier translated them, nine
months after the first translation was written.

So the table is ``data_bundled/hebrew.json``, beside the other
committed bundles, and both languages read it. Edited there, not in
either reader. A phrase translated in two places is a phrase that will
disagree with itself.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TypedDict


class HebrewBundle(TypedDict):
    """The shape of ``data_bundled/hebrew.json``."""

    #: Prose that is only in the file, never read.
    _comment: str
    #: English phrase → Hebrew, applied as substring replacement.
    terms: dict[str, str]
    #: ``[regex, replacement]`` pairs, for a Hebrew phrase that has to
    #: move a number rather than sit beside it.
    patterns: list[list[str]]
    #: Four-digit SIC code → Hebrew.
    industry_by_code: dict[str, str]
    #: Two-digit SIC major group → Hebrew.
    industry_by_group: dict[str, str]


BUNDLE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data_bundled" / "hebrew.json"
)


@lru_cache(maxsize=1)
def bundle() -> HebrewBundle:
    """The table, read once per process.

    No fallback. An unreadable table would otherwise mean a message
    quietly rendered in English, which is the defect this file exists to
    end — better a loud failure at import than a silent regression in
    every notification.
    """
    raw = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    for key in ("terms", "patterns", "industry_by_code", "industry_by_group"):
        if key not in raw:
            raise ValueError(f"{BUNDLE_PATH} is missing '{key}'")
    return HebrewBundle(
        _comment=str(raw.get("_comment", "")),
        terms={str(k): str(v) for k, v in raw["terms"].items()},
        patterns=[[str(p), str(r)] for p, r in raw["patterns"]],
        industry_by_code={str(k): str(v) for k, v in raw["industry_by_code"].items()},
        industry_by_group={str(k): str(v) for k, v in raw["industry_by_group"].items()},
    )


__all__ = ["BUNDLE_PATH", "HebrewBundle", "bundle"]
