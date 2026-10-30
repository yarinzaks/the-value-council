"""What the client must never do, and what it must refuse to return.

The analyst layer decides whether an agent buys. So the failure that
matters is not an exception — it is a memo that comes back looking
complete and is not. Every path below that could produce half an answer
raises instead.

The other half is drift. Two parameters were removed from the Messages
API and one was renamed between SDK majors, and the way that bites is
silently: a request that 400s in production because a field was carried
over from the client this replaced. The parameter assertions here are
the guard.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import anthropic
import pytest
from pydantic import BaseModel, SecretStr

from core.exceptions import LLMError
from core.llm.claude_client import ClaudeClient
from core.llm.memo_log import MemoLog


class Memo(BaseModel):
    """A stand-in for an agent's own memo model."""

    decision: str
    confidence: float


A_MEMO = Memo(decision="BUY", confidence=0.8)

#: The factory the `client` fixture hands back.
ClientFactory = Callable[..., tuple[ClaudeClient, "_FakeMessages"]]


class _FakeMessages:
    """Records what the client asked for, answers with what the test says."""

    def __init__(
        self,
        *,
        parsed: Any = A_MEMO,
        stop_reason: str = "end_turn",
        raises: BaseException | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._parsed = parsed
        self._stop = stop_reason
        self._raises = raises

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises

        class _Response:
            stop_reason = self._stop
            parsed_output = self._parsed

        return _Response()


class _FakeSdk:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ClientFactory:
    """A ClaudeClient whose SDK and settings are both injected.

    The settings are stubbed rather than loaded: the real loader reads
    the project's .env, and a test that depends on a developer's private
    file passes or fails for reasons that have nothing to do with it.
    """
    import core.llm.claude_client as mod

    def build(**kw: Any) -> tuple[ClaudeClient, _FakeMessages]:
        fake = _FakeMessages(**kw)
        monkeypatch.setattr(
            mod, "get_settings", lambda: type("S", (), {"anthropic_api_key": SecretStr("k")})()
        )
        monkeypatch.setattr(
            "core.llm.claude_client.anthropic.Anthropic",
            lambda **_: _FakeSdk(fake),
        )
        # A temp log: these tests must not write into the real data
        # directory, and recording is on by default by design.
        c = mod.ClaudeClient(
            min_interval_seconds=0.0, memo_log=MemoLog(root=tmp_path)
        )
        return c, fake

    return build


class TestTheHappyPath:
    def test_the_memo_comes_back_as_the_callers_own_type(self, client: ClientFactory) -> None:
        c, _fake = client()

        got = c.complete_memo(system="sys", prompt="p", schema=Memo)

        assert isinstance(got, Memo)
        assert got.decision == "BUY"

    def test_the_schema_the_caller_passed_is_what_is_requested(self, client: ClientFactory) -> None:
        c, fake = client()

        c.complete_memo(system="sys", prompt="the prompt", schema=Memo)

        sent = fake.calls[0]
        assert sent["output_format"] is Memo, "structured output, not prose"
        assert sent["system"] == "sys"
        assert sent["messages"] == [{"role": "user", "content": "the prompt"}]


class TestTheRemovedParameters:
    """Drift guards. Each of these 400s in production if re-added."""

    def test_no_sampling_parameters_are_sent(self, client: ClientFactory) -> None:
        """`temperature` was removed from the Messages API.

        The client this replaces passed 0.3–0.4. Carrying it over would
        fail every request, and it is exactly the kind of thing a
        well-meaning edit restores.
        """
        c, fake = client()

        c.complete_memo(system="s", prompt="p", schema=Memo)

        for banned in ("temperature", "top_p", "top_k"):
            assert banned not in fake.calls[0], f"{banned} was sent"

    def test_the_installed_sdk_really_does_reject_temperature(self) -> None:
        """Proves the assertion above is about reality, not folklore."""
        import inspect

        sig = inspect.signature(anthropic.Anthropic(api_key="x").messages.parse)
        assert "temperature" not in sig.parameters

    def test_thinking_is_absent_unless_asked_for(self, client: ClientFactory) -> None:
        c, fake = client()

        c.complete_memo(system="s", prompt="p", schema=Memo)

        assert "thinking" not in fake.calls[0], "extended reasoning is billed"

    def test_thinking_is_adaptive_when_asked_for(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Adaptive, not a budget: `budget_tokens` is rejected outright."""
        import core.llm.claude_client as mod

        fake = _FakeMessages()
        monkeypatch.setattr(
            mod, "get_settings", lambda: type("S", (), {"anthropic_api_key": SecretStr("k")})()
        )
        monkeypatch.setattr(
            "core.llm.claude_client.anthropic.Anthropic",
            lambda **_: _FakeSdk(fake),
        )

        mod.ClaudeClient(
            min_interval_seconds=0.0, thinking=True, memo_log=MemoLog(root=tmp_path)
        ).complete_memo(
            system="s", prompt="p", schema=Memo
        )

        assert fake.calls[0]["thinking"] == {"type": "adaptive"}
        assert "budget_tokens" not in fake.calls[0]["thinking"]


class TestItRefusesHalfAnAnswer:
    def test_a_truncated_memo_is_an_error_not_a_memo(self, client: ClientFactory) -> None:
        """Half a thesis read as a whole one is the worst outcome here."""
        c, _fake = client(stop_reason="max_tokens")

        with pytest.raises(LLMError, match="incomplete"):
            c.complete_memo(system="s", prompt="p", schema=Memo)

    def test_a_refusal_is_an_error(self, client: ClientFactory) -> None:
        c, _fake = client(stop_reason="refusal")

        with pytest.raises(LLMError, match="declined"):
            c.complete_memo(system="s", prompt="p", schema=Memo)

    def test_an_empty_parse_is_an_error(self, client: ClientFactory) -> None:
        """`parsed_output` is Optional and does come back None."""
        c, _fake = client(parsed=None)

        with pytest.raises(LLMError, match="returned no Memo"):
            c.complete_memo(system="s", prompt="p", schema=Memo)

    def test_an_api_failure_arrives_as_an_llm_error(self, client: ClientFactory) -> None:
        c, _fake = client(
            raises=anthropic.APIError("boom", request=None, body=None)  # type: ignore[arg-type]
        )

        with pytest.raises(LLMError, match="Claude API call failed"):
            c.complete_memo(system="s", prompt="p", schema=Memo)

    def test_the_schema_name_is_in_every_failure(self, client: ClientFactory) -> None:
        """So a log line says which agent's memo failed."""
        c, _fake = client(stop_reason="max_tokens")

        with pytest.raises(LLMError, match="Memo"):
            c.complete_memo(system="s", prompt="p", schema=Memo)


class TestConstruction:
    def test_a_missing_key_fails_at_construction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not on the first call, half way through a trading run."""
        import core.llm.claude_client as mod

        monkeypatch.setattr(
            mod, "get_settings", lambda: type("S", (), {"anthropic_api_key": None})()
        )

        with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
            mod.ClaudeClient()

    def test_the_key_is_never_in_the_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import core.llm.claude_client as mod

        monkeypatch.setattr(
            mod, "get_settings", lambda: type("S", (), {"anthropic_api_key": None})()
        )
        try:
            mod.ClaudeClient()
        except LLMError as exc:
            assert "SecretStr" not in str(exc)


class TestEveryMemoIsRecorded:
    """The audit trail. Switching the layer on without this is the
    thing the owner objected to: "it works and I cannot see it"."""

    def test_a_successful_call_is_written_down(self, client: ClientFactory) -> None:
        c, _fake = client()

        c.complete_memo(system="s", prompt="the prompt", schema=Memo)

        records = c._memo_log.read_all_for_agent("connectivity_probe")
        assert len(records) == 0, "Memo is not the probe's schema"
        written = c._memo_log.read_all_for_agent("memo")
        assert len(written) == 1
        assert written[0].memo == {"decision": "BUY", "confidence": 0.8}

    def test_the_whole_memo_is_kept_not_a_summary(self, client: ClientFactory) -> None:
        """The reason this exists: the journal keeps three fields."""
        rich = Memo(decision="BUY", confidence=0.65)
        c, _fake = client(parsed=rich)

        c.complete_memo(system="s", prompt="p", schema=Memo, agent="philip_fisher")

        got = c._memo_log.read_all_for_agent("philip_fisher")[0]
        assert got.memo == rich.model_dump(mode="json")

    def test_the_cost_of_the_call_is_kept(self, client: ClientFactory) -> None:
        c, _fake = client()

        c.complete_memo(system="s", prompt="p" * 40, schema=Memo, agent="a_book")

        got = c._memo_log.read_all_for_agent("a_book")[0]
        assert got.model == "claude-opus-5-5"
        assert got.prompt_chars == 40
        assert got.prompt_sha256 and len(got.prompt_sha256) == 64

    def test_an_explicit_agent_wins_over_the_schema_mapping(
        self, client: ClientFactory
    ) -> None:
        c, _fake = client()

        c.complete_memo(system="s", prompt="p", schema=Memo, agent="howard_marks")

        assert len(c._memo_log.read_all_for_agent("howard_marks")) == 1
        assert c._memo_log.read_all_for_agent("memo") == []

    def test_a_failed_call_records_nothing(self, client: ClientFactory) -> None:
        """A truncated memo is not reasoning, it is a fragment."""
        c, _fake = client(stop_reason="max_tokens")

        with pytest.raises(LLMError):
            c.complete_memo(system="s", prompt="p", schema=Memo, agent="a_book")

        assert c._memo_log.read_all_for_agent("a_book") == []

    def test_a_broken_log_does_not_fail_the_call(
        self, client: ClientFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The memo is already in the caller's hands by then.

        Losing the record is bad; throwing away a memo the agent is
        entitled to act on, because a disk write failed, is worse.
        """
        c, _fake = client()
        monkeypatch.setattr(
            c._memo_log, "log", lambda _r: (_ for _ in ()).throw(OSError("disk full"))
        )

        got = c.complete_memo(system="s", prompt="p", schema=Memo)

        assert got.decision == "BUY", "the call must still return"

    def test_recording_is_on_without_being_asked_for(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No call site gets to opt out of being auditable."""
        import inspect

        import core.llm.claude_client as mod

        sig = inspect.signature(mod.ClaudeClient.__init__)
        assert sig.parameters["memo_log"].default is None, (
            "the default must construct a real log, not disable recording"
        )
        source = inspect.getsource(mod.ClaudeClient._record)
        assert "self._memo_log.log" in source
