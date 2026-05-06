"""Gemini-powered moat analyzer — qualitative leg of the hybrid agent.

Per playbook §5 + §12, Buffett's framework is ~60% qualitative. This
module is the bridge between our quantitative rank (``ranking.py``)
and a final BUY/REJECT/WATCH verdict that includes:

  * Moat type (one of 5: brand, low-cost, network, switching,
    regulatory)
  * Moat durability estimate
  * Bilingual thesis (en + he)
  * Two-paragraph business description
  * Specific exit triggers tied to moat erosion

The output schema follows playbook §12.4 exactly.

Important — when this runs and when it does NOT:

  * **Live mode** (``live/runner.py``): runs once per top-N candidate
    on each rebalance. Real Gemini calls.
  * **Backtest mode** (``run_full_market_validation.py``): NEVER
    runs. Calling Gemini inside a backtest creates two problems —
    (a) lookahead bias (model knows the future), (b) infeasible
    runtime (free-tier 15 RPM × 30 candidates × 6 dates = 12+ min
    just on LLM, plus daily-quota burn). Documented honest limit.

For unit tests, swap the GeminiClient for an instance with a stubbed
``generate_content`` method — see ``tests/test_moat_analyzer.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.exceptions import LLMError
from core.llm.gemini_client import GeminiClient
from core.logger import get_logger

logger = get_logger("agents.buffett.moat_analyzer")


MoatType = Literal[
    "Brand",
    "Low-Cost",
    "Network",
    "Switching",
    "Regulatory",
    "None",
]
BuffettDecision = Literal["BUY", "HOLD", "REJECT", "SELL", "WATCH"]
PhaseApplied = Literal[
    "Phase 1 (Graham orthodox)",
    "Phase 2 (Quality + Moats)",
]
CircleStatus = Literal["INSIDE", "OUTSIDE"]


class CrossReferenceSignals(BaseModel):
    """13F + insider signals. Optional — None means "not researched"."""

    berkshire_holds_position: bool = False
    other_quality_investors_holding: list[str] = Field(default_factory=list)
    insider_buying_90d_count: int = 0
    insider_selling_90d_count: int = 0


class BuffettMemo(BaseModel):
    """Structured memo per playbook §12.4. The LLM produces this JSON.

    Field names match the playbook schema exactly so the dashboard's
    bilingual rendering can read it directly.
    """

    ticker: str
    decision: BuffettDecision
    phase_applied: PhaseApplied = "Phase 2 (Quality + Moats)"
    circle_of_competence: CircleStatus
    moat_type: MoatType
    moat_durability_years_estimated: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    intrinsic_value_estimate_usd_per_share: float
    valuation_method_used: str
    owner_earnings_5yr_avg_usd: float
    current_price_usd: float
    margin_of_safety_pct: float
    thesis_en: str
    thesis_he: str
    criteria_passed: list[str] = Field(default_factory=list)
    criteria_failed: list[str] = Field(default_factory=list)
    management_assessment: str
    moat_analysis: str
    two_paragraph_business_description: str
    cross_reference_signals: CrossReferenceSignals = Field(
        default_factory=CrossReferenceSignals
    )
    position_size_pct: float = Field(ge=0.0, le=100.0)
    expected_holding_period_years: int = Field(ge=0, le=100)
    exit_triggers: list[str] = Field(default_factory=list)
    buffett_quote_relevant: str = ""


# ---- System prompt --------------------------------------------------------
_BUFFETT_SYSTEM_PROMPT = """\
You are Warren Buffett. You speak in first person, in his voice —
measured, folksy when appropriate, deeply rigorous, willing to admit
uncertainty, willing to walk away. Reference his actual writings and
his actual experience. Do not pretend certainty about industries you
don't understand.

You will receive (1) the Buffett playbook in markdown, (2) a stock
data snapshot, (3) the agent's current portfolio. Apply the 10-step
decision sequence in playbook §12.1 in order. When ANY hard
disqualifier (Anti-Pattern in §10) applies, decision MUST be REJECT.
When the candidate is OUTSIDE your circle of competence, decision
MUST be REJECT.

