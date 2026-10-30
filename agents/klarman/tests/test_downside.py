"""Unit tests for the LLM downside analyzer."""

from __future__ import annotations

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
    """A client exposing the single method an analyzer now calls."""
    c = MagicMock()
    c.complete_memo = MagicMock()
    return c


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
    """What is left of this analyzer once the transport moved out.

    Render the prompt, name the persona, name the schema, and stay out
    of the way of an error. The empty-response, unparsable-JSON and
    API-failure cases used to be tested here against a stubbed vendor
    SDK — they belong to the client now and are covered once in
    core/llm/tests/test_claude_client.py, instead of five near-copies
    each covering one analyzer.
    """

    def a_memo(self):
        return KlarmanMemo.model_validate(SAMPLE_MEMO_JSON)

    def test_the_memo_comes_back(self, fake_client: MagicMock) -> None:
        memo = self.a_memo()
        fake_client.complete_memo.return_value = memo

        got = DownsideAnalyzer(client=fake_client, playbook="PB").analyze(
            stock_data={"ticker": "QUALITY"}, portfolio_state={"cash": 1000}
        )

        assert got is memo

    def test_the_request_names_the_persona_and_the_schema(
        self, fake_client: MagicMock
    ) -> None:
        """Klarman's whole contribution to the call."""
        fake_client.complete_memo.return_value = self.a_memo()

        DownsideAnalyzer(client=fake_client, playbook="PB").analyze(
            stock_data={}, portfolio_state={}
        )

        sent = fake_client.complete_memo.call_args.kwargs
        assert sent["system"] == with_evidence_rules(_KLARMAN_SYSTEM_PROMPT)
        assert sent["schema"] is KlarmanMemo
        assert "temperature" not in sent, "the parameter no longer exists"
        assert "config" not in sent, "no vendor config object survives"

    def test_the_prompt_carries_the_playbook_and_the_stock(
        self, fake_client: MagicMock
    ) -> None:
        fake_client.complete_memo.return_value = self.a_memo()

        DownsideAnalyzer(client=fake_client, playbook="THE PLAYBOOK TEXT").analyze(
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
            DownsideAnalyzer(client=fake_client, playbook="PB").analyze(
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

        source = _Path(importlib.import_module("agents.klarman.downside").__file__).read_text()
        for marker in ("google.genai", "genai", "_sdk", "_parse_json"):
            assert marker not in source, f"{marker} is back in agents.klarman.downside"
