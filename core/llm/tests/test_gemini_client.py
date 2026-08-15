"""Characterization tests for :class:`GeminiClient`.

Why this file exists
~~~~~~~~~~~~~~~~~~~~

Every agent's BUY/SELL decision arrives through
``generate_investment_memo``. Until now the module had no unit tests at
all: the only thing that exercised it was ``core/tests/test_connections.py``,
which is a human-run live probe, not a test. That meant the retry count,
the sampling temperature, the throttle interval and the JSON salvage
logic were all unpinned — a refactor could change any of them silently
and the suite would stay green.

These tests pin the observable behavior of the client so it can be
carried across an SDK migration unchanged.

The SDK seam
~~~~~~~~~~~~

Everything the vendor SDK is asked to do is captured by the
``sdk`` fixture, which exposes the request in vendor-neutral terms —
``sdk.model_name``, ``sdk.system_instruction``, ``sdk.prompt``,
``sdk.temperature``, ``sdk.response_mime_type``. Tests assert on those
names, never on the SDK's own call shape, so swapping the SDK
underneath rewrites one fixture rather than the whole file. That is the
point: if these assertions still pass after the swap, the behavior the
agents depend on genuinely did not move.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from google.genai import types
from pydantic import SecretStr, ValidationError

from core.exceptions import LLMError
from core.llm import gemini_client as gc
from core.llm.gemini_client import DEFAULT_MODEL, GeminiClient, InvestmentMemo
from core.llm.prompts import INVESTMENT_MEMO_SYSTEM

API_KEY = "test-api-key-not-a-real-secret"

VALID_MEMO: dict[str, Any] = {
    "decision": "BUY",
    "confidence": 0.82,
    "thesis": "Durable franchise trading below intrinsic value.",
    "key_metrics_passed": ["ROE > 15%", "Debt/Equity < 0.5"],
    "concerns": ["Input cost inflation"],
    "position_size_pct": 7.5,
    "expected_holding": "3-5 years",
    "exit_triggers": ["Moat erosion", "Price exceeds intrinsic value"],
}


# ---- SDK seam --------------------------------------------------------------
@dataclass
class _Response:
    """Stand-in for the SDK's response object.

    ``text`` is deliberately a plain attribute that may be ``None`` —
    the real SDK returns ``Optional[str]`` and the client's
    ``(response.text or "")`` guard exists for that case.
    """

    text: str | None


@dataclass
class _SdkRecorder:
    """Captures what the client asked the vendor SDK to do."""

    api_key: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    # Queued outcomes, one per call: a _Response to return or an
    # exception to raise. A single remaining entry is reused, so the
    # common case needs no bookkeeping.
    responses: list[Any] = field(default_factory=list)

    # --- Vendor-neutral view of the last request ---------------------------
    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def _last(self) -> dict[str, Any]:
        assert self.calls, "the SDK was never called"
        return self.calls[-1]

    # Each returns whatever the SDK was actually handed, so the type is
    # the vendor's to decide — the assertions are what pin it.
    @property
    def model_name(self) -> Any:
        return self._last["model"]

    @property
    def system_instruction(self) -> Any:
        return self._last["config"].system_instruction

    @property
    def prompt(self) -> Any:
        return self._last["contents"]

    @property
    def temperature(self) -> Any:
        return self._last["config"].temperature

    @property
    def response_mime_type(self) -> Any:
        return self._last["config"].response_mime_type


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> _SdkRecorder:
    """Replace the vendor SDK and the settings lookup.

    Returns a recorder whose ``responses`` list is popped once per call;
    each entry is either a :class:`_Response` to return or an exception
    to raise. Default: one valid memo.
    """
    rec = _SdkRecorder(responses=[_Response(json.dumps(VALID_MEMO))])
    outcomes = rec.responses

    class _FakeModels:
        def generate_content(
            self, *, model: str, contents: str, config: types.GenerateContentConfig
        ) -> _Response:
            rec.calls.append({"model": model, "contents": contents, "config": config})
            outcome = outcomes[0] if len(outcomes) == 1 else outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            assert isinstance(outcome, _Response), "queue a _Response or an exception"
            return outcome

    class _FakeClient:
        def __init__(self, *, api_key: str) -> None:
            rec.api_key = api_key
            self.models = _FakeModels()

    monkeypatch.setattr("core.llm.gemini_client.genai.Client", _FakeClient)
    monkeypatch.setattr(
        "core.llm.gemini_client.get_settings",
        lambda: _FakeSettings(SecretStr(API_KEY)),
    )
    return rec


@dataclass
class _FakeSettings:
    gemini_api_key: SecretStr


@pytest.fixture(autouse=True)
def _no_real_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite fast without hiding the behavior under test.

    Two independent sleeps exist, and a retry hits both: tenacity's
    back-off between attempts, and the client's own 4-second throttle,
    which the back-off resets the window for. Left alone, the six
    error-path tests cost eight seconds each.

    Both are stubbed here rather than shortened, so no test depends on
    wall-clock timing. The throttle's arithmetic is still asserted, in
    :class:`TestThrottle`, against a recording stub that shadows this
    one — ``monkeypatch`` unwinds them in reverse order.
    """
    GeminiClient._last_call_at = 0.0
    monkeypatch.setattr("core.llm.gemini_client.time.sleep", lambda _s: None)
    monkeypatch.setattr(_retrying(), "sleep", lambda _s: None)


