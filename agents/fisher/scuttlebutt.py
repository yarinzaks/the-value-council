"""Gemini-powered scuttlebutt + 15-point analyzer — Fisher's qual leg.

Per playbook §5 + §12, scuttlebutt is Fisher's most original
contribution: gather qualitative intelligence from competitors,
suppliers, customers, former employees, trade press, and patent
filings. In a 2026 paper-trading agent the modern equivalents are
Glassdoor, G2/Trustpilot, industry analyst posts, USPTO, and earnings
call Q&A transcripts.

This module wraps Gemini with the Fisher persona system prompt and
the ``FisherMemo`` schema (playbook §12.4). Live mode only — backtest
runs WITHOUT the LLM (lookahead bias + free-tier quota; documented
in ``run_full_market_validation.py``).

The memo includes:

  * Full 15-point checklist scoring (each PASS/FAIL/UNCLEAR)
  * Total score out of 15
  * Integrity check (Point 15 must be PASS — non-negotiable)
  * Scuttlebutt synthesis across 5 source categories
  * Tier classification (A/B/C or Reject)
  * Bilingual thesis_en/thesis_he

The strategy in :mod:`quality_growth` uses the LLM verdict to veto
quant winners whose qualitative signal is bad — including any
candidate that fails the integrity check regardless of quant score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from agents.evidence_rules import with_evidence_rules
from core.exceptions import LLMError
from core.llm.gemini_client import GeminiClient
from core.logger import get_logger

logger = get_logger("agents.fisher.scuttlebutt")


FisherDecision = Literal["BUY", "HOLD", "REJECT", "TRIM", "RARE_SELL", "WATCH"]
PointVerdict = Literal["PASS", "FAIL", "UNCLEAR"]
TierClassification = Literal["Tier A", "Tier B", "Tier C", "Reject"]
ValuationAssessment = Literal[
    "Premium-Justified", "Fair", "Stretched", "Excessive"
]
ScuttlebuttAssessment = Literal["POSITIVE", "MIXED", "NEGATIVE"]
PatentStrength = Literal["Strong", "Adequate", "Weak"]
HoldingPeriod = Literal["10+", "5-10", "3-5"]


# ---- Sub-schemas (playbook §12.4) ----------------------------------------
class FifteenPointsScore(BaseModel):
    """Per-point verdict for all 15 Fisher points."""

    point_1_market_potential: PointVerdict
    point_2_product_development: PointVerdict
    point_3_rd_effectiveness: PointVerdict
    point_4_sales_organization: PointVerdict
    point_5_profit_margins: PointVerdict
    point_6_margin_maintenance: PointVerdict
    point_7_labor_relations: PointVerdict
    point_8_executive_relations: PointVerdict
    point_9_depth_of_management: PointVerdict
    point_10_accounting_controls: PointVerdict
    point_11_industry_specific: PointVerdict
    point_12_long_range_outlook: PointVerdict
    point_13_equity_financing: PointVerdict
    point_14_communication_candor: PointVerdict
    point_15_management_integrity: PointVerdict


class ScuttlebuttResearch(BaseModel):
    """Qualitative intelligence synthesis (playbook §4.2)."""

    customer_signal: str
    former_employee_signal: str
    competitor_signal: str
    industry_analyst_signal: str
    patent_filing_strength: PatentStrength
    overall_scuttlebutt_assessment: ScuttlebuttAssessment


class FisherMemo(BaseModel):
    """Structured memo per playbook §12.4."""

    ticker: str
    decision: FisherDecision
    fifteen_points_score: FifteenPointsScore
    total_score_out_of_15: int = Field(ge=0, le=15)
    integrity_check_passed: bool
    scuttlebutt_research: ScuttlebuttResearch
    tier_classification: TierClassification
    rationale_for_tier: str
    intrinsic_value_estimate_usd: float
    current_price_usd: float
    valuation_assessment: ValuationAssessment
    pe_ratio: float
    expected_growth_rate_5yr_pct: float
    rd_to_revenue_pct: float
    operating_margin_pct: float
    industry_avg_operating_margin_pct: float
    customer_retention_pct: float = Field(ge=0.0, le=100.0)
    employee_turnover_pct: float = Field(ge=0.0, le=100.0)
    share_count_change_5yr_pct: float
    confidence: float = Field(ge=0.0, le=1.0)
    thesis_en: str
    thesis_he: str
    criteria_passed: list[str] = Field(default_factory=list)
    criteria_failed: list[str] = Field(default_factory=list)
    position_size_pct: float = Field(ge=0.0, le=100.0)
    expected_holding_period_years: HoldingPeriod
    reverse_triggers: list[str] = Field(default_factory=list)
    fisher_quote_relevant: str = ""


# ---- System prompt -------------------------------------------------------
_FISHER_SYSTEM_PROMPT = """\
You are Philip Fisher. You speak in first person, in his voice —
patient, qualitative-first, deeply respectful of management quality,
willing to pay full prices for outstanding companies, focused on
multi-decade holding periods. Reference his actual writings ("Common
Stocks and Uncommon Profits", "Conservative Investors Sleep Well")
and his actual experience (Motorola 49 years, Texas Instruments,
Dow Chemical, Hewlett-Packard).

THE 15-POINT CHECKLIST IS YOUR SCAFFOLDING. SCUTTLEBUTT IS YOUR
METHOD. INTEGRITY IS NON-NEGOTIABLE.

You will receive (1) the Fisher playbook, (2) a stock data snapshot
including the agent's quant 5-point quality score, (3) the agent's
current portfolio. Apply the 9-step decision sequence in playbook
§12.1 in order.

Mandatory ordering:
  1. Integrity Check (Point 15) FIRST — if anything less than
     completely satisfactory, decision MUST be REJECT.
  2. Score all 15 points (PASS/FAIL/UNCLEAR).
  3. Synthesize scuttlebutt — if NEGATIVE, decision MUST be REJECT
     regardless of financial scores.
  4. Require total score ≥ 12/15 AND Point 15 = PASS for any BUY.
  5. Tier-classify: A (perfect or near-perfect, mega-trend), B
     (12-13/15, good industry), C (high-upside speculative), Reject.

Your output is a single JSON object matching the FisherMemo schema
in playbook §12.4. No prose around it. No markdown fences.

The JSON MUST include:
  - fifteen_points_score: per-point PASS/FAIL/UNCLEAR for all 15
  - integrity_check_passed: explicit bool
  - scuttlebutt_research: 5-source synthesis
  - tier_classification + rationale_for_tier
  - reverse_triggers: explicit thesis-breaking conditions
  - thesis_en: 3-5 sentences in your voice
  - thesis_he: faithful Hebrew translation, NOT literal — preserve
    the rhetorical effect for a Hebrew-speaking reader
  - decision: one of BUY | HOLD | REJECT | TRIM | RARE_SELL | WATCH

NEVER fabricate financial data. NEVER lower the integrity bar.
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
        "## Fisher Playbook\n"
        f"{playbook}\n\n"
        "## Stock + Quant Score Data\n"
        f"{_stringify(stock_data)}\n\n"
        "## Portfolio State\n"
        f"{_stringify(portfolio_state)}\n\n"
        "## Your Task\n"
        "Apply the 9-step decision sequence (playbook §12.1). "
        "Output a single JSON object matching the FisherMemo schema "
        "in §12.4. Nothing else."
    )


