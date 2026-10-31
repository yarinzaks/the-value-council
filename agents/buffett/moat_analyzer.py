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
    runs. Calling the model inside a backtest creates two problems —
    (a) lookahead bias (the model knows the future), (b) infeasible
    runtime and cost across every candidate on every rebalance date.
    Documented honest limit.

For unit tests, stub ``complete_memo`` on the client — see
``tests/test_moat_analyzer.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from agents.evidence_rules import with_evidence_rules
from core.llm.claude_client import ClaudeClient
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
#: The playbook, headed exactly as the prompt used to head it.
#:
#: It moved out of the user message and into a cached system block
#: without changing a byte of what the model reads. The cache matches
#: on an exact prefix and an agent's playbook is identical for every
#: candidate in a run, so carrying it afresh in each call was most of
#: what a shadow run cost.
def _reference(playbook: str) -> str:
    return f"## Buffett Playbook\n{playbook}"


@dataclass
class MoatAnalyzer:
    """Wraps :class:`ClaudeClient` with the Buffett persona prompt.

    The ``client`` may be the real :class:`ClaudeClient` or any
    stand-in exposing ``complete_memo`` — one method now, rather than
    the three internals this used to reach for.
    """

    client: ClaudeClient
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
            stock_data=stock_data,
            portfolio_state=portfolio_state,
        )

        # Use the underlying SDK directly so we can override the
        # system prompt to Buffett's persona (not the generic memo
        # prompt baked into ClaudeClient).
        # The persona and the schema are this analyzer's whole
        # contribution. The transport, the retries, the truncation check
        # and the validation all belong to the client, which is why no
        # analyzer imports a provider SDK any more.
        return self.client.complete_memo(
            system=with_evidence_rules(_BUFFETT_SYSTEM_PROMPT),
            prompt=prompt,
            reference=_reference(self.playbook),
            schema=BuffettMemo,
        )


__all__ = [
    "BuffettDecision",
    "BuffettMemo",
    "CrossReferenceSignals",
    "MoatAnalyzer",
    "MoatType",
]