# Tenacity attaches ``.retry`` to the wrapper at decoration time but
# does not declare it on the wrapper's type, so the alias is what makes
# the access checkable.
_DECORATED_MEMO_CALL: Any = GeminiClient.generate_investment_memo


def _retrying() -> Any:
    """The tenacity ``Retrying`` object behind the decorated method."""
    return _DECORATED_MEMO_CALL.retry


def _memo(client: GeminiClient) -> InvestmentMemo:
    """Call the client with throwaway inputs."""
    return client.generate_investment_memo(
        playbook="PLAYBOOK", stock_data={"ticker": "KO"}, portfolio_state={"cash": 1000}
    )


# ---- Model selection -------------------------------------------------------
class TestModelSelection:
    def test_default_model_is_flash(self) -> None:
        """The free tier this project targets is priced around flash."""
        assert DEFAULT_MODEL == "gemini-2.5-flash"

    def test_default_model_reaches_the_sdk(self, sdk: _SdkRecorder) -> None:
        client = GeminiClient()
        _memo(client)
        assert client._model_name == DEFAULT_MODEL
        assert sdk.model_name == DEFAULT_MODEL

    def test_model_override_reaches_the_sdk(self, sdk: _SdkRecorder) -> None:
        client = GeminiClient(model="gemini-2.5-pro")
        _memo(client)
        assert client._model_name == "gemini-2.5-pro"
        assert sdk.model_name == "gemini-2.5-pro"

    def test_system_instruction_is_the_memo_prompt(self, sdk: _SdkRecorder) -> None:
        _memo(GeminiClient())
        assert sdk.system_instruction == INVESTMENT_MEMO_SYSTEM

    def test_api_key_comes_from_settings(self, sdk: _SdkRecorder) -> None:
        GeminiClient()
        assert sdk.api_key == API_KEY

    def test_construction_makes_no_api_call(self, sdk: _SdkRecorder) -> None:
        """Building a client must stay cheap and offline."""
        GeminiClient()
        assert sdk.call_count == 0


