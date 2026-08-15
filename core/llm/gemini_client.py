"""Gemini API client wrapper.

Wraps :mod:`google.genai` with retry logic, rate limiting, and
JSON-output parsing. The free tier (1500 RPD, ~1 RPM) is the default
target; the limiter throttles to stay safely inside that envelope.

The SDK does no retrying of its own: ``HttpOptions.retry_options``
defaults to ``None``, which the client reads as "never retry". Every
attempt you see here is one this module asked for, so the tenacity
policy below remains the whole story.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config import get_settings
from core.exceptions import LLMError
from core.logger import get_logger

from .prompts import (
    INVESTMENT_MEMO_SYSTEM,
    render_investment_memo_prompt,
)

DEFAULT_MODEL: str = "gemini-2.5-flash"
_MIN_INTERVAL_SECONDS: float = 4.0  # ~15 RPM safely under flash's free tier

Decision = Literal["BUY", "SELL", "HOLD", "PASS"]


class InvestmentMemo(BaseModel):
    """Structured investment memo returned by the LLM."""

    decision: Decision
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str
    key_metrics_passed: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    position_size_pct: float = Field(ge=0.0, le=100.0)
    expected_holding: str
    exit_triggers: list[str] = Field(default_factory=list)


def _log_retry(retry_state: RetryCallState) -> None:
    logger = get_logger("core.llm.gemini_client")
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(f"Gemini retry {retry_state.attempt_number}/3: {exc}")


_retry_llm = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(LLMError),
    before_sleep=_log_retry,
    reraise=True,
)


class GeminiClient:
    """Thin wrapper over the Gemini SDK with rate limiting and JSON parsing."""

    _last_call_at: float = 0.0
    _lock: threading.Lock = threading.Lock()

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.logger = get_logger("core.llm.gemini_client")
        self._model_name = model
        api_key = get_settings().gemini_api_key.get_secret_value()
        # The key is held on the client rather than configured globally,
        # and the model and system instruction are named per request.
        # Constructing this opens no connection.
        self._sdk = genai.Client(api_key=api_key)

    # --- Rate limit ----------------------------------------------------------
    def _throttle(self) -> None:
        with GeminiClient._lock:
            elapsed = time.monotonic() - GeminiClient._last_call_at
            if elapsed < _MIN_INTERVAL_SECONDS:
                wait = _MIN_INTERVAL_SECONDS - elapsed
                self.logger.debug(f"throttling Gemini call: sleeping {wait:.1f}s")
                time.sleep(wait)
            GeminiClient._last_call_at = time.monotonic()

    # --- Public --------------------------------------------------------------
    @_retry_llm
    def generate_investment_memo(
        self,
        playbook: str,
        stock_data: dict[str, Any] | str,
        portfolio_state: dict[str, Any] | str,
    ) -> InvestmentMemo:
        """Send the investment memo prompt and return a parsed memo.

        Args:
            playbook: The investor's playbook in markdown.
            stock_data: Snapshot of the stock under consideration. Dicts
                are serialized to indented JSON.
            portfolio_state: The agent's current portfolio. Dicts are
                serialized to indented JSON.

        Raises:
            LLMError: When the API errors persist or the response is not
                valid JSON matching :class:`InvestmentMemo`.
        """
        self._throttle()

        prompt = render_investment_memo_prompt(
            playbook=playbook,
            stock_data=self._to_json_str(stock_data),
            portfolio_state=self._to_json_str(portfolio_state),
        )

        self.logger.debug(f"Gemini call: model={self._model_name}, prompt_len={len(prompt)}")
        try:
            response = self._sdk.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=INVESTMENT_MEMO_SYSTEM,
                    temperature=0.4,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:
            raise LLMError(f"Gemini API call failed: {exc}") from exc

        text = (response.text or "").strip()
        if not text:
            raise LLMError("Gemini returned an empty response")

        memo_dict = self._parse_json(text)
        try:
            return InvestmentMemo.model_validate(memo_dict)
        except Exception as exc:
            raise LLMError(
                f"Gemini response did not match InvestmentMemo schema: {exc}\n"
                f"Raw: {text[:500]}"
            ) from exc

    # --- Helpers -------------------------------------------------------------
    @staticmethod
    def _to_json_str(value: dict[str, Any] | str) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, indent=2, default=str)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Best-effort JSON extraction.

        Handles bare JSON, JSON inside markdown fences, and JSON with
        leading/trailing prose (rare but observed).
        """
        # Strip markdown fences if the model added them despite instructions.
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1)

        # Find the first balanced JSON object.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMError(f"no JSON object found in response: {text[:200]}")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"invalid JSON in response: {exc}") from exc


__all__ = ["DEFAULT_MODEL", "GeminiClient", "InvestmentMemo"]
