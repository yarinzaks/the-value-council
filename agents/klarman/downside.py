"""Gemini-powered "what could go wrong" downside analyzer.

Per playbook §4.3 + §12, Klarman's signature analytical question
fires BEFORE evaluating upside:

  *"If we review the reasons for failure two years later, what will
   we be discussing?"*

Every BUY decision must include 3-5 specific failure scenarios with
probabilities and price impacts, and verify the cumulative permanent-
loss probability is acceptable for the position size.

This module wraps Gemini with the Klarman persona system prompt and
a ``KlarmanMemo`` Pydantic schema mirroring playbook §12.4. Like the
other hybrid agents:

  * **Live mode**: each top quant candidate gets a memo with explicit
    what-could-go-wrong scenarios, scaling-out plan, reverse triggers,
    and bilingual thesis_en/thesis_he. REJECT verdicts veto quant.
  * **Backtest mode**: NEVER — lookahead bias + free-tier quota.
    Documented in ``run_full_market_validation.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.exceptions import LLMError
from core.llm.gemini_client import GeminiClient
from core.logger import get_logger

logger = get_logger("agents.klarman.downside")


KlarmanDecision = Literal["BUY", "HOLD", "REJECT", "SELL", "TRIM", "WATCH"]
AssetType = Literal[
    "Public Equity",
    "Distressed Debt",
    "Special Situation",
    "Real Estate",
    "Bankruptcy Claim",
    "Other",
]
ValuationMethod = Literal[
    "DCF",
    "NAV",
    "Sum-of-Parts",
    "Recovery Analysis",
    "Scenario-Weighted",
]
CatalystStrength = Literal["Strong", "Acceptable", "Weak", "None"]
CapitalStructurePosition = Literal[
    "Senior Secured",
    "Senior Unsecured",
    "Subordinated",
    "Equity",
    "N/A",
]
DiversificationDimension = Literal[
    "Asset class", "Catalyst type", "Geography", "Time horizon"
]


# ---- Sub-schemas ---------------------------------------------------------
class FailureScenario(BaseModel):
    """One row in the what-could-go-wrong table (playbook §4.3)."""

    scenario: str
    probability_pct: float = Field(ge=0.0, le=100.0)
    estimated_price_impact_pct: float


class Catalyst(BaseModel):
    description: str
    expected_timeline_months: int = Field(ge=0, le=120)
    strength: CatalystStrength


class ScalingOutPlan(BaseModel):
    first_trim_at_price: float
    first_trim_pct_of_position: float = Field(ge=0.0, le=100.0)
    second_trim_at_price: float
    second_trim_pct_of_position: float = Field(ge=0.0, le=100.0)
    full_exit_at_price: float


class KlarmanMemo(BaseModel):
    """Structured memo per playbook §12.4."""

    ticker: str
    decision: KlarmanDecision
    asset_type: AssetType
    valuation_method_used: ValuationMethod
    intrinsic_value_estimate_usd: float
    current_price_usd: float
    margin_of_safety_pct: float
    what_could_go_wrong: list[FailureScenario] = Field(default_factory=list)
    cumulative_permanent_loss_probability_pct: float = Field(ge=0.0, le=100.0)
    catalyst: Catalyst
    capital_structure_position: CapitalStructurePosition
    confidence: float = Field(ge=0.0, le=1.0)
    thesis_en: str
    thesis_he: str
    criteria_passed: list[str] = Field(default_factory=list)
    criteria_failed: list[str] = Field(default_factory=list)
    diversification_dimension: DiversificationDimension
    position_size_pct: float = Field(ge=0.0, le=100.0)
    expected_holding_period_months: int = Field(ge=0, le=600)
    scaling_out_plan: ScalingOutPlan
    reverse_triggers: list[str] = Field(default_factory=list)
    klarman_quote_relevant: str = ""


# ---- System prompt -------------------------------------------------------
_KLARMAN_SYSTEM_PROMPT = """\
You are Seth Klarman. You speak in first person, in his voice —
measured, risk-focused, never excited, deeply analytical, willing to
walk away, comfortable with cash, deeply skeptical of consensus.
Reference his actual writings ("Margin of Safety", Baupost letters)
and his actual experience.

ALWAYS ask "what could go wrong" BEFORE "what could go right".
Generate at least 3 specific failure scenarios with probabilities and
price impacts. If the cumulative permanent-loss probability exceeds
40%, decision MUST be REJECT regardless of upside.

You will receive (1) the Klarman playbook, (2) a stock data snapshot
including the agent's quant valuation, (3) the agent's current
portfolio. Apply the 11-step decision sequence in playbook §12.1 in
order. When ANY hard disqualifier (Anti-Pattern in §10) applies,
decision MUST be REJECT.

Your output is a single JSON object matching the KlarmanMemo schema
in playbook §12.4. No prose around it. No markdown fences.

The JSON MUST include:
  - what_could_go_wrong: at least 3 specific scenarios with
    probabilities and estimated price impacts
  - cumulative_permanent_loss_probability_pct: explicit number
  - catalyst: description + timeline_months + strength
  - scaling_out_plan: first_trim, second_trim, full_exit price levels
  - reverse_triggers: explicit thesis-breaking conditions
  - thesis_en: 3-5 sentences in your voice
  - thesis_he: faithful Hebrew translation, NOT literal — preserve
    the rhetorical effect for a Hebrew-speaking reader
  - decision: one of BUY | HOLD | REJECT | SELL | TRIM | WATCH

Cash is a position. NEVER force-deploy when nothing qualifies.
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
        "## Klarman Playbook\n"
        f"{playbook}\n\n"
        "## Stock + Valuation Data\n"
        f"{_stringify(stock_data)}\n\n"
        "## Portfolio State\n"
        f"{_stringify(portfolio_state)}\n\n"
        "## Your Task\n"
        "Apply the 11-step decision sequence (playbook §12.1). "
        "Output a single JSON object matching the KlarmanMemo schema "
        "in §12.4. Nothing else."
    )


@dataclass
class DownsideAnalyzer:
    """Wraps :class:`GeminiClient` with the Klarman-specific prompt
    and KlarmanMemo parsing. Live mode only.
    """

    client: GeminiClient
    playbook: str

    def analyze(
        self,
        *,
        stock_data: dict[str, Any] | str,
        portfolio_state: dict[str, Any] | str,
    ) -> KlarmanMemo:
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
                system_instruction=_KLARMAN_SYSTEM_PROMPT,
            )
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.3,
                    "response_mime_type": "application/json",
                },
            )
        except Exception as exc:
            raise LLMError(f"Gemini Klarman call failed: {exc}") from exc

        text = (response.text or "").strip()
        if not text:
            raise LLMError("Gemini returned an empty response")

        memo_dict = self.client._parse_json(text)
        try:
            return KlarmanMemo.model_validate(memo_dict)
        except Exception as exc:
            raise LLMError(
                f"Gemini response did not match KlarmanMemo schema: {exc}\n"
                f"Raw: {text[:500]}"
            ) from exc


__all__ = [
    "AssetType",
    "CapitalStructurePosition",
    "Catalyst",
    "CatalystStrength",
    "DiversificationDimension",
    "DownsideAnalyzer",
    "FailureScenario",
    "KlarmanDecision",
    "KlarmanMemo",
    "ScalingOutPlan",
    "ValuationMethod",
]
