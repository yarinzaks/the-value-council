"""Unit tests for the LLM scuttlebutt analyzer."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.evidence_rules import with_evidence_rules
from agents.fisher.scuttlebutt import (
    _FISHER_SYSTEM_PROMPT,
    FisherMemo,
    ScuttlebuttAnalyzer,
)
from core.exceptions import LLMError

SAMPLE_MEMO_JSON: dict[str, Any] = {
    "ticker": "QUALITY",
    "decision": "BUY",
    "fifteen_points_score": {
        "point_1_market_potential": "PASS",
        "point_2_product_development": "PASS",
        "point_3_rd_effectiveness": "PASS",
        "point_4_sales_organization": "PASS",
        "point_5_profit_margins": "PASS",
        "point_6_margin_maintenance": "PASS",
        "point_7_labor_relations": "PASS",
        "point_8_executive_relations": "PASS",
        "point_9_depth_of_management": "PASS",
        "point_10_accounting_controls": "PASS",
        "point_11_industry_specific": "PASS",
        "point_12_long_range_outlook": "PASS",
        "point_13_equity_financing": "PASS",
        "point_14_communication_candor": "PASS",
        "point_15_management_integrity": "PASS",
    },
    "total_score_out_of_15": 15,
    "integrity_check_passed": True,
    "scuttlebutt_research": {
        "customer_signal": (
            "Reviews praise reliability and customer service."
        ),
        "former_employee_signal": (
            "Glassdoor 4.3, engineering culture intact."
        ),
        "competitor_signal": (
            "Competitor Q&A indirectly acknowledges product strength."
        ),
        "industry_analyst_signal": (
            "Multiple analysts emphasize moat durability."
        ),
        "patent_filing_strength": "Strong",
        "overall_scuttlebutt_assessment": "POSITIVE",
    },
    "tier_classification": "Tier A",
    "rationale_for_tier": (
        "15/15 score, mega-trend industry, integrity confirmed."
    ),
    "intrinsic_value_estimate_usd": 250.0,
    "current_price_usd": 187.0,
    "valuation_assessment": "Premium-Justified",
    "pe_ratio": 28.0,
    "expected_growth_rate_5yr_pct": 17.0,
    "rd_to_revenue_pct": 18.0,
    "operating_margin_pct": 24.0,
    "industry_avg_operating_margin_pct": 16.0,
    "customer_retention_pct": 91.0,
    "employee_turnover_pct": 8.0,
    "share_count_change_5yr_pct": -4.0,
    "confidence": 0.85,
    "thesis_en": (
        "This is a Fisher-grade compounder. 15/15 on the checklist, "
        "scuttlebutt strongly positive, R&D effectiveness "
        "demonstrated, integrity unquestionable. The premium multiple "
        "is justified by quality. Multi-decade hold."
    ),
    "thesis_he": (
        "זוהי חברת איכות לפי פישר. 15/15 בצ'קליסט, סקאטלבאט חיובי "
        "מאוד, אפקטיביות מו\"פ מוכחת, יושרה ללא ספק. המכפיל הגבוה "
        "מוצדק על ידי האיכות. החזקה לעשורים."
    ),
    "criteria_passed": ["all 15 points", "scuttlebutt POSITIVE"],
    "criteria_failed": [],
    "position_size_pct": 12.0,
    "expected_holding_period_years": "10+",
    "reverse_triggers": [
        "15-point score drops below 11",
        "integrity question emerges",
        "R&D effectiveness declines",
    ],
    "fisher_quote_relevant": (
        "If the job has been correctly done when a common stock is "
        "purchased, the time to sell it is - almost never."
    ),
}


@pytest.fixture
def fake_client() -> MagicMock:
    """A client exposing the single method an analyzer now calls."""
    c = MagicMock()
    c.complete_memo = MagicMock()
    return c


# ---- Schema tests ---------------------------------------------------------
class TestFisherMemoSchema:
    def test_full_payload_validates(self) -> None:
        memo = FisherMemo.model_validate(SAMPLE_MEMO_JSON)
        assert memo.ticker == "QUALITY"
        assert memo.decision == "BUY"
        assert memo.tier_classification == "Tier A"
        assert memo.total_score_out_of_15 == 15
        assert memo.integrity_check_passed is True
        assert memo.fifteen_points_score.point_15_management_integrity == "PASS"
        assert memo.scuttlebutt_research.overall_scuttlebutt_assessment == "POSITIVE"
        assert memo.thesis_he.startswith("זוהי")

    def test_invalid_decision_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["decision"] = "MAYBE"
        with pytest.raises(Exception):
            FisherMemo.model_validate(bad)

    def test_invalid_tier_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["tier_classification"] = "Tier X"
        with pytest.raises(Exception):
            FisherMemo.model_validate(bad)

    def test_invalid_point_verdict_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["fifteen_points_score"] = dict(bad["fifteen_points_score"])
        bad["fifteen_points_score"]["point_1_market_potential"] = "GOOD"
        with pytest.raises(Exception):
            FisherMemo.model_validate(bad)

    def test_score_out_of_range_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["total_score_out_of_15"] = 16
        with pytest.raises(Exception):
            FisherMemo.model_validate(bad)


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
        return FisherMemo.model_validate(SAMPLE_MEMO_JSON)

    def test_the_memo_comes_back(self, fake_client: MagicMock) -> None:
        memo = self.a_memo()
        fake_client.complete_memo.return_value = memo

        got = ScuttlebuttAnalyzer(client=fake_client, playbook="PB").analyze(
            stock_data={"ticker": "QUALITY"}, portfolio_state={"cash": 1000}
        )

        assert got is memo

    def test_the_request_names_the_persona_and_the_schema(
        self, fake_client: MagicMock
    ) -> None:
        """Fisher's whole contribution to the call."""
        fake_client.complete_memo.return_value = self.a_memo()

        ScuttlebuttAnalyzer(client=fake_client, playbook="PB").analyze(
            stock_data={}, portfolio_state={}
        )

        sent = fake_client.complete_memo.call_args.kwargs
        assert sent["system"] == with_evidence_rules(_FISHER_SYSTEM_PROMPT)
        assert sent["schema"] is FisherMemo
        assert "temperature" not in sent, "the parameter no longer exists"
        assert "config" not in sent, "no vendor config object survives"

    def test_the_prompt_carries_the_playbook_and_the_stock(
        self, fake_client: MagicMock
    ) -> None:
        fake_client.complete_memo.return_value = self.a_memo()

        ScuttlebuttAnalyzer(client=fake_client, playbook="THE PLAYBOOK TEXT").analyze(
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
            ScuttlebuttAnalyzer(client=fake_client, playbook="PB").analyze(
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

        source = _Path(importlib.import_module("agents.fisher.scuttlebutt").__file__).read_text()
        for marker in ("google.genai", "genai", "_sdk", "_parse_json"):
            assert marker not in source, f"{marker} is back in agents.fisher.scuttlebutt"
