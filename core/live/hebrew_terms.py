"""The agents' English criteria, in Hebrew, for the notification.

Why a translation table and not a second field
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each agent records what a company passed in its own English —
``"current ratio=2.10 (≥ 2.0)"``, ``"positive trailing net income"`` —
and that string is the audit trail. The notification is Hebrew, so the
reader was getting a Hebrew message with its reasoning in English:
exactly the part they read most closely, in the language they read
least comfortably.

The alternative was a Hebrew field beside every criterion, written at
each of the agents. That is one claim in two places, in twelve
strategies, and the two would disagree within a month — which is the
reason :func:`core.live.notify.format_action` reads the journal rather
than restating it.

So the translation happens once, here, at the edge where the message is
built. The journal keeps its English and stays the audit trail.

What is deliberately *not* translated
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ratio abbreviations. P/E, P/B, D/E, ROE, EPS, FCF, PEG and the rest
are what a Hebrew-speaking investor calls them — Hebrew financial press
and every Israeli broker's screen use the Latin forms. Rendering them
as "מכפיל הון" would be translating past the reader rather than for
them.

Numbers are never touched. The table replaces words around them.

Coverage
~~~~~~~~

Keyed on the fifty templates the journal actually contains, extracted
from every decision file rather than guessed at. A phrase with no entry
passes through in English: a new criterion from a new strategy arrives
readable rather than mangled, and the test that walks the live journal
says how much is still untranslated.

Order matters. The phrases are applied longest-first so that
``"yield=12.0% (top quintile)"`` is not matched by the shorter
``"yield="`` and left with an English tail.
"""

from __future__ import annotations

import re

from core.live.hebrew_bundle import bundle

#: English phrase → Hebrew. Applied as substring replacement,
#: longest key first, so a longer phrase cannot be pre-empted by a
#: prefix of it.
TERMS: dict[str, str] = dict(bundle()["terms"])

#: Patterns whose Hebrew has to move a number, so a substring swap
#: cannot do it. ``5yr avg FCF $217,127,200`` reads in Hebrew as "FCF
#: ממוצע ל-5 שנים", with the 5 before the word rather than after it.
PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), replacement)
    for pattern, replacement in bundle()["patterns"]
)


_LONGEST_FIRST: tuple[tuple[str, str], ...] = tuple(
    sorted(TERMS.items(), key=lambda kv: -len(kv[0]))
)


def translate(text: str) -> str:
    """``text`` with every phrase this module knows rendered in Hebrew.

    A string with no known phrase comes back unchanged, which is what
    keeps a criterion from a strategy added later readable instead of
    half-translated.
    """
    if not text:
        return text
    out = str(text)
    for pattern, replacement in PATTERNS:
        out = pattern.sub(replacement, out)
    for english, hebrew in _LONGEST_FIRST:
        if english in out:
            out = out.replace(english, hebrew)
    return out


__all__ = ["PATTERNS", "TERMS", "translate"]
