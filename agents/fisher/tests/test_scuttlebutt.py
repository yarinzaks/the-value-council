"""Unit tests for the LLM scuttlebutt analyzer."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agents.fisher.scuttlebutt import (
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
    def test_happy_path(self, fake_client: MagicMock) -> None:
        analyzer = ScuttlebuttAnalyzer(client=fake_client, playbook="PB")
        fake_model = MagicMock()
        fake_model.generate_content.return_value = _build_response(
            json.dumps(SAMPLE_MEMO_JSON)
        )

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ):
            memo = analyzer.analyze(
                stock_data={"ticker": "QUALITY"},
                portfolio_state={"cash": 1000},
            )

        assert memo.ticker == "QUALITY"
        assert memo.decision == "BUY"
        fake_client._throttle.assert_called_once()

    def test_empty_response_raises(self, fake_client: MagicMock) -> None:
        analyzer = ScuttlebuttAnalyzer(client=fake_client, playbook="PB")
        fake_model = MagicMock()
        fake_model.generate_content.return_value = _build_response("")

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ), pytest.raises(LLMError, match="empty"):
            analyzer.analyze(stock_data={}, portfolio_state={})

    def test_invalid_schema_raises(self, fake_client: MagicMock) -> None:
        analyzer = ScuttlebuttAnalyzer(client=fake_client, playbook="PB")
        bad = dict(SAMPLE_MEMO_JSON)
        bad["decision"] = "GARBAGE"
        fake_model = MagicMock()
        fake_model.generate_content.return_value = _build_response(
            json.dumps(bad)
        )

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ), pytest.raises(LLMError, match="schema"):
            analyzer.analyze(stock_data={}, portfolio_state={})

    def test_sdk_error_wrapped(self, fake_client: MagicMock) -> None:
        analyzer = ScuttlebuttAnalyzer(client=fake_client, playbook="PB")
        fake_model = MagicMock()
        fake_model.generate_content.side_effect = RuntimeError("API down")

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ), pytest.raises(LLMError, match="failed"):
            analyzer.analyze(stock_data={}, portfolio_state={})
