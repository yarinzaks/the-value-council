"""All five hybrid agents obey the mode, or none of them does usefully.

A shadow run whose point is "what would the layer have said" is worth
nothing if one of the five quietly kept its veto: the book would move
for a reason the fortnight's evidence does not contain, and nothing
would say which. The behavioural proof lives with Buffett, where the
fixtures are; this file is the census that catches the fourth agent
being wired and the fifth forgotten.
"""

from __future__ import annotations

import inspect
from unittest.mock import Mock

import pytest

from agents.buffett.wonderful_business import WarrenBuffett
from agents.fisher.quality_growth import PhilipFisher
from agents.klarman.margin_of_safety import SethKlarman
from agents.lynch.garp import PeterLynch
from agents.marks.cycle_value import HowardMarks
from core.llm.mode import LLMMode

#: Every agent that carries an analyzer, with the keyword it takes one
#: under. A sixth added without a line here is a sixth this file cannot
#: speak for.
HYBRIDS = [
    (WarrenBuffett, "moat_analyzer"),
    (PhilipFisher, "scuttlebutt_analyzer"),
    (SethKlarman, "downside_analyzer"),
    (PeterLynch, "category_classifier"),
    (HowardMarks, "second_level_analyzer"),
]


class TestTheModeItself:
    def test_off_runs_nothing(self) -> None:
        assert not LLMMode.OFF.runs_the_analyzer
        assert not LLMMode.OFF.may_drop_a_name

    def test_shadow_runs_but_does_not_decide(self) -> None:
        assert LLMMode.SHADOW.runs_the_analyzer
        assert not LLMMode.SHADOW.may_drop_a_name

    def test_enforce_does_both(self) -> None:
        assert LLMMode.ENFORCE.runs_the_analyzer
        assert LLMMode.ENFORCE.may_drop_a_name

    def test_it_survives_a_round_trip_through_a_string(self) -> None:
        """It will be read from configuration before long."""
        assert LLMMode("shadow") is LLMMode.SHADOW


@pytest.mark.parametrize("strategy,param", HYBRIDS, ids=lambda v: getattr(v, "__name__", v))
class TestEveryHybridObeysIt:
    def test_it_takes_the_mode(self, strategy: type, param: str) -> None:
        assert "llm_mode" in inspect.signature(strategy.__init__).parameters

    def test_the_default_is_still_enforce(self, strategy: type, param: str) -> None:
        """Passing an analyzer has always meant arming the veto, and a
        flag that silently disarmed one is the worse surprise."""
        built = strategy(edgar_cache=Mock(), **{param: None})

        assert built.llm_mode is LLMMode.ENFORCE

    def test_the_mode_reaches_the_instance(self, strategy: type, param: str) -> None:
        built = strategy(edgar_cache=Mock(), llm_mode=LLMMode.SHADOW, **{param: None})

        assert built.llm_mode is LLMMode.SHADOW

    def test_its_filter_asks_before_dropping(self, strategy: type, param: str) -> None:
        """Structural, and deliberately so.

        Each agent drops names by its own doctrine rule — Fisher's
        integrity gate, the others' BUY/HOLD list — so there is no one
        behaviour to assert across all five. What they must share is
        that the question is asked at all.
        """
        source = inspect.getsource(strategy._llm_filter)

        assert "may_drop_a_name" in source, (
            f"{strategy.__name__}._llm_filter can still drop a name in shadow"
        )

    def test_the_memo_is_kept_before_the_gate(self, strategy: type, param: str) -> None:
        """Otherwise shadow mode pays for memos it then throws away."""
        source = inspect.getsource(strategy._llm_filter)
        kept = source.index("memos.append(memo)")
        gate = source.index("may_drop_a_name")

        assert kept < gate, f"{strategy.__name__} gates before recording"
