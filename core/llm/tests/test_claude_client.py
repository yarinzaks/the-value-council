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

from typing import Any

import anthropic
import pytest
from pydantic import BaseModel, SecretStr

from core.exceptions import LLMError


class Memo(BaseModel):
    """A stand-in for an agent's own memo model."""

    decision: str
    confidence: float


A_MEMO = Memo(decision="BUY", confidence=0.8)


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
def client(monkeypatch: pytest.MonkeyPatch):
    """A ClaudeClient whose SDK and settings are both injected.

    The settings are stubbed rather than loaded: the real loader reads
    the project's .env, and a test that depends on a developer's private
    file passes or fails for reasons that have nothing to do with it.
    """
    import core.llm.claude_client as mod

    def build(**kw: Any):
        fake = _FakeMessages(**kw)
        monkeypatch.setattr(
            mod, "get_settings", lambda: type("S", (), {"anthropic_api_key": SecretStr("k")})()
        )
        monkeypatch.setattr(mod.anthropic, "Anthropic", lambda **_: _FakeSdk(fake))
        c = mod.ClaudeClient(min_interval_seconds=0.0)
        return c, fake

    return build


class TestTheHappyPath:
    def test_the_memo_comes_back_as_the_callers_own_type(self, client):
        c, _fake = client()

        got = c.complete_memo(system="sys", prompt="p", schema=Memo)

        assert isinstance(got, Memo)
        assert got.decision == "BUY"

    def test_the_schema_the_caller_passed_is_what_is_requested(self, client):
        c, fake = client()

        c.complete_memo(system="sys", prompt="the prompt", schema=Memo)

        sent = fake.calls[0]
        assert sent["output_format"] is Memo, "structured output, not prose"
        assert sent["system"] == "sys"
        assert sent["messages"] == [{"role": "user", "content": "the prompt"}]


class TestTheRemovedParameters:
    """Drift guards. Each of these 400s in production if re-added."""

    def test_no_sampling_parameters_are_sent(self, client):
        """`temperature` was removed from the Messages API.

        The client this replaces passed 0.3–0.4. Carrying it over would
        fail every request, and it is exactly the kind of thing a
        well-meaning edit restores.
        """
        c, fake = client()

        c.complete_memo(system="s", prompt="p", schema=Memo)

        for banned in ("temperature", "top_p", "top_k"):
            assert banned not in fake.calls[0], f"{banned} was sent"

    def test_the_installed_sdk_really_does_reject_temperature(self):
        """Proves the assertion above is about reality, not folklore."""
        import inspect

        sig = inspect.signature(anthropic.Anthropic(api_key="x").messages.parse)
        assert "temperature" not in sig.parameters

    def test_thinking_is_absent_unless_asked_for(self, client):
        c, fake = client()

        c.complete_memo(system="s", prompt="p", schema=Memo)

        assert "thinking" not in fake.calls[0], "extended reasoning is billed"

    def test_thinking_is_adaptive_when_asked_for(self, monkeypatch):
        """Adaptive, not a budget: `budget_tokens` is rejected outright."""
        import core.llm.claude_client as mod

        fake = _FakeMessages()
        monkeypatch.setattr(
            mod, "get_settings", lambda: type("S", (), {"anthropic_api_key": SecretStr("k")})()
        )
        monkeypatch.setattr(mod.anthropic, "Anthropic", lambda **_: _FakeSdk(fake))

        mod.ClaudeClient(min_interval_seconds=0.0, thinking=True).complete_memo(
            system="s", prompt="p", schema=Memo
        )

        assert fake.calls[0]["thinking"] == {"type": "adaptive"}
        assert "budget_tokens" not in fake.calls[0]["thinking"]


class TestItRefusesHalfAnAnswer:
    def test_a_truncated_memo_is_an_error_not_a_memo(self, client):
        """Half a thesis read as a whole one is the worst outcome here."""
        c, _fake = client(stop_reason="max_tokens")

        with pytest.raises(LLMError, match="incomplete"):
            c.complete_memo(system="s", prompt="p", schema=Memo)

    def test_a_refusal_is_an_error(self, client):
        c, _fake = client(stop_reason="refusal")

        with pytest.raises(LLMError, match="declined"):
            c.complete_memo(system="s", prompt="p", schema=Memo)

    def test_an_empty_parse_is_an_error(self, client):
        """`parsed_output` is Optional and does come back None."""
        c, _fake = client(parsed=None)

        with pytest.raises(LLMError, match="returned no Memo"):
            c.complete_memo(system="s", prompt="p", schema=Memo)

    def test_an_api_failure_arrives_as_an_llm_error(self, client):
        c, _fake = client(
            raises=anthropic.APIError("boom", request=None, body=None)  # type: ignore[arg-type]
        )

        with pytest.raises(LLMError, match="Claude API call failed"):
            c.complete_memo(system="s", prompt="p", schema=Memo)

    def test_the_schema_name_is_in_every_failure(self, client):
        """So a log line says which agent's memo failed."""
        c, _fake = client(stop_reason="max_tokens")

        with pytest.raises(LLMError, match="Memo"):
            c.complete_memo(system="s", prompt="p", schema=Memo)


class TestConstruction:
    def test_a_missing_key_fails_at_construction(self, monkeypatch):
        """Not on the first call, half way through a trading run."""
        import core.llm.claude_client as mod

        monkeypatch.setattr(
            mod, "get_settings", lambda: type("S", (), {"anthropic_api_key": None})()
        )

        with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
            mod.ClaudeClient()

    def test_the_key_is_never_in_the_error(self, monkeypatch):
        import core.llm.claude_client as mod

        monkeypatch.setattr(
            mod, "get_settings", lambda: type("S", (), {"anthropic_api_key": None})()
        )
        try:
            mod.ClaudeClient()
        except LLMError as exc:
            assert "SecretStr" not in str(exc)
