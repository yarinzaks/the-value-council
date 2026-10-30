"""Unit tests for the LLM moat analyzer.

The real Gemini call is mocked — we verify (a) BuffettMemo schema
validation, (b) prompt composition, (c) error propagation. No real
API key is needed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.buffett.moat_analyzer import (
    _BUFFETT_SYSTEM_PROMPT,
    BuffettMemo,
    CrossReferenceSignals,
    MoatAnalyzer,
    _render_prompt,
)
from agents.evidence_rules import with_evidence_rules
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
    """A client exposing the single method an analyzer now calls."""
    c = MagicMock()
    c.complete_memo = MagicMock()
    return c


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
    """What is left of this analyzer once the transport moved out.

    Render the prompt, name the persona, name the schema, and stay out
    of the way of an error. The empty-response, unparsable-JSON and
    API-failure cases used to be tested here against a stubbed vendor
    SDK — they belong to the client now and are covered once in
    core/llm/tests/test_claude_client.py, instead of five near-copies
    each covering one analyzer.
    """

    def a_memo(self):
        return BuffettMemo.model_validate(SAMPLE_MEMO_JSON)

    def test_the_memo_comes_back(self, fake_client: MagicMock) -> None:
        memo = self.a_memo()
        fake_client.complete_memo.return_value = memo

        got = MoatAnalyzer(client=fake_client, playbook="PB").analyze(
            stock_data={"ticker": "QUALITY"}, portfolio_state={"cash": 1000}
        )

        assert got is memo

    def test_the_request_names_the_persona_and_the_schema(
        self, fake_client: MagicMock
    ) -> None:
        """Buffett's whole contribution to the call."""
        fake_client.complete_memo.return_value = self.a_memo()

        MoatAnalyzer(client=fake_client, playbook="PB").analyze(
            stock_data={}, portfolio_state={}
        )

        sent = fake_client.complete_memo.call_args.kwargs
        assert sent["system"] == with_evidence_rules(_BUFFETT_SYSTEM_PROMPT)
        assert sent["schema"] is BuffettMemo
        assert "temperature" not in sent, "the parameter no longer exists"
        assert "config" not in sent, "no vendor config object survives"

    def test_the_prompt_carries_the_playbook_and_the_stock(
        self, fake_client: MagicMock
    ) -> None:
        fake_client.complete_memo.return_value = self.a_memo()

        MoatAnalyzer(client=fake_client, playbook="THE PLAYBOOK TEXT").analyze(
            stock_data={"ticker": "ZZZZ"}, portfolio_state={"cash_usd": 4321}
        )

        prompt = fake_client.complete_memo.call_args.kwargs["prompt"]
        assert "THE PLAYBOOK TEXT" in prompt
        assert "ZZZZ" in prompt
        assert "4321" in prompt

    def test_an_error_from_the_client_is_not_swallowed(
        self, fake_client: MagicMock
    ) -> None:
        """A truncated memo must reach the caller, not a half-filled one."""
        fake_client.complete_memo.side_effect = LLMError("memo is incomplete")

        with pytest.raises(LLMError, match="incomplete"):
            MoatAnalyzer(client=fake_client, playbook="PB").analyze(
                stock_data={}, portfolio_state={}
            )

    def test_this_module_imports_no_provider_sdk(self) -> None:
        """The property the swap existed to produce.

        An analyzer that reaches into a vendor SDK is an analyzer that
        has to be rewritten on the next provider change. This is the
        cheap guard against that creeping back.
        """
        import importlib
        from pathlib import Path as _Path

        source = _Path(importlib.import_module("agents.buffett.moat_analyzer").__file__).read_text()
        for marker in ("google.genai", "genai", "_sdk", "_parse_json"):
            assert marker not in source, f"{marker} is back in agents.buffett.moat_analyzer"