@dataclass
class ScuttlebuttAnalyzer:
    """Wraps :class:`GeminiClient` with the Fisher persona prompt and
    FisherMemo parsing. Live mode only.
    """

    client: GeminiClient
    playbook: str

    def analyze(
        self,
        *,
        stock_data: dict[str, Any] | str,
        portfolio_state: dict[str, Any] | str,
    ) -> FisherMemo:
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
                system_instruction=with_evidence_rules(_FISHER_SYSTEM_PROMPT),
            )
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.3,
                    "response_mime_type": "application/json",
                },
            )
        except Exception as exc:
            raise LLMError(f"Gemini Fisher call failed: {exc}") from exc

        text = (response.text or "").strip()
        if not text:
            raise LLMError("Gemini returned an empty response")

        memo_dict = self.client._parse_json(text)
        try:
            return FisherMemo.model_validate(memo_dict)
        except Exception as exc:
            raise LLMError(
                f"Gemini response did not match FisherMemo schema: {exc}\n"
                f"Raw: {text[:500]}"
            ) from exc


__all__ = [
    "FifteenPointsScore",
    "FisherDecision",
    "FisherMemo",
    "HoldingPeriod",
    "PatentStrength",
    "PointVerdict",
    "ScuttlebuttAnalyzer",
    "ScuttlebuttAssessment",
    "ScuttlebuttResearch",
    "TierClassification",
    "ValuationAssessment",
]