# ---- Rate limiting ---------------------------------------------------------
class TestThrottle:
    def test_interval_is_four_seconds(self) -> None:
        assert gc._MIN_INTERVAL_SECONDS == 4.0

    def test_sleeps_the_remainder_of_the_window(
        self, sdk: _SdkRecorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slept: list[float] = []
        monkeypatch.setattr("core.llm.gemini_client.time.sleep", lambda s: slept.append(s))
        monkeypatch.setattr("core.llm.gemini_client.time.monotonic", lambda: 100.0)
        GeminiClient._last_call_at = 98.5  # 1.5s ago -> owes 2.5s

        GeminiClient()._throttle()

        assert slept == [pytest.approx(2.5)]

    def test_does_not_sleep_once_the_window_has_passed(
        self, sdk: _SdkRecorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slept: list[float] = []
        monkeypatch.setattr("core.llm.gemini_client.time.sleep", lambda s: slept.append(s))
        monkeypatch.setattr("core.llm.gemini_client.time.monotonic", lambda: 100.0)
        GeminiClient._last_call_at = 90.0  # 10s ago

        GeminiClient()._throttle()

        assert slept == []

    def test_stamp_is_updated_after_throttling(
        self, sdk: _SdkRecorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("core.llm.gemini_client.time.sleep", lambda _s: None)
        monkeypatch.setattr("core.llm.gemini_client.time.monotonic", lambda: 500.0)

        GeminiClient()._throttle()

        assert GeminiClient._last_call_at == 500.0

    def test_budget_is_shared_across_instances(
        self, sdk: _SdkRecorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The quota is per API key, not per object.

        Ten agents each build their own client; if the interval were
        per-instance the free tier would be blown ten times over.
        """
        slept: list[float] = []
        monkeypatch.setattr("core.llm.gemini_client.time.sleep", lambda s: slept.append(s))
        monkeypatch.setattr("core.llm.gemini_client.time.monotonic", lambda: 100.0)
        GeminiClient._last_call_at = 99.0

        GeminiClient()._throttle()  # a different instance ...
        assert slept == [pytest.approx(3.0)]

        GeminiClient._last_call_at = 99.0
        GeminiClient()._throttle()  # ... still sees the same clock
        assert slept == [pytest.approx(3.0), pytest.approx(3.0)]

    def test_throttle_runs_before_the_request(
        self, sdk: _SdkRecorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Throttling first is what keeps the free tier intact."""
        client = GeminiClient()
        order: list[str] = []
        original = client._throttle

        def _tracked() -> None:
            order.append("throttle")
            original()

        monkeypatch.setattr(client, "_throttle", _tracked)
        _memo(client)
        order.append("call")
        assert order == ["throttle", "call"]


# ---- Retry policy ----------------------------------------------------------
class TestRetryPolicy:
    def test_policy_is_three_attempts_with_capped_backoff(self) -> None:
        retrying = _retrying()
        assert retrying.stop.max_attempt_number == 3
        assert retrying.wait.multiplier == 2
        assert retrying.wait.min == 2
        assert retrying.wait.max == 30
        assert retrying.reraise is True

    def test_api_failure_is_retried_three_times(self, sdk: _SdkRecorder) -> None:
        sdk.responses[:] = [RuntimeError("503 unavailable")] * 3
        with pytest.raises(LLMError, match="Gemini API call failed"):
            _memo(GeminiClient())
        assert sdk.call_count == 3

    def test_a_late_success_is_returned(self, sdk: _SdkRecorder) -> None:
        sdk.responses[:] = [
            RuntimeError("429 rate limited"),
            _Response(json.dumps(VALID_MEMO)),
        ]
        memo = _memo(GeminiClient())
        assert memo.decision == "BUY"
        assert sdk.call_count == 2

    def test_unparseable_output_also_costs_three_attempts(self, sdk: _SdkRecorder) -> None:
        """A malformed reply is retried, not just reported.

        Worth pinning because it is expensive: three requests are spent
        on one bad response, and the throttle serializes them.
        """
        sdk.responses[:] = [_Response("not json at all")] * 3
        with pytest.raises(LLMError):
            _memo(GeminiClient())
        assert sdk.call_count == 3

    def test_throttle_applies_to_every_attempt(
        self, sdk: _SdkRecorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retry storm must not outrun the rate limit either."""
        client = GeminiClient()
        throttles: list[int] = []
        monkeypatch.setattr(client, "_throttle", lambda: throttles.append(1))
        sdk.responses[:] = [RuntimeError("boom")] * 3

        with pytest.raises(LLMError):
            _memo(client)

        assert len(throttles) == 3


# ---- Request construction --------------------------------------------------
class TestRequest:
    def test_sampling_parameters(self, sdk: _SdkRecorder) -> None:
        """Low temperature and JSON mode are load-bearing, not cosmetic."""
        _memo(GeminiClient())
        assert sdk.temperature == 0.4
        assert sdk.response_mime_type == "application/json"

    def test_prompt_carries_all_three_inputs(self, sdk: _SdkRecorder) -> None:
        GeminiClient().generate_investment_memo(
            playbook="THE-PLAYBOOK-BODY",
            stock_data={"ticker": "KO"},
            portfolio_state={"cash_usd": 4242},
        )
        assert "THE-PLAYBOOK-BODY" in sdk.prompt
        assert "KO" in sdk.prompt
        assert "4242" in sdk.prompt

    def test_dicts_are_pretty_printed(self, sdk: _SdkRecorder) -> None:
        _memo(GeminiClient())
        assert '\n  "ticker": "KO"' in sdk.prompt

    def test_pre_stringified_inputs_pass_through(self, sdk: _SdkRecorder) -> None:
        GeminiClient().generate_investment_memo(
            playbook="PB",
            stock_data="RAW-STOCK-BLOB",
            portfolio_state="RAW-PORTFOLIO-BLOB",
        )
        assert "RAW-STOCK-BLOB" in sdk.prompt
        assert "RAW-PORTFOLIO-BLOB" in sdk.prompt


# ---- Response handling -----------------------------------------------------
class TestResponseHandling:
    def test_happy_path_returns_a_validated_memo(self, sdk: _SdkRecorder) -> None:
        memo = _memo(GeminiClient())
        assert isinstance(memo, InvestmentMemo)
        assert memo.decision == "BUY"
        assert memo.confidence == 0.82
        assert memo.position_size_pct == 7.5
        assert memo.key_metrics_passed == ["ROE > 15%", "Debt/Equity < 0.5"]

    @pytest.mark.parametrize("payload", ["", "   \n\t  ", None])
    def test_blank_response_is_an_error(self, sdk: _SdkRecorder, payload: str | None) -> None:
        sdk.responses[:] = [_Response(payload)]
        with pytest.raises(LLMError, match="empty response"):
            _memo(GeminiClient())

    def test_sdk_exception_is_wrapped_and_chained(self, sdk: _SdkRecorder) -> None:
        original = RuntimeError("connection reset")
        sdk.responses[:] = [original] * 3
        with pytest.raises(LLMError, match="Gemini API call failed") as excinfo:
            _memo(GeminiClient())
        assert excinfo.value.__cause__ is original

    def test_schema_violation_reports_the_raw_text(self, sdk: _SdkRecorder) -> None:
        bad = dict(VALID_MEMO, decision="MAYBE")
        sdk.responses[:] = [_Response(json.dumps(bad))] * 3
        with pytest.raises(LLMError, match="did not match InvestmentMemo schema"):
            _memo(GeminiClient())

    def test_raw_excerpt_is_truncated(self, sdk: _SdkRecorder) -> None:
        """Never dump an unbounded model response into the log."""
        bad = dict(VALID_MEMO, decision="MAYBE", thesis="x" * 5000)
        sdk.responses[:] = [_Response(json.dumps(bad))] * 3
        with pytest.raises(LLMError) as excinfo:
            _memo(GeminiClient())
        assert len(str(excinfo.value)) < 1000


# ---- JSON salvage ----------------------------------------------------------
class TestParseJson:
    def test_bare_object(self) -> None:
        assert GeminiClient._parse_json('{"a": 1}') == {"a": 1}

    def test_json_fence(self) -> None:
        text = 'Here you go:\n```json\n{"a": 1}\n```'
        assert GeminiClient._parse_json(text) == {"a": 1}

    def test_bare_fence(self) -> None:
        assert GeminiClient._parse_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_surrounding_prose(self) -> None:
        text = 'Certainly. {"a": 1} Let me know if you need more.'
        assert GeminiClient._parse_json(text) == {"a": 1}

    def test_nested_object_survives_the_brace_scan(self) -> None:
        """``rfind("}")`` must reach the outermost closing brace."""
        text = '{"outer": {"inner": [1, 2]}}'
        assert GeminiClient._parse_json(text) == {"outer": {"inner": [1, 2]}}

    def test_no_object_at_all(self) -> None:
        with pytest.raises(LLMError, match="no JSON object found"):
            GeminiClient._parse_json("I cannot help with that.")

    def test_inverted_braces(self) -> None:
        with pytest.raises(LLMError, match="no JSON object found"):
            GeminiClient._parse_json("} nonsense {")

    def test_malformed_json(self) -> None:
        with pytest.raises(LLMError, match="invalid JSON"):
            GeminiClient._parse_json('{"a": 1,,}')


# ---- Input serialization ---------------------------------------------------
class TestToJsonStr:
    def test_strings_pass_through_untouched(self) -> None:
        assert GeminiClient._to_json_str("already a string") == "already a string"

    def test_dicts_are_indented(self) -> None:
        assert GeminiClient._to_json_str({"a": 1}) == '{\n  "a": 1\n}'

    def test_unserializable_values_fall_back_to_str(self) -> None:
        """Stock snapshots carry Decimals and datetimes; they must not raise."""
        from datetime import date
        from decimal import Decimal

        out = GeminiClient._to_json_str({"price": Decimal("1.5"), "as_of": date(2026, 1, 2)})
        assert "1.5" in out
        assert "2026-01-02" in out


# ---- Schema ----------------------------------------------------------------
class TestInvestmentMemoSchema:
    def test_valid_payload(self) -> None:
        assert InvestmentMemo.model_validate(VALID_MEMO).decision == "BUY"

    @pytest.mark.parametrize("decision", ["BUY", "SELL", "HOLD", "PASS"])
    def test_every_permitted_decision(self, decision: str) -> None:
        memo = InvestmentMemo.model_validate(dict(VALID_MEMO, decision=decision))
        assert memo.decision == decision

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("decision", "MAYBE"),
            ("confidence", 1.5),
            ("confidence", -0.1),
            ("position_size_pct", 100.1),
            ("position_size_pct", -1.0),
        ],
    )
    def test_out_of_range_values_are_rejected(self, field_name: str, value: Any) -> None:
        with pytest.raises(ValidationError):
            InvestmentMemo.model_validate(dict(VALID_MEMO, **{field_name: value}))

    def test_list_fields_default_to_empty(self) -> None:
        minimal = {
            "decision": "PASS",
            "confidence": 0.1,
            "thesis": "No.",
            "position_size_pct": 0.0,
            "expected_holding": "n/a",
        }
        memo = InvestmentMemo.model_validate(minimal)
        assert memo.key_metrics_passed == []
        assert memo.concerns == []
        assert memo.exit_triggers == []
