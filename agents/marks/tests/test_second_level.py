"""Unit tests for the LLM second-level analyzer."""

from __future__ import annotations

import json
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
    c = MagicMock()
    c._model_name = "gemini-2.5-flash"
    c._throttle = MagicMock()
    c._parse_json = MagicMock(side_effect=_parse_json_side_effect)
    return c


def _parse_json_side_effect(text: str) -> dict[str, Any]:
    import re

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError(f"no JSON object: {text[:80]}")
    return json.loads(text[start : end + 1])


def _build_response(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    return r


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
    def test_happy_path(self, fake_client: MagicMock) -> None:
        analyzer = SecondLevelAnalyzer(client=fake_client, playbook="PB")
        fake_client._sdk.models.generate_content.return_value = _build_response(
            json.dumps(SAMPLE_MEMO_JSON)
        )

        memo = analyzer.analyze(
            stock_data={"ticker": "ENERGY-BOND-X"},
            portfolio_state={"posture": "Cool"},
        )

        assert memo.ticker == "ENERGY-BOND-X"
        assert memo.decision == "BUY"
        fake_client._throttle.assert_called_once()

    def test_empty_response_raises(self, fake_client: MagicMock) -> None:
        analyzer = SecondLevelAnalyzer(client=fake_client, playbook="PB")
        fake_client._sdk.models.generate_content.return_value = _build_response("")

        with pytest.raises(LLMError, match="empty"):
            analyzer.analyze(stock_data={}, portfolio_state={})

    def test_invalid_schema_raises(self, fake_client: MagicMock) -> None:
        analyzer = SecondLevelAnalyzer(client=fake_client, playbook="PB")
        bad = dict(SAMPLE_MEMO_JSON)
        bad["decision"] = "GARBAGE"
        fake_client._sdk.models.generate_content.return_value = _build_response(json.dumps(bad))

        with pytest.raises(LLMError, match="schema"):
            analyzer.analyze(stock_data={}, portfolio_state={})

    def test_sdk_error_wrapped(self, fake_client: MagicMock) -> None:
        analyzer = SecondLevelAnalyzer(client=fake_client, playbook="PB")
        fake_client._sdk.models.generate_content.side_effect = RuntimeError("API down")

        with pytest.raises(LLMError, match="failed"):
            analyzer.analyze(stock_data={}, portfolio_state={})

    def test_request_carries_the_persona_and_sampling(self, fake_client: MagicMock) -> None:
        """What makes this call Marks's rather than the generic memo.

        The system instruction and temperature are set per request now;
        before, they were bound when the model object was built.
        """
        analyzer = SecondLevelAnalyzer(client=fake_client, playbook="PB")
        fake_client._sdk.models.generate_content.return_value = _build_response(
            json.dumps(SAMPLE_MEMO_JSON)
        )

        analyzer.analyze(stock_data={}, portfolio_state={})

        kwargs = fake_client._sdk.models.generate_content.call_args.kwargs
        assert kwargs["model"] == fake_client._model_name
        assert kwargs["config"].system_instruction == with_evidence_rules(_MARKS_SYSTEM_PROMPT)
        assert kwargs["config"].temperature == 0.4
        assert kwargs["config"].response_mime_type == "application/json"
