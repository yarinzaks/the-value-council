"""Whether the analyst layer is watching or deciding.

The five hybrid agents have carried an optional analyzer since they were
written, and the code that acts on its verdict was written with it: a
memo saying REJECT removes the name. So the switch that turns the layer
on has, until now, also been the switch that gives it a veto, and there
was no way to ask the question this mode exists for — what would it
have said? — without already living with the answer.

``SHADOW`` separates them. The analyzer runs on the real candidates of
a real run, the memo is written to disk and rendered on /brain, and the
selection is exactly what the quantitative screen produced. A fortnight
of that is readable evidence; the same fortnight under ``ENFORCE`` is a
book whose trades cannot be attributed to either half.

``ENFORCE`` is the default because it is what every existing caller
already means by passing an analyzer, and because a mode flag that
silently disarmed a configured veto would be the worse surprise.
"""

from __future__ import annotations

from enum import StrEnum


class LLMMode(StrEnum):
    """What the analyst layer is allowed to do with its own verdict."""

    #: No analyzer runs. Equivalent to passing none at all, and the
    #: state every agent is in before one is configured.
    OFF = "off"

    #: The analyzer runs and the memo is kept; the selection is
    #: untouched. Costs the same API calls as ENFORCE and changes
    #: nothing about what the book does, which is the point.
    SHADOW = "shadow"

    #: The verdict removes names, per each agent's own doctrine rule —
    #: Fisher's integrity gate, the others' BUY/HOLD list.
    ENFORCE = "enforce"

    @property
    def runs_the_analyzer(self) -> bool:
        return self is not LLMMode.OFF

    @property
    def may_drop_a_name(self) -> bool:
        return self is LLMMode.ENFORCE


__all__ = ["LLMMode"]
