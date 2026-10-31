"""Keeping numbers where they belong in a right-to-left message.

The problem
~~~~~~~~~~~

The notification is Hebrew prose with English tickers and dollar figures
inside it, and every renderer that shows it — Telegram on iOS, on
Android, on the desktop — lays it out with the Unicode bidirectional
algorithm. That algorithm decides a paragraph's direction from its first
strong character, then reorders the runs inside it.

So a line beginning in Hebrew is a right-to-left paragraph, and the
digits in it are a left-to-right run embedded in one. Where that run
*ends up* depends on the neutral characters around it — a dollar sign, a
plus, a bracket, a space — and the answer is frequently not what was
written. "יעד מכירה $31.24 +35.0%" can arrive with the sign on the wrong
side of its number, or with the percentage before the price it belongs
to. Three lines of every trade notification are exposed to this.

The fix
~~~~~~~

Isolates, not marks. ``LRI``/``PDI`` wrap a run so the algorithm treats
it as a single neutral object from the outside and resolves its insides
independently: the digits stay in their own order, the currency symbol
stays attached, and nothing leaks into the Hebrew around it. A mark
(``LRM``) only nudges the character beside it and has to be placed
correctly at every boundary, which is the kind of thing that works until
someone edits the sentence.

Both are invisible formatting characters. A renderer that does not
understand them draws nothing, so the worst case is exactly the layout
we have today.
"""

from __future__ import annotations

#: Isolate a left-to-right run inside right-to-left text.
LRI = "⁦"
#: Close the innermost isolate.
PDI = "⁩"
#: Force a line to be a right-to-left paragraph regardless of what it
#: starts with. Used where a line opens with a ticker or a figure but
#: belongs to a Hebrew message and should hang from the right margin.
RLM = "‏"


def ltr(text: str | None) -> str:
    """``text`` as one left-to-right island inside a Hebrew sentence.

    For anything written left to right that sits in Hebrew prose: a
    ticker, a price, a percentage, a date, a company name. Empty input
    returns empty rather than a pair of isolates around nothing.
    """
    if not text:
        return ""
    return f"{LRI}{text}{PDI}"


def rtl_line(text: str) -> str:
    """``text`` as a right-to-left paragraph, whatever it begins with.

    A line opening with a ticker or a figure would otherwise be laid out
    left to right and hang from the opposite margin to the Hebrew lines
    around it, which reads as a different message rather than the same
    one.
    """
    if not text:
        return text
    return f"{RLM}{text}"


def strip_marks(text: str) -> str:
    """``text`` without any of this module's characters.

    For tests and for anything that measures length against a limit the
    renderer counts differently — and for asserting that a formatter's
    output says what it is supposed to say, without every assertion
    having to know where the isolates went.
    """
    for mark in (LRI, PDI, RLM):
        text = text.replace(mark, "")
    return text


__all__ = ["LRI", "PDI", "RLM", "ltr", "rtl_line", "strip_marks"]
