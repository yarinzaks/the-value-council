"""Small text helpers for data that was stored shouting.

Both of this module's callers read from EDGAR, which stores names and
classifications in upper case: "SEABOARD CORP /DE/", "MOTORCYCLES,
BICYCLES & PARTS". Neither belongs on a page or in a message that way,
and the casing rule is fiddly enough to be worth one implementation
rather than two that drift.
"""

from __future__ import annotations

import re

#: Characters a new word may begin after.
#:
#: An apostrophe is deliberately absent. ``str.title`` treats every
#: non-alphabetic character as a boundary, which turns "WOMEN'S" into
#: "Women'S" — the bug this module exists to avoid. A hyphen is present,
#: because "WHOLESALE-FARM" is two words and "Wholesale-farm" reads as a
#: typo.
_WORD_START = re.compile(r"(?:^|(?<=[\s\-/(&]))([a-z])")


def titlecase(text: str) -> str:
    """Title-case ``text`` without ``str.title``'s apostrophe bug."""
    return _WORD_START.sub(lambda m: m.group(1).upper(), text.lower())


__all__ = ["titlecase"]
