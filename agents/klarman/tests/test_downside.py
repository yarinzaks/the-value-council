"""Unit tests for the LLM downside analyzer."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.evidence_rules import with_evidence_rules
from agents.klarman.downside import (
    _KLARMAN_SYSTEM_PROMPT,
    DownsideAnalyzer,
    KlarmanMemo,
)
from core.exceptions import LLMError

SAMPLE_MEMO_JSON: dict[str, Any] = {
    "ticker": "REAL-ESTATE-X",
    "decision": "BUY",
    "asset_type": "Real Estate",
    "valuation_method_used": "NAV",
    "intrinsic_value_estimate_usd": 58.0,
    "current_price_usd": 34.0,
    "margin_of_safety_pct": 41.0,
    "what_could_go_wrong": [
        {
            "scenario": "office vacancy continues rising",
            "probability_pct": 35.0,
            "estimated_price_impact_pct": -25.0,
        },
        {
            "scenario": "major tenant bankruptcy",
            "probability_pct": 20.0,
            "estimated_price_impact_pct": -15.0,
        },
        {
            "scenario": "refinancing failure",
            "probability_pct": 15.0,
            "estimated_price_impact_pct": -30.0,
        },
    ],
    "cumulative_permanent_loss_probability_pct": 25.0,
    "catalyst": {
        "description": (
            "Three properties in announced disposition program "
            "totaling $200M+ over 18 months."
        ),
        "expected_timeline_months": 18,
        "strength": "Acceptable",
    },
    "capital_structure_position": "Equity",
    "confidence": 0.7,
    "thesis_en": (
        "Forced sellers are creating a 41% discount to my conservative "
        "NAV. The catalyst is property dispositions over 18 months. "
        "Reverse trigger if office vacancy exceeds 25%."
    ),
    "thesis_he": (
        "מוכרים בכפייה יוצרים הנחה של 41% מול ה־NAV השמרני שלי. "
        "הזרז הוא מכירות נכסים על פני 18 חודשים. הטריגר ההפוך אם "
        "התפוסה במשרדים תרד מתחת ל־75%."
    ),
    "criteria_passed": ["MoS > 30%", "identifiable catalyst"],
    "criteria_failed": [],
    "diversification_dimension": "Asset class",
    "position_size_pct": 5.0,
    "expected_holding_period_months": 36,
    "scaling_out_plan": {
        "first_trim_at_price": 48.0,
        "first_trim_pct_of_position": 25.0,
        "second_trim_at_price": 55.0,
        "second_trim_pct_of_position": 25.0,
        "full_exit_at_price": 58.0,
    },
    "reverse_triggers": [
        "vacancy > 25% in core markets",
        "property sales fail to materialize at announced timeline + 6 mo",
    ],
    "klarman_quote_relevant": (
        "You don't reduce risk by buying 'safer' companies. You "
        "reduce risk by paying a 'safer' price."
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
class TestKlarmanMemoSchema:
    def test_full_payload_validates(self) -> None:
        memo = KlarmanMemo.model_validate(SAMPLE_MEMO_JSON)
        assert memo.ticker == "REAL-ESTATE-X"
        assert memo.decision == "BUY"
        assert memo.asset_type == "Real Estate"
        assert memo.valuation_method_used == "NAV"
        assert len(memo.what_could_go_wrong) == 3
        assert memo.scaling_out_plan.first_trim_at_price == 48.0
        assert memo.thesis_he.startswith("מוכרים")

    def test_invalid_decision_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["decision"] = "MAYBE"
        with pytest.raises(Exception):
            KlarmanMemo.model_validate(bad)

    def test_invalid_asset_type_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["asset_type"] = "Crypto"
        with pytest.raises(Exception):
            KlarmanMemo.model_validate(bad)

    def test_invalid_valuation_method_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["valuation_method_used"] = "Astrology"
        with pytest.raises(Exception):
            KlarmanMemo.model_validate(bad)

    def test_invalid_catalyst_strength_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad = dict(bad)
        bad["catalyst"] = dict(bad["catalyst"])
        bad["catalyst"]["strength"] = "Magical"
        with pytest.raises(Exception):
            KlarmanMemo.model_validate(bad)

    def test_loss_probability_out_of_range_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["cumulative_permanent_loss_probability_pct"] = 150.0
        with pytest.raises(Exception):
            KlarmanMemo.model_validate(bad)


# ---- Analyzer end-to-end --------------------------------------------------
class TestAnalyze:
    def test_happy_path(self, fake_client: MagicMock) -> None:
        analyzer = DownsideAnalyzer(client=fake_client, playbook="PB")
        fake_client._sdk.models.generate_content.return_value = _build_response(
            json.dumps(SAMPLE_MEMO_JSON)
        )

        memo = analyzer.analyze(
            stock_data={"ticker": "REAL-ESTATE-X"},
            portfolio_state={"cash": 1000},
        )

        assert memo.ticker == "REAL-ESTATE-X"
        assert memo.decision == "BUY"
        fake_client._throttle.assert_called_once()

    def test_empty_response_raises(self, fake_client: MagicMock) -> None:
        analyzer = DownsideAnalyzer(client=fake_client, playbook="PB")
        fake_client._sdk.models.generate_content.return_value = _build_response("")

        with pytest.raises(LLMError, match="empty"):
            analyzer.analyze(stock_data={}, portfolio_state={})

    def test_invalid_schema_raises(self, fake_client: MagicMock) -> None:
        analyzer = DownsideAnalyzer(client=fake_client, playbook="PB")
        bad = dict(SAMPLE_MEMO_JSON)
        bad["decision"] = "GARBAGE"
        fake_client._sdk.models.generate_content.return_value = _build_response(json.dumps(bad))

        with pytest.raises(LLMError, match="schema"):
            analyzer.analyze(stock_data={}, portfolio_state={})

    def test_sdk_error_wrapped(self, fake_client: MagicMock) -> None:
        analyzer = DownsideAnalyzer(client=fake_client, playbook="PB")
        fake_client._sdk.models.generate_content.side_effect = RuntimeError("API down")

        with pytest.raises(LLMError, match="failed"):
            analyzer.analyze(stock_data={}, portfolio_state={})

    def test_request_carries_the_persona_and_sampling(self, fake_client: MagicMock) -> None:
        """What makes this call Klarman's rather than the generic memo.

        The system instruction and temperature are set per request now;
        before, they were bound when the model object was built.
        """
        analyzer = DownsideAnalyzer(client=fake_client, playbook="PB")
        fake_client._sdk.models.generate_content.return_value = _build_response(
            json.dumps(SAMPLE_MEMO_JSON)
        )

        analyzer.analyze(stock_data={}, portfolio_state={})

        kwargs = fake_client._sdk.models.generate_content.call_args.kwargs
        assert kwargs["model"] == fake_client._model_name
        assert kwargs["config"].system_instruction == with_evidence_rules(_KLARMAN_SYSTEM_PROMPT)
        assert kwargs["config"].temperature == 0.3
        assert kwargs["config"].response_mime_type == "application/json"
