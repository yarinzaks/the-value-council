"""Lynch's six-category classifier — heuristic + Gemini LLM.

Per playbook §4, every Lynch candidate fits exactly one of:

  1. Slow Grower    — large + mature; growth ≤ 5%; high dividend
  2. Stalwart       — established; growth 10-12%; recession-resilient
  3. Fast Grower    — small/mid-cap; growth 20-50%; the engine
  4. Cyclical       — earnings tied to economic cycles
  5. Turnaround     — distressed; recovery in progress
  6. Asset Play     — hidden assets exceeding market cap

This module provides BOTH paths:

  * :func:`heuristic_classify` — pure-Python rules over PIT data.
    Used by the BACKTEST. Limited to the 3 categories that have
    clean quantitative signatures (Slow Grower / Stalwart / Fast
    Grower). Cyclicals + Turnarounds + Asset Plays require
    qualitative judgment Lynch did "by hand" — they fall to the
    LLM in live mode.

  * :class:`CategoryClassifier` — Gemini-backed full 6-category
    classification with bilingual memo. Used in LIVE mode only.
    Same lookahead-bias rationale as the Buffett moat analyzer
    (see ``run_full_market_validation.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.exceptions import LLMError
from core.llm.gemini_client import GeminiClient
from core.logger import get_logger

logger = get_logger("agents.lynch.category_classifier")


LynchCategory = Literal[
    "Slow Grower",
    "Stalwart",
    "Fast Grower",
    "Cyclical",
    "Turnaround",
    "Asset Play",
]
LynchDecision = Literal["BUY", "HOLD", "REJECT", "SELL", "WATCH"]
FcfTrend = Literal["positive_growing", "positive_stable", "negative"]
MarginTrend = Literal["expanding", "stable", "contracting"]
SssTrend = Literal["accelerating", "stable", "decelerating", "n/a"]


# ---- Heuristic classification thresholds ---------------------------------
#: Above this 5-yr EPS CAGR, we tag as Fast Grower.
FAST_GROWER_MIN_GROWTH_PCT: float = 20.0

#: Lynch becomes "suspicious of sustainability" above 50%; we cap our
#: classification but still allow it (the strategy layer can soft-flag).
FAST_GROWER_MAX_GROWTH_PCT: float = 50.0

#: Stalwart growth band per playbook §4.2.
STALWART_MIN_GROWTH_PCT: float = 10.0
STALWART_MAX_GROWTH_PCT: float = 20.0

#: Slow grower band per §4.1.
SLOW_GROWER_MAX_GROWTH_PCT: float = 5.0
SLOW_GROWER_MIN_YIELD_PCT: float = 4.0

#: Mid-cap floor for "established" Stalwarts. Below this we treat
#: a 10-20% grower as a Fast Grower instead.
STALWART_MIN_MARKET_CAP_USD: float = 5_000_000_000.0


def heuristic_classify(
    *,
    growth_rate_pct: float | None,
    dividend_yield_pct: float | None,
    market_cap_usd: float | None,
) -> LynchCategory | None:
    """Classify with simple rules. Returns None when the candidate
    doesn't cleanly fit any of the 3 quant-friendly categories
    (Slow / Stalwart / Fast Grower).

    Cyclical / Turnaround / Asset Play classification is deliberately
    NOT attempted here — those need qualitative judgment Lynch did
    by hand. The LLM picks them up in live mode.
    """
    if growth_rate_pct is None:
        return None

    g = growth_rate_pct
    yld = dividend_yield_pct or 0.0
    mcap = market_cap_usd or 0.0

    # Slow Grower: low growth + meaningful dividend.
    if g <= SLOW_GROWER_MAX_GROWTH_PCT and yld >= SLOW_GROWER_MIN_YIELD_PCT:
        return "Slow Grower"

    # Fast Grower: high growth (with optional small/mid-cap bias).
    if g >= FAST_GROWER_MIN_GROWTH_PCT:
        return "Fast Grower"

    # Stalwart: 10-20% growth AND established size.
    if (
        STALWART_MIN_GROWTH_PCT <= g < STALWART_MAX_GROWTH_PCT
        and mcap >= STALWART_MIN_MARKET_CAP_USD
    ):
        return "Stalwart"

    # 5-20% small-cap growth: ambiguous — could be early Stalwart or
    # decelerating Fast Grower. We classify as Fast Grower (the
    # generous interpretation; the PEG floor will reject if too
    # expensive).
    if STALWART_MIN_GROWTH_PCT <= g < STALWART_MAX_GROWTH_PCT:
        return "Fast Grower"

    # 5-10% with no dividend: not a Lynch fit.
    return None


# ---- LLM memo schema (playbook §12.4) ------------------------------------
class FundamentalsCheck(BaseModel):
    """Structured fundamentals slice mirroring playbook §12.4."""

    earnings_consistency_years: int = Field(ge=0, le=30)
    debt_to_equity: float
    free_cash_flow_trend: FcfTrend
    profit_margin_trend: MarginTrend
    same_store_sales_trend: SssTrend = "n/a"
    insider_ownership_pct: float = Field(ge=0.0, le=100.0)
    insider_buying_90d_count: int = Field(ge=0)
    institutional_ownership_pct: float = Field(ge=0.0, le=100.0)


class FastGrowerData(BaseModel):
    regions_proven_in: int = Field(ge=0)
    ten_bagger_potential: Literal["high", "medium", "low"]


class CyclicalData(BaseModel):
    cycle_position: Literal["trough", "early_recovery", "mid_cycle", "peak"]
    capacity_utilization_pct: float = Field(ge=0.0, le=100.0)


class TurnaroundData(BaseModel):
    specific_problem: str
    management_plan: str
    liquidity_runway_months: int = Field(ge=0, le=120)


class AssetPlayData(BaseModel):
    hidden_asset_description: str
    asset_value_vs_market_cap_pct: float


class CategorySpecificData(BaseModel):
    fast_grower: FastGrowerData | None = None
    cyclical: CyclicalData | None = None
    turnaround: TurnaroundData | None = None
    asset_play: AssetPlayData | None = None


class LynchMemo(BaseModel):
    """Structured memo per playbook §12.4."""

    ticker: str
    decision: LynchDecision
    lynch_category: LynchCategory
    two_minute_drill: str
    peg_ratio: float
    pegy_ratio: float
    growth_rate_5yr_pct: float
    pe_ratio: float
    dividend_yield_pct: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    intrinsic_value_estimate_usd: float
    current_price_usd: float
    margin_of_safety_pct: float
    thesis_en: str
    thesis_he: str
    criteria_passed: list[str] = Field(default_factory=list)
    criteria_failed: list[str] = Field(default_factory=list)
    fundamentals_check: FundamentalsCheck
    category_specific_data: CategorySpecificData = Field(
        default_factory=CategorySpecificData
    )
    position_size_pct: float = Field(ge=0.0, le=100.0)
    expected_holding_period_months: int = Field(ge=0, le=600)
    exit_triggers: list[str] = Field(default_factory=list)
    lynch_quote_relevant: str = ""


# ---- LLM prompt -----------------------------------------------------------
_LYNCH_SYSTEM_PROMPT = """\
You are Peter Lynch. You speak in first person, in his voice —
practical, observation-rich, full of specific examples, willing to
admit when you don't understand a business, energetic about finding
hidden growth at a reasonable price. Reference his actual writings
and his actual experience. EVERY analysis routes through the six
categories: Slow Grower, Stalwart, Fast Grower, Cyclical,
Turnaround, Asset Play.