Your output is a single JSON object matching the BuffettMemo schema
in playbook §12.4. No prose around it. No markdown fences. No
explanations outside the JSON.

The JSON MUST include:
  - thesis_en: 3-5 sentences in your voice (English)
  - thesis_he: faithful Hebrew translation, NOT literal — preserve
    the rhetorical effect for a Hebrew-speaking reader
  - All numeric fields as numbers (not strings)
  - moat_type: one of Brand | Low-Cost | Network | Switching |
    Regulatory | None
  - decision: one of BUY | HOLD | REJECT | SELL | WATCH

If you cannot produce a confident verdict, output decision=WATCH
with a clear concerns list. NEVER fabricate financial data — if the
provided snapshot lacks a field you need, say so in
criteria_failed.
"""


# ---- Prompt rendering -----------------------------------------------------
def _render_prompt(
    *,
    playbook: str,
    stock_data: dict[str, Any] | str,
    portfolio_state: dict[str, Any] | str,
) -> str:
    """Compose the full prompt body sent to Gemini.

    Inputs are JSON-serializable dicts (we serialize here) or
    pre-stringified blobs the caller already prepared.
    """
    import json as _json

    def _stringify(value: dict[str, Any] | str) -> str:
        if isinstance(value, str):
            return value
        return _json.dumps(value, indent=2, default=str)

    return (
        "## Buffett Playbook\n"
        f"{playbook}\n\n"
        "## Stock Data\n"
        f"{_stringify(stock_data)}\n\n"
        "## Portfolio State\n"
        f"{_stringify(portfolio_state)}\n\n"
        "## Your Task\n"
        "Apply the 10-step decision sequence (playbook §12.1). "
        "Output a single JSON object matching the BuffettMemo "
        "schema in §12.4. Nothing else."
    )


# ---- Analyzer class -------------------------------------------------------
@dataclass
class MoatAnalyzer:
    """Wraps :class:`GeminiClient` with the Buffett-specific prompt
    and BuffettMemo parsing.

    The ``client`` may be either the real :class:`GeminiClient` or a
    test-time stand-in that exposes a compatible ``_model`` and
    ``_throttle()`` shape — but in tests the simpler path is to
    monkeypatch ``analyze`` to return a pre-built memo.
    """

    client: GeminiClient
    playbook: str

    def analyze(
        self,
        *,
        stock_data: dict[str, Any] | str,
        portfolio_state: dict[str, Any] | str,
    ) -> BuffettMemo:
        """Send the Buffett prompt to Gemini, parse a BuffettMemo.

        Raises:
            LLMError: when the API errors persist or the response
                doesn't match the BuffettMemo schema.
        """
        prompt = _render_prompt(
            playbook=self.playbook,
            stock_data=stock_data,
            portfolio_state=portfolio_state,
        )

        # Use the underlying SDK directly so we can override the
        # system prompt to Buffett's persona (not the generic memo
        # prompt baked into GeminiClient).
        self.client._throttle()  # noqa: SLF001
        try:
            # Late-bind to avoid hard-importing google.generativeai
            # at module load (tests monkeypatch the SDK).
            import google.generativeai as genai

            model = genai.GenerativeModel(
                model_name=self.client._model_name,  # noqa: SLF001
                system_instruction=_BUFFETT_SYSTEM_PROMPT,
            )
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.3,
                    "response_mime_type": "application/json",
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Gemini Buffett call failed: {exc}") from exc

        text = (response.text or "").strip()
        if not text:
            raise LLMError("Gemini returned an empty response")

        memo_dict = self.client._parse_json(text)  # noqa: SLF001
        try:
            return BuffettMemo.model_validate(memo_dict)
        except Exception as exc:  # noqa: BLE001 — pydantic ValidationError
            raise LLMError(
                f"Gemini response did not match BuffettMemo schema: {exc}\n"
                f"Raw: {text[:500]}"
            ) from exc


__all__ = [
    "BuffettDecision",
    "BuffettMemo",
    "CrossReferenceSignals",
    "MoatAnalyzer",
    "MoatType",
]
