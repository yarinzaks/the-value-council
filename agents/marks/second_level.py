"""Gemini-powered second-level thinking — the qualitative leg.

Per playbook §3.1 / §12, Marks's signature concept is second-level
thinking: don't ask "is this a good company?" — ask "what does
consensus believe about this company, why might it be wrong, and what
non-consensus view am I taking?"

This module wraps Gemini with the Marks persona system prompt and a
``MarksMemo`` Pydantic schema mirroring playbook §12.4.

When this runs and when it does NOT — same pattern as the Buffett /
Lynch agents:

  * **Live mode**: each top quant candidate gets a memo with full
    second-level analysis, scenarios, posture context, and bilingual
    thesis_en/thesis_he. REJECT verdicts veto quant winners.
  * **Backtest mode**: NEVER — lookahead bias + free-tier quota burn.
    Documented in ``run_full_market_validation.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.exceptions import LLMError
from core.llm.gemini_client import GeminiClient
from core.logger import get_logger

logger = get_logger("agents.marks.second_level")


MarksDecision = Literal[
    "BUY", "HOLD", "REJECT", "SELL", "TRIM", "ADD", "WATCH"
]
AssetType = Literal[
    "Distressed Debt",
    "High-Yield Bond",
    "Public Equity",
    "Convertible",
    "Bank Loan",
    "Other",
]
MarketTemperatureScore = Literal["Cold", "Cool", "Neutral", "Warm", "Hot"]
CyclePhase = Literal[
    "Early-cycle", "Mid-cycle", "Late-cycle", "Distress", "Recovery"
]
PostureRecommendation = Literal[
    "Aggressive", "Active", "Selective", "Defensive", "Maximum-Cash"
]


# ---- Sub-schemas (playbook §12.4) ----------------------------------------
class SecondLevelThinking(BaseModel):
    consensus_view: str
    non_consensus_view: str
    why_consensus_might_be_wrong: str


class Scenario(BaseModel):
    scenario: str
    probability_pct: float = Field(ge=0.0, le=100.0)
    return_pct: float


class RiskAdjustedReturnAnalysis(BaseModel):
    scenarios: list[Scenario]
    expected_return_pct: float


class IDontKnowCheck(BaseModel):
    key_assumptions: list[str] = Field(default_factory=list)
    thesis_robust_to_multiple_futures: bool


class ScalingOutPlan(BaseModel):
    trim_at_price_levels: list[float] = Field(default_factory=list)
    full_exit_price: float | None = None
    reverse_trigger_price: float | None = None


class MarksMemo(BaseModel):
    """Structured memo per playbook §12.4."""

    ticker: str
    decision: MarksDecision
    asset_type: AssetType
    market_temperature_score: MarketTemperatureScore
    market_temperature_indicators: dict[str, str] = Field(default_factory=dict)
    current_cycle_phase_estimate: CyclePhase
    posture_recommendation: PostureRecommendation
    second_level_thinking: SecondLevelThinking
    risk_adjusted_return_analysis: RiskAdjustedReturnAnalysis
    i_dont_know_check: IDontKnowCheck
    confidence: float = Field(ge=0.0, le=1.0)
    thesis_en: str
    thesis_he: str
    criteria_passed: list[str] = Field(default_factory=list)
    criteria_failed: list[str] = Field(default_factory=list)
    position_size_pct: float = Field(ge=0.0, le=100.0)
    expected_holding_period_months: int = Field(ge=0, le=600)
    scaling_out_plan: ScalingOutPlan = Field(default_factory=ScalingOutPlan)
    marks_quote_relevant: str = ""


# ---- System prompt -------------------------------------------------------
_MARKS_SYSTEM_PROMPT = """\
You are Howard Marks. You speak in first person, in his voice — calm,
philosophical, deeply contextual, willing to acknowledge what you
don't know, focused on cycles and psychology more than security
analysis. Reference his actual writings (memos, "The Most Important
Thing", "Mastering the Market Cycle") and his actual experience.
ALWAYS assess where the pendulum is BEFORE evaluating individual
securities.

You will receive (1) the Marks playbook, (2) a stock data snapshot
including the agent's quant temperature assessment, (3) the agent's
current portfolio. Apply the 10-step decision sequence in playbook
§12.1 in order. When ANY hard disqualifier (Anti-Pattern in §10)
applies, decision MUST be REJECT.

Your output is a single JSON object matching the MarksMemo schema in
playbook §12.4. No prose around it. No markdown fences.

The JSON MUST include:
  - second_level_thinking: explicit consensus / non-consensus / why-
    might-consensus-be-wrong
  - risk_adjusted_return_analysis: at least 2 scenarios (probabilities
    summing to ~100%) with expected_return_pct
  - i_dont_know_check: list specific assumptions; flag whether the
    thesis works in multiple futures
  - thesis_en: 3-5 sentences in your voice
  - thesis_he: faithful Hebrew translation, NOT literal — preserve
    the rhetorical effect for a Hebrew-speaking reader
  - market_temperature_score: one of Cold | Cool | Neutral | Warm | Hot
  - decision: one of BUY | HOLD | REJECT | SELL | TRIM | ADD | WATCH

If the cycle is hot AND the candidate offers no margin of safety,
default to REJECT or WATCH — NEVER force a buy. Cash is a position.
NEVER fabricate financial data.
"""


def _render_prompt(
    *,
    playbook: str,
    stock_data: dict[str, Any] | str,
    portfolio_state: dict[str, Any] | str,
) -> str:
    import json as _json

    def _stringify(value: dict[str, Any] | str) -> str:
        if isinstance(value, str):
            return value
        return _json.dumps(value, indent=2, default=str)

    return (
        "## Marks Playbook\n"
        f"{playbook}\n\n"
        "## Stock + Cycle Data\n"
        f"{_stringify(stock_data)}\n\n"
        "## Portfolio State\n"
        f"{_stringify(portfolio_state)}\n\n"
        "## Your Task\n"
        "Apply the 10-step decision sequence (playbook §12.1). "
        "Output a single JSON object matching the MarksMemo schema "
        "in §12.4. Nothing else."
    )


@dataclass
class SecondLevelAnalyzer:
    """Wraps :class:`GeminiClient` with the Marks-specific prompt and
    MarksMemo parsing. Live mode only.
    """

    client: GeminiClient
    playbook: str

    def analyze(
        self,
        *,
        stock_data: dict[str, Any] | str,
        portfolio_state: dict[str, Any] | str,
    ) -> MarksMemo:
        prompt = _render_prompt(
            playbook=self.playbook,
            stock_data=stock_data,
            portfolio_state=portfolio_state,
        )

        self.client._throttle()
        try:
            import google.generativeai as genai

            model = genai.GenerativeModel(
                model_name=self.client._model_name,
                system_instruction=_MARKS_SYSTEM_PROMPT,
            )
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.4,
                    "response_mime_type": "application/json",
                },
            )
        except Exception as exc:
            raise LLMError(f"Gemini Marks call failed: {exc}") from exc

        text = (response.text or "").strip()
        if not text:
            raise LLMError("Gemini returned an empty response")

        memo_dict = self.client._parse_json(text)
        try:
            return MarksMemo.model_validate(memo_dict)
        except Exception as exc:
            raise LLMError(
                f"Gemini response did not match MarksMemo schema: {exc}\n"
                f"Raw: {text[:500]}"
            ) from exc


__all__ = [
    "AssetType",
    "CyclePhase",
    "IDontKnowCheck",
    "MarketTemperatureScore",
    "MarksDecision",
    "MarksMemo",
    "PostureRecommendation",
    "RiskAdjustedReturnAnalysis",
    "ScalingOutPlan",
    "Scenario",
    "SecondLevelAnalyzer",
    "SecondLevelThinking",
]
