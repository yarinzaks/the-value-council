"""Unit tests for the LLM second-level analyzer."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.evidence_rules import with_evidence_rules
from agents.marks.second_level import (
    _MARKS_SYSTEM_PROMPT,
    MarksMemo,
    SecondLevelAnalyzer,
)
from core.exceptions import LLMError

SAMPLE_MEMO_JSON: dict[str, Any] = {
    "ticker": "ENERGY-BOND-X",
    "decision": "BUY",
    "asset_type": "High-Yield Bond",
    "market_temperature_score": "Cool",
    "market_temperature_indicators": {
        "yield_spread": "wide",
        "credit_defaults": "rising",
    },
    "current_cycle_phase_estimate": "Late-cycle",
    "posture_recommendation": "Active",
    "second_level_thinking": {
        "consensus_view": "Energy companies will default en masse.",
        "non_consensus_view": (
            "Senior secured recovery still 80%+; the panic is "
            "overshooting."
        ),
        "why_consensus_might_be_wrong": (
            "Forced sellers can't distinguish among credits."
        ),
    },
    "risk_adjusted_return_analysis": {
        "scenarios": [
            {
                "scenario": "commodities recover",
                "probability_pct": 50.0,
                "return_pct": 28.0,
            },
            {
                "scenario": "company restructures",
                "probability_pct": 35.0,
                "return_pct": 9.0,
            },
            {
                "scenario": "deeper commodity collapse",
                "probability_pct": 15.0,
                "return_pct": -12.0,
            },
        ],
        "expected_return_pct": 15.4,
    },
    "i_dont_know_check": {
        "key_assumptions": [
            "commodity prices stabilize within 18 months",
            "regulatory regime unchanged",
        ],
        "thesis_robust_to_multiple_futures": True,
    },
    "confidence": 0.78,
    "thesis_en": (
        "The pendulum has swung toward fear in energy credit. "
        "First-level fear is creating my opportunity. Senior "
        "secured recovery is acceptable in three of four scenarios."
    ),
    "thesis_he": (
        "המטוטלת נטתה לפחד באשראי האנרגיה. הפחד הראשוני יוצר עבורי "
        "הזדמנות. ההחלמה של החוב הבכיר מקובלת בשלושה מתוך ארבעה "
        "תרחישים."
    ),
    "criteria_passed": ["spread > 5%", "senior secured cushion"],
    "criteria_failed": [],
    "position_size_pct": 5.0,
    "expected_holding_period_months": 18,
    "scaling_out_plan": {
        "trim_at_price_levels": [0.92, 1.00],
        "full_exit_price": 1.00,
        "reverse_trigger_price": 0.65,
    },
    "marks_quote_relevant": (
        "You can't predict. You can prepare."
    ),
}


@pytest.fixture
def fake_client() -> MagicMock:
    """A client exposing the single method an analyzer now calls."""
    c = MagicMock()
    c.complete_memo = MagicMock()
    return c


# ---- Schema tests ---------------------------------------------------------
class TestMarksMemoSchema:
    def test_full_payload_validates(self) -> None:
        memo = MarksMemo.model_validate(SAMPLE_MEMO_JSON)
        assert memo.ticker == "ENERGY-BOND-X"
        assert memo.decision == "BUY"
        assert memo.market_temperature_score == "Cool"
        assert memo.thesis_he.startswith("המטוטלת")
        assert (
            len(memo.risk_adjusted_return_analysis.scenarios) == 3
        )
        assert memo.scaling_out_plan.trim_at_price_levels == [0.92, 1.00]

    def test_invalid_decision_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["decision"] = "MAYBE"
        with pytest.raises(Exception):
            MarksMemo.model_validate(bad)

    def test_invalid_asset_type_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["asset_type"] = "Magic Beans"
        with pytest.raises(Exception):
            MarksMemo.model_validate(bad)

    def test_invalid_temperature_score_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["market_temperature_score"] = "Lukewarm"
        with pytest.raises(Exception):
            MarksMemo.model_validate(bad)

    def test_confidence_out_of_range_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["confidence"] = 1.5
        with pytest.raises(Exception):
            MarksMemo.model_validate(bad)


# ---- Analyzer end-to-end --------------------------------------------------


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
        return MarksMemo.model_validate(SAMPLE_MEMO_JSON)

    def test_the_memo_comes_back(self, fake_client: MagicMock) -> None:
        memo = self.a_memo()
        fake_client.complete_memo.return_value = memo

        got = SecondLevelAnalyzer(client=fake_client, playbook="PB").analyze(
            stock_data={"ticker": "QUALITY"}, portfolio_state={"cash": 1000}
        )

        assert got is memo

    def test_the_request_names_the_persona_and_the_schema(
        self, fake_client: MagicMock
    ) -> None:
        """Marks's whole contribution to the call."""
        fake_client.complete_memo.return_value = self.a_memo()

        SecondLevelAnalyzer(client=fake_client, playbook="PB").analyze(
            stock_data={}, portfolio_state={}
        )

        sent = fake_client.complete_memo.call_args.kwargs
        assert sent["system"] == with_evidence_rules(_MARKS_SYSTEM_PROMPT)
        assert sent["schema"] is MarksMemo
        assert "temperature" not in sent, "the parameter no longer exists"
        assert "config" not in sent, "no vendor config object survives"

    def test_the_prompt_carries_the_playbook_and_the_stock(
        self, fake_client: MagicMock
    ) -> None:
        fake_client.complete_memo.return_value = self.a_memo()

        SecondLevelAnalyzer(client=fake_client, playbook="THE PLAYBOOK TEXT").analyze(
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
            SecondLevelAnalyzer(client=fake_client, playbook="PB").analyze(
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

        source = _Path(importlib.import_module("agents.marks.second_level").__file__).read_text()
        for marker in ("google.genai", "genai", "_sdk", "_parse_json"):
            assert marker not in source, f"{marker} is back in agents.marks.second_level"
