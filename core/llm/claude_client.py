"""Claude client for the agents' analyst layer.

Why this exposes a method instead of a client
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The client this replaces offered exactly one public call, with one
prompt baked into it. Every agent needs its own system prompt and its
own memo schema, so all five reached around it into the provider SDK —
``self.client._sdk.models.generate_content(...)`` — and each imported
the vendor's types to build a config object. That is why swapping the
provider touched nine files instead of one.

So the provider-shaped surface is one method here. It takes the system
prompt, the rendered prompt and the agent's own Pydantic model, and
returns an instance of that model. No caller imports an SDK, and the
next provider change is this file alone.

What structured output replaced
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Asking for JSON and scraping it back out. The old path set a JSON mime
type, then ran a regex for a fenced block, then searched for the first
``{`` and the last ``}``, then validated — three fallbacks deep, each
one a place a memo could be silently mangled. ``output_format`` takes
the model class and the response carries a validated instance, so a
malformed memo is an error rather than a guess.

Two things deliberately absent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**No sampling temperature.** The agents asked for 0.3–0.4 to keep
judgments repeatable. ``temperature``, ``top_p`` and ``top_k`` were
removed from the Messages API, so there is nothing to carry over and
no equivalent to set. Verified against the installed SDK: the
parameter is not in the signature.

**No retry decorator.** The old client wrapped its call in tenacity
because the Gemini SDK does not retry at all. This one retries
connection errors, 408, 409, 429 and 5xx with exponential backoff on
its own. Keeping both would have meant up to six attempts where three
were intended, which on a rate limit is the opposite of backing off.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import anthropic
from pydantic import BaseModel, Field

from core.config import get_settings
from core.exceptions import LLMError
from core.llm.prompts import (
    INVESTMENT_MEMO_SYSTEM,
    render_investment_memo_prompt,
)
from core.logger import get_logger

logger = get_logger("core.llm.claude_client")

#: Opus 5.5 — the judgment these memos ask for is the kind worth the
#: better model, and the brain runs once a day per agent rather than
#: once per quote.
DEFAULT_MODEL: str = "claude-opus-5-5"

#: Enough for the longest memo any agent defines. Fisher's carries a
#: verdict and a note for each of fifteen points; the rest are shorter.
#: A memo that hits this ceiling is reported as truncated rather than
#: parsed, because half a thesis is worse than none.
DEFAULT_MAX_TOKENS: int = 8192

#: A floor between calls. The SDK already backs off on a 429, so this
#: is not the rate limiter — it is there so a run that screens many
#: names does not open with a burst that earns one.
DEFAULT_MIN_INTERVAL_SECONDS: float = 1.0


class InvestmentMemo(BaseModel):
    """The generic memo shape, used by the connectivity check.

    Each agent defines its own richer model; this is the one the plain
    "does the brain answer at all" path asks for.
    """

    decision: str
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str
    key_metrics_passed: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    position_size_pct: float = Field(ge=0.0, le=100.0)
    expected_holding: str
    exit_triggers: list[str] = Field(default_factory=list)


class ClaudeClient:
    """One Claude call, shaped by the caller's own schema."""

    _last_call_at: float = 0.0
    _lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        thinking: bool = False,
    ) -> None:
        """Hold the key and the defaults. Opens no connection.

        ``thinking`` is off by default and deliberately a parameter.
        Extended reasoning is worth having for a judgment like this, and
        it is also what the bill is made of — so turning it on is a
        decision taken when the analyst layer is switched on, not one
        buried in a default.
        """
        self.logger = logger
        self._model_name = model
        self._max_tokens = max_tokens
        self._min_interval = min_interval_seconds
        self._thinking = thinking

        key = get_settings().anthropic_api_key
        if key is None:
            raise LLMError(
                "ANTHROPIC_API_KEY is not set; the analyst layer cannot run"
            )
        self._sdk = anthropic.Anthropic(api_key=key.get_secret_value())

    # --- Rate floor ----------------------------------------------------------
    def _throttle(self) -> None:
        with ClaudeClient._lock:
            elapsed = time.monotonic() - ClaudeClient._last_call_at
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                self.logger.debug(f"spacing Claude calls: sleeping {wait:.1f}s")
                time.sleep(wait)
            ClaudeClient._last_call_at = time.monotonic()

    # --- Public --------------------------------------------------------------
    def complete_memo[MemoT: BaseModel](
        self,
        *,
        system: str,
        prompt: str,
        schema: type[MemoT],
    ) -> MemoT:
        """Send one prompt and return a validated instance of ``schema``.

        Raises:
            LLMError: on an API failure, on a truncated or refused
                response, or when the model returned nothing the schema
                accepts. Never returns a partially filled memo — an
                agent acting on half a thesis is worse than one that
                reports it could not get an answer.
        """
        self._throttle()
        self.logger.debug(
            f"Claude call: model={self._model_name}, "
            f"schema={schema.__name__}, prompt_len={len(prompt)}"
        )

        kwargs: dict[str, Any] = {}
        if self._thinking:
            # Adaptive rather than a token budget: `budget_tokens` is
            # rejected outright on this model family.
            kwargs["thinking"] = {"type": "adaptive"}

        try:
            response = self._sdk.messages.parse(
                model=self._model_name,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                **kwargs,
            )
        except anthropic.APIError as exc:
            # The SDK's own type, so a rate limit or a bad key arrives
            # named rather than as a bare Exception.
            raise LLMError(f"Claude API call failed: {exc}") from exc

        stop = response.stop_reason
        if stop == "max_tokens":
            raise LLMError(
                f"Claude truncated the {schema.__name__} at "
                f"{self._max_tokens} tokens; the memo is incomplete"
            )
        if stop == "refusal":
            raise LLMError(f"Claude declined to produce the {schema.__name__}")

        memo = response.parsed_output
        if memo is None:
            # `parsed_output` is Optional: a response whose text block
            # carried nothing the schema accepted leaves it empty.
            raise LLMError(
                f"Claude returned no {schema.__name__} "
                f"(stop_reason={stop!r})"
            )
        return memo

    def generate_investment_memo(
        self,
        playbook: str,
        stock_data: dict[str, Any] | str,
        portfolio_state: dict[str, Any] | str,
    ) -> InvestmentMemo:
        """The generic memo, for checking the layer answers at all."""
        return self.complete_memo(
            system=INVESTMENT_MEMO_SYSTEM,
            prompt=render_investment_memo_prompt(
                playbook=playbook,
                stock_data=self._to_json_str(stock_data),
                portfolio_state=self._to_json_str(portfolio_state),
            ),
            schema=InvestmentMemo,
        )

    # --- Helpers -------------------------------------------------------------
    @staticmethod
    def _to_json_str(value: dict[str, Any] | str) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, indent=2, default=str)


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MIN_INTERVAL_SECONDS",
    "DEFAULT_MODEL",
    "ClaudeClient",
    "InvestmentMemo",
]