You will receive (1) the Lynch playbook, (2) a stock data snapshot,
(3) the agent's current portfolio. Apply the 9-step decision
sequence in playbook §12.1 in order. When ANY hard disqualifier
(Anti-Pattern in §10) applies, decision MUST be REJECT. When the
candidate fails the two-minute drill (you cannot describe what it
does and why it should grow in two minutes), decision MUST be
REJECT.

Your output is a single JSON object matching the LynchMemo schema
in playbook §12.4. No prose around it. No markdown fences.

The JSON MUST include:
  - lynch_category: exactly one of the six
  - two_minute_drill: a real two-minute monologue, not a label
  - peg_ratio + pegy_ratio: numbers, computed from given inputs
  - thesis_en: 3-5 sentences in your voice
  - thesis_he: faithful Hebrew translation, NOT literal — preserve
    the rhetorical effect for a Hebrew-speaking reader
  - All numeric fields as numbers (not strings)
  - decision: one of BUY | HOLD | REJECT | SELL | WATCH

If you cannot confidently classify the candidate into a category,
default to WATCH and explain in concerns. NEVER fabricate financial
data.
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
        "## Lynch Playbook\n"
        f"{playbook}\n\n"
        "## Stock Data\n"
        f"{_stringify(stock_data)}\n\n"
        "## Portfolio State\n"
        f"{_stringify(portfolio_state)}\n\n"
        "## Your Task\n"
        "Apply the 9-step decision sequence (playbook §12.1). "
        "Output a single JSON object matching the LynchMemo schema "
        "in §12.4. Nothing else."
    )


@dataclass
class CategoryClassifier:
    """Wraps :class:`GeminiClient` with the Lynch-specific prompt and
    LynchMemo parsing.

    Live mode only. Tests stub the underlying SDK call directly.
    """

    client: GeminiClient
    playbook: str

    def classify(
        self,
        *,
        stock_data: dict[str, Any] | str,
        portfolio_state: dict[str, Any] | str,
    ) -> LynchMemo:
        prompt = _render_prompt(
            playbook=self.playbook,
            stock_data=stock_data,
            portfolio_state=portfolio_state,
        )

        self.client._throttle()  # noqa: SLF001
        try:
            import google.generativeai as genai

            model = genai.GenerativeModel(
                model_name=self.client._model_name,  # noqa: SLF001
                system_instruction=_LYNCH_SYSTEM_PROMPT,
            )
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.4,
                    "response_mime_type": "application/json",
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Gemini Lynch call failed: {exc}") from exc

        text = (response.text or "").strip()
        if not text:
            raise LLMError("Gemini returned an empty response")

        memo_dict = self.client._parse_json(text)  # noqa: SLF001
        try:
            return LynchMemo.model_validate(memo_dict)
        except Exception as exc:  # noqa: BLE001 — pydantic ValidationError
            raise LLMError(
                f"Gemini response did not match LynchMemo schema: {exc}\n"
                f"Raw: {text[:500]}"
            ) from exc


__all__ = [
    "AssetPlayData",
    "CategoryClassifier",
    "CategorySpecificData",
    "CyclicalData",
    "FAST_GROWER_MAX_GROWTH_PCT",
    "FAST_GROWER_MIN_GROWTH_PCT",
    "FastGrowerData",
    "FundamentalsCheck",
    "LynchCategory",
    "LynchDecision",
    "LynchMemo",
    "SLOW_GROWER_MAX_GROWTH_PCT",
    "SLOW_GROWER_MIN_YIELD_PCT",
    "STALWART_MAX_GROWTH_PCT",
    "STALWART_MIN_GROWTH_PCT",
    "STALWART_MIN_MARKET_CAP_USD",
    "TurnaroundData",
    "heuristic_classify",
]
