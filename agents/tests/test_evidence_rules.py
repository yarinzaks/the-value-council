"""The evidence clause must reach every LLM analyzer.

Five agents run a model in live mode and none of their prompts said
what to do about a missing figure or forbade asserting one that was
never supplied. In the quant path that failure produces a wrong ratio;
here it produces a confident sentence containing a number nobody
computed, and those memos are the "why" a reader is shown.
"""

from __future__ import annotations

import importlib

import pytest

from agents.evidence_rules import EVIDENCE_CLAUSE, with_evidence_rules

#: (module, prompt constant) for every analyzer that talks to a model.
ANALYZERS = [
    ("agents.lynch.category_classifier", "_LYNCH_SYSTEM_PROMPT"),
    ("agents.marks.second_level", "_MARKS_SYSTEM_PROMPT"),
    ("agents.fisher.scuttlebutt", "_FISHER_SYSTEM_PROMPT"),
    ("agents.klarman.downside", "_KLARMAN_SYSTEM_PROMPT"),
    ("agents.buffett.moat_analyzer", "_BUFFETT_SYSTEM_PROMPT"),
]


class TestTheClause:
    def test_it_forbids_supplying_an_absent_figure(self) -> None:
        text = EVIDENCE_CLAUSE.lower()

        assert "must appear in the snapshot" in text
        assert "never substitute zero" in text

    def test_it_states_the_rule_the_code_follows(self) -> None:
        # The same sentence the leverage helper, Fisher's dilution point
        # and Marks's temperature signals were each fixed to obey.
        assert "absence of evidence is not evidence" in EVIDENCE_CLAUSE.lower()

    def test_missing_data_lowers_confidence(self) -> None:
        text = EVIDENCE_CLAUSE.lower()

        assert "lowers your confidence" in text
        assert "never raises it" in text

    def test_it_offers_watch_rather_than_a_guess(self) -> None:
        # The alternative to inventing a number has to be spelled out,
        # or the model picks the one that completes the schema.
        assert "watch" in EVIDENCE_CLAUSE.lower()

    def test_it_binds_over_doctrine(self) -> None:
        assert "override your investing doctrine" in EVIDENCE_CLAUSE.lower()


class TestWithEvidenceRules:
    def test_it_appends_rather_than_prepends(self) -> None:
        # Identity and doctrine first, then the rules stated as binding
        # over them — a clause at the top reads as context to weigh.
        out = with_evidence_rules("You are Peter Lynch.")

        assert out.startswith("You are Peter Lynch.")
        assert out.endswith(EVIDENCE_CLAUSE)

    def test_it_does_not_double_the_separator(self) -> None:
        assert "\n\n\n" not in with_evidence_rules("You are Peter Lynch.\n\n")


@pytest.mark.parametrize(("module_name", "prompt_name"), ANALYZERS)
def test_every_analyzer_applies_the_clause(
    module_name: str, prompt_name: str
) -> None:
    """The raw constant stays clause-free; the wrapper adds it at the
    call site, so all five cannot drift apart."""
    mod = importlib.import_module(module_name)
    prompt = getattr(mod, prompt_name)

    assert "absence of evidence" not in prompt.lower()
    assert "absence of evidence" in with_evidence_rules(prompt).lower()


@pytest.mark.parametrize(("module_name", "prompt_name"), ANALYZERS)
def test_no_analyzer_passes_the_bare_prompt(
    module_name: str, prompt_name: str
) -> None:
    """Guards the wiring itself: a future edit that hands the model the
    unwrapped constant would silently drop the rules for that agent."""
    mod = importlib.import_module(module_name)
    source = importlib.import_module(mod.__name__).__file__
    assert source is not None
    with open(source, encoding="utf-8") as fh:
        text = fh.read()

    assert f"system_instruction={prompt_name}," not in text
    assert f"with_evidence_rules({prompt_name})" in text
