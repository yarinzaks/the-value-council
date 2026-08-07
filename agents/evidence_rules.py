"""The evidence clause every agent's LLM prompt carries.

Why this exists
~~~~~~~~~~~~~~~

Five agents run an LLM analyzer in live mode — Lynch's classifier,
Marks's second-level memo, Fisher's scuttlebutt, Klarman's downside
case, Buffett's moat assessment. Each prompt is careful about voice,
structure and output schema, and none of the five said anything about
what to do when a figure is missing, or forbade asserting one that was
never supplied.

That is the same failure fixed all through the quant path this session,
and it lands harder here. A leverage helper that reads an untagged
balance sheet as zero debt produces a wrong number; a language model
asked to write a paragraph about a company whose current ratio is
``null`` produces a confident sentence containing a number nobody
computed. Those memos are the ``why`` the dashboard shows a reader, so
an invented figure is not an internal error — it is the product.

The rule is the one the code already follows: absence of evidence is
not evidence. A missing figure is reported as missing, and it lowers
confidence rather than being filled in.

Stated once, imported by all five, so the wording cannot drift apart.
"""

from __future__ import annotations

#: Appended to every agent system prompt. Written as instructions to
#: the model, in the second person, to match the surrounding prompts.
EVIDENCE_CLAUSE = """\

EVIDENCE RULES — these override your investing doctrine when they
conflict, because a wrong number is worse than a missing one.

1. Every figure you state must appear in the snapshot you were given.
   Do not compute a ratio from figures that are not both present, and
   do not recall a company's numbers from memory — the snapshot is
   point-in-time and your memory is not.

2. When a field is null, absent, or marked unavailable, say so in
   plain words ("the current ratio is not reported in this filing").
   Never substitute zero, an industry average, a prior year, or an
   estimate. Absence of evidence is not evidence: a company whose debt
   is untagged is not a company without debt.

3. Missing data lowers your confidence — it never raises it. If a
   criterion your doctrine requires cannot be checked, that criterion
   has not been met. Say which check you could not perform.

4. If too much is missing to reach a view, the honest output is a
   WATCH with the gaps named, not a BUY on the fields that happen to
   be present.

5. Qualitative judgement is yours to make; numbers are not. You may
   argue about what a figure means. You may not supply the figure.
"""


def with_evidence_rules(system_prompt: str) -> str:
    """Return ``system_prompt`` with the evidence clause appended.

    Appended rather than prepended: the agent's identity and doctrine
    should be established first, and these rules are then stated as
    binding over it.
    """
    return system_prompt.rstrip() + "\n" + EVIDENCE_CLAUSE


__all__ = ["EVIDENCE_CLAUSE", "with_evidence_rules"]
