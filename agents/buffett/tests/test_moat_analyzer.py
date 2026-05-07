"""Unit tests for the LLM moat analyzer.

The real Gemini call is mocked — we verify (a) BuffettMemo schema
validation, (b) prompt composition, (c) error propagation. No real
API key is needed.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agents.buffett.moat_analyzer import (
    BuffettMemo,
    CrossReferenceSignals,
    MoatAnalyzer,
    _render_prompt,
)
from core.exceptions import LLMError


# ---- Sample LLM output -----------------------------------------------------
SAMPLE_MEMO_JSON: dict[str, Any] = {
    "ticker": "KO",
    "decision": "BUY",
    "phase_applied": "Phase 2 (Quality + Moats)",
    "circle_of_competence": "INSIDE",
    "moat_type": "Brand",
    "moat_durability_years_estimated": 30,
    "confidence": 0.85,
    "intrinsic_value_estimate_usd_per_share": 75.0,
    "valuation_method_used": "Owner Earnings DCF",
    "owner_earnings_5yr_avg_usd": 9_500_000_000.0,
    "current_price_usd": 60.0,
    "margin_of_safety_pct": 20.0,
    "thesis_en": (
        "Coca-Cola is the textbook brand moat. The combination of "
        "global distribution and unmatched brand recognition gives it "
        "pricing power year after year. I bought it in 1988 and have "
        "never sold a share. At today's price the dividend yield alone "
        "approaches my purchase basis. This is a Phase 2 hold."
    ),
    "thesis_he": (
        "קוקה־קולה היא דוגמה קלאסית לחפיר מותג. שילוב של הפצה "
        "גלובלית ומותג בלתי תחליפי מעניק לה כוח תמחור שנה אחר שנה. "
        "קניתי ב־1988 ומעולם לא מכרתי. במחיר הנוכחי תשואת הדיבידנד "
        "לבדה קרובה לעלות הקנייה שלי. זוהי החזקה של 'שלב 2'."
    ),
    "criteria_passed": [
        "Berkshire Criterion 1: Size $260B ✓",
        "Berkshire Criterion 2: 30+ years of positive earnings ✓",
        "Berkshire Criterion 3: ROE 38% / D/E 0.4 ✓",
        "Berkshire Criterion 4: Tenured CEO with track record ✓",
        "Berkshire Criterion 5: Two-paragraph test passes ✓",
        "Berkshire Criterion 6: 20% MoS ✓",
    ],
    "criteria_failed": [],
    "management_assessment": (
        "James Quincey has run Coke since 2017 with capital allocation "
        "consistent with the franchise."
    ),
    "moat_analysis": (
        "Brand moat measured by 5% annual price increases without "
        "volume loss; global distribution network of 200+ countries; "
        "shelf-space economics no challenger can replicate at scale."
    ),
    "two_paragraph_business_description": (
        "Coca-Cola sells concentrate to bottlers worldwide who "
        "manufacture and distribute beverages under Coca-Cola brands.\n"
        "Revenue comes from concentrate sales plus licensing; the "
        "asset-light model produces high returns on capital."
    ),
    "cross_reference_signals": {
        "berkshire_holds_position": True,
        "other_quality_investors_holding": ["Sequoia"],
        "insider_buying_90d_count": 1,
        "insider_selling_90d_count": 0,
    },
    "position_size_pct": 12.5,
    "expected_holding_period_years": 20,
    "exit_triggers": [
        "Sustained loss of pricing power",
        "Competitor capturing >10% global market share",
    ],
    "buffett_quote_relevant": (
        "If you aren't willing to own a stock for ten years, don't "
        "even think about owning it for ten minutes."
    ),
}


# ---- Fixtures --------------------------------------------------------------
@pytest.fixture
def fake_client() -> MagicMock:
    """A bare-minimum stand-in for GeminiClient. Only the attributes
    the analyzer touches are stubbed out — no real SDK initialization.
    """
    c = MagicMock()
    c._model_name = "gemini-2.5-flash"
    c._throttle = MagicMock()
    c._parse_json = MagicMock(side_effect=_parse_json_side_effect)
    return c


def _parse_json_side_effect(text: str) -> dict[str, Any]:
    """Mirror the real GeminiClient._parse_json behavior."""
    # Strip markdown fences if present.
    import re

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError(f"no JSON object: {text[:80]}")
    return json.loads(text[start : end + 1])


# ---- Schema tests ----------------------------------------------------------
class TestBuffettMemoSchema:
    def test_full_payload_validates(self) -> None:
        memo = BuffettMemo.model_validate(SAMPLE_MEMO_JSON)
        assert memo.ticker == "KO"
        assert memo.decision == "BUY"
        assert memo.moat_type == "Brand"
        assert memo.thesis_he.startswith("קוקה")  # bilingual present

    def test_invalid_decision_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["decision"] = "MAYBE"
        with pytest.raises(Exception):
            BuffettMemo.model_validate(bad)

    def test_invalid_moat_type_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["moat_type"] = "Magic"
        with pytest.raises(Exception):
            BuffettMemo.model_validate(bad)

    def test_confidence_out_of_range_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["confidence"] = 1.5
        with pytest.raises(Exception):
            BuffettMemo.model_validate(bad)

    def test_default_cross_ref_signals(self) -> None:
        # CrossReferenceSignals has all defaults — bare init works.
        sig = CrossReferenceSignals()
        assert sig.berkshire_holds_position is False
        assert sig.other_quality_investors_holding == []


# ---- Prompt rendering tests ------------------------------------------------
class TestRenderPrompt:
    def test_includes_all_three_sections(self) -> None:
        out = _render_prompt(
            playbook="PLAYBOOK_BODY",
            stock_data={"ticker": "KO"},
            portfolio_state={"cash": 1000},
        )
        assert "Buffett Playbook" in out
        assert "PLAYBOOK_BODY" in out
        assert "Stock Data" in out
        assert "KO" in out
        assert "Portfolio State" in out
        assert "1000" in out

    def test_pre_stringified_inputs(self) -> None:
        out = _render_prompt(
            playbook="PB",
            stock_data="raw stock blob",
            portfolio_state="raw portfolio blob",
        )
        assert "raw stock blob" in out
        assert "raw portfolio blob" in out


# ---- End-to-end analyze() with mocked SDK ---------------------------------
def _build_response(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    return r


class TestAnalyze:
    """Patches ``google.generativeai.GenerativeModel`` directly. The
    SDK is already imported by the time tests run; ``patch.dict`` on
    sys.modules is too late, so we patch the attribute on the live
    module instead.
    """

    def test_happy_path(self, fake_client: MagicMock) -> None:
        analyzer = MoatAnalyzer(client=fake_client, playbook="PB")
        fake_model = MagicMock()
        fake_model.generate_content.return_value = _build_response(
            json.dumps(SAMPLE_MEMO_JSON)
        )

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ):
            memo = analyzer.analyze(
                stock_data={"ticker": "KO"},
                portfolio_state={"cash": 1000},
            )

        assert memo.ticker == "KO"
        assert memo.decision == "BUY"
        fake_client._throttle.assert_called_once()

    def test_empty_response_raises(self, fake_client: MagicMock) -> None:
        analyzer = MoatAnalyzer(client=fake_client, playbook="PB")
        fake_model = MagicMock()
        fake_model.generate_content.return_value = _build_response("")

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ):
            with pytest.raises(LLMError, match="empty"):
                analyzer.analyze(stock_data={}, portfolio_state={})

    def test_invalid_schema_raises(self, fake_client: MagicMock) -> None:
        analyzer = MoatAnalyzer(client=fake_client, playbook="PB")
        bad_payload = dict(SAMPLE_MEMO_JSON)
        bad_payload["decision"] = "NONSENSE"
        fake_model = MagicMock()
        fake_model.generate_content.return_value = _build_response(
            json.dumps(bad_payload)
        )

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ):
            with pytest.raises(LLMError, match="schema"):
                analyzer.analyze(stock_data={}, portfolio_state={})

    def test_sdk_error_wrapped_in_llm_error(self, fake_client: MagicMock) -> None:
        analyzer = MoatAnalyzer(client=fake_client, playbook="PB")
        fake_model = MagicMock()
        fake_model.generate_content.side_effect = RuntimeError("API down")

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ):
            with pytest.raises(LLMError, match="failed"):
                analyzer.analyze(stock_data={}, portfolio_state={})
