"""The layer is optional, and its absence is not a fault.

A checkout without an API key is the normal state of a fresh clone, and
a run that cannot reach the analyst layer must still trade on its
quantitative screen exactly as it did before there was one. So every
way of not having the layer — no key, no playbook, mode off — returns
nothing rather than raising, and the mode the agents are told about is
the one that actually applies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.llm.claude_client import LLMError
from core.llm.mode import LLMMode
from core.llm.wiring import AGENTS_ROOT, build_analyst_layer

HYBRIDS = ("buffett", "lynch", "marks", "klarman", "fisher")


class _StubClient:
    """Enough of a client for the wiring: it names its model, which the
    layer's own log line reads."""

    model_name = "claude-sonnet-5-5"


class TestOffCostsNothing:
    def test_off_never_builds_a_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A run that does not want the layer must not touch the key."""

        def explode(*_a: object, **_k: object) -> None:
            raise AssertionError("OFF constructed a client")

        monkeypatch.setattr("core.llm.wiring.ClaudeClient", explode)
        layer = build_analyst_layer(HYBRIDS, mode=LLMMode.OFF)

        assert not layer.available
        assert layer.mode is LLMMode.OFF

    def test_off_builds_no_analyzer(self) -> None:
        layer = build_analyst_layer(HYBRIDS, mode=LLMMode.OFF)

        assert layer.build("buffett", object) is None


class TestAMissingKeyIsNotAFailure:
    def test_it_degrades_to_off_rather_than_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_key(*_a: object, **_k: object) -> None:
            raise LLMError("ANTHROPIC_API_KEY is not set")

        monkeypatch.setattr("core.llm.wiring.ClaudeClient", no_key)
        layer = build_analyst_layer(HYBRIDS, mode=LLMMode.SHADOW)

        assert layer.mode is LLMMode.OFF, "the books must not be told shadow"
        assert not layer.available
        assert layer.build("buffett", object) is None


class TestPlaybooks:
    def test_every_hybrid_has_one_on_disk(self) -> None:
        """A missing file would silently disable that agent's layer."""
        for agent in HYBRIDS:
            path = AGENTS_ROOT / agent / "playbook.md"
            assert path.exists(), path
            assert path.read_text().strip(), f"{agent}: empty playbook"

    def test_an_agent_without_one_simply_has_no_analyzer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("core.llm.wiring.ClaudeClient", lambda *a, **k: _StubClient())
        monkeypatch.setattr(
            "core.llm.wiring._read_playbook",
            lambda agent: None if agent == "lynch" else "PLAYBOOK",
        )
        layer = build_analyst_layer(HYBRIDS, mode=LLMMode.SHADOW)

        assert layer.build("lynch", lambda **kw: "built") is None
        assert layer.build("buffett", lambda **kw: "built") == "built"

    def test_no_playbooks_at_all_turns_the_layer_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("core.llm.wiring.ClaudeClient", lambda *a, **k: _StubClient())
        monkeypatch.setattr("core.llm.wiring._read_playbook", lambda _a: None)
        layer = build_analyst_layer(HYBRIDS, mode=LLMMode.SHADOW)

        assert layer.mode is LLMMode.OFF

    def test_an_empty_playbook_counts_as_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("core.llm.wiring.AGENTS_ROOT", tmp_path)
        (tmp_path / "buffett").mkdir()
        (tmp_path / "buffett" / "playbook.md").write_text("   \n")
        monkeypatch.setattr("core.llm.wiring.ClaudeClient", lambda *a, **k: _StubClient())

        assert build_analyst_layer(("buffett",), mode=LLMMode.SHADOW).mode is LLMMode.OFF


class TestOneClientForAllFive:
    def test_the_analyzers_share_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Five clients would space their calls against five clocks."""
        sentinel = _StubClient()
        monkeypatch.setattr("core.llm.wiring.ClaudeClient", lambda *a, **k: sentinel)
        monkeypatch.setattr("core.llm.wiring._read_playbook", lambda _a: "PLAYBOOK")
        layer = build_analyst_layer(HYBRIDS, mode=LLMMode.SHADOW)

        built = [
            layer.build(agent, lambda *, client, playbook: client) for agent in HYBRIDS
        ]

        assert built == [sentinel] * len(HYBRIDS)
