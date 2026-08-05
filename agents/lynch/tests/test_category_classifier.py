"""Unit tests for the heuristic + LLM category classifier."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agents.lynch.category_classifier import (
    CategoryClassifier,
    LynchMemo,
    heuristic_classify,
)
from core.exceptions import LLMError


# ---- Heuristic tests -------------------------------------------------------
class TestHeuristicClassify:
    def test_fast_grower(self) -> None:
        out = heuristic_classify(
            growth_rate_pct=25.0,
            dividend_yield_pct=0.5,
            market_cap_usd=2_000_000_000.0,
        )
        assert out == "Fast Grower"

    def test_stalwart(self) -> None:
        out = heuristic_classify(
            growth_rate_pct=12.0,
            dividend_yield_pct=2.0,
            market_cap_usd=20_000_000_000.0,  # ≥ $5B
        )
        assert out == "Stalwart"

    def test_slow_grower(self) -> None:
        out = heuristic_classify(
            growth_rate_pct=3.0,
            dividend_yield_pct=5.0,
            market_cap_usd=50_000_000_000.0,
        )
        assert out == "Slow Grower"

    def test_mid_growth_small_cap_is_fast_grower(self) -> None:
        # 12% growth but small-cap → Fast Grower (not "established"
        # enough for Stalwart).
        out = heuristic_classify(
            growth_rate_pct=12.0,
            dividend_yield_pct=0.5,
            market_cap_usd=1_000_000_000.0,  # below $5B Stalwart floor
        )
        assert out == "Fast Grower"

    def test_low_growth_no_dividend_no_category(self) -> None:
        # 4% growth, 1% yield → not slow grower (yield too low),
        # not stalwart (growth too low), not fast grower → None.
        out = heuristic_classify(
            growth_rate_pct=4.0,
            dividend_yield_pct=1.0,
            market_cap_usd=2_000_000_000.0,
        )
        assert out is None

    def test_none_growth_returns_none(self) -> None:
        assert (
            heuristic_classify(
                growth_rate_pct=None,
                dividend_yield_pct=2.0,
                market_cap_usd=1e9,
            )
            is None
        )


# ---- LLM memo schema -------------------------------------------------------
SAMPLE_MEMO_JSON: dict[str, Any] = {
    "ticker": "WMT",
    "decision": "BUY",
    "lynch_category": "Fast Grower",
    "two_minute_drill": (
        "Walmart runs about 4,700 stores in the United States and "
        "thousands more abroad. Same-store sales are growing low "
        "single-digits, but unit count and e-commerce are growing "
        "faster than the market thinks. The store-within-a-store "
        "and Walmart+ subscription model give them new growth runways. "
        "What could go wrong: Amazon, plus margin compression from "
        "wage inflation."
    ),
    "peg_ratio": 0.85,
    "pegy_ratio": 0.78,
    "growth_rate_5yr_pct": 22.0,
    "pe_ratio": 18.7,
    "dividend_yield_pct": 1.4,
    "confidence": 0.78,
    "intrinsic_value_estimate_usd": 200.0,
    "current_price_usd": 168.0,
    "margin_of_safety_pct": 16.0,
    "thesis_en": (
        "Walmart is exactly the kind of Fast Grower I love — proven "
        "concept, expanding at a measured pace, with the stomach to "
        "weather store-traffic cycles. The store-within-a-store and "
        "Walmart+ model gives them a new growth lever institutions "
        "haven't priced in. PEG of 0.85 is in my buy zone."
    ),
    "thesis_he": (
        "וולמארט היא בדיוק הסוג של 'מצמיחי הצמיחה המהירה' שאני אוהב — "
        "מודל מוכח, התרחבות מבוקרת, ועצבים לעבור מחזורי תנועה בחנויות. "
        "ה־Walmart+ פותח להם ציר צמיחה חדש שמוסדיים עדיין לא מתמחרים. "
        "PEG של 0.85 הוא בתוך אזור הקנייה שלי."
    ),
    "criteria_passed": ["PEG 0.85 < 1.0", "5yr CAGR 22% > 20%"],
    "criteria_failed": [],
    "fundamentals_check": {
        "earnings_consistency_years": 10,
        "debt_to_equity": 0.42,
        "free_cash_flow_trend": "positive_growing",
        "profit_margin_trend": "stable",
        "same_store_sales_trend": "stable",
        "insider_ownership_pct": 1.2,
        "insider_buying_90d_count": 2,
        "institutional_ownership_pct": 35.0,
    },
    "category_specific_data": {
        "fast_grower": {
            "regions_proven_in": 4,
            "ten_bagger_potential": "medium",
        },
    },
    "position_size_pct": 4.0,
    "expected_holding_period_months": 36,
    "exit_triggers": [
        "growth decelerates below 15% for 2 quarters",
        "PEG > 1.5",
        "institutional ownership > 60%",
    ],
    "lynch_quote_relevant": "Invest in what you know.",
}


class TestLynchMemoSchema:
    def test_full_payload_validates(self) -> None:
        memo = LynchMemo.model_validate(SAMPLE_MEMO_JSON)
        assert memo.ticker == "WMT"
        assert memo.decision == "BUY"
        assert memo.lynch_category == "Fast Grower"
        assert memo.thesis_he.startswith("וולמארט")
        assert memo.fundamentals_check.earnings_consistency_years == 10
        assert memo.category_specific_data.fast_grower is not None
        assert memo.category_specific_data.fast_grower.regions_proven_in == 4

    def test_invalid_category_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["lynch_category"] = "Magic Beans"
        with pytest.raises(Exception):
            LynchMemo.model_validate(bad)

    def test_invalid_decision_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["decision"] = "MAYBE"
        with pytest.raises(Exception):
            LynchMemo.model_validate(bad)

    def test_confidence_out_of_range_rejected(self) -> None:
        bad = dict(SAMPLE_MEMO_JSON)
        bad["confidence"] = 1.5
        with pytest.raises(Exception):
            LynchMemo.model_validate(bad)


# ---- LLM end-to-end with mocked SDK ---------------------------------------
@pytest.fixture
def fake_client() -> MagicMock:
    c = MagicMock()
    c._model_name = "gemini-2.5-flash"
    c._throttle = MagicMock()
    c._parse_json = MagicMock(side_effect=_parse_json_side_effect)
    return c


def _parse_json_side_effect(text: str) -> dict[str, Any]:
    """Mirror GeminiClient._parse_json behavior."""
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


class TestClassify:
    def test_happy_path(self, fake_client: MagicMock) -> None:
        analyzer = CategoryClassifier(client=fake_client, playbook="PB")
        fake_model = MagicMock()
        fake_model.generate_content.return_value = _build_response(
            json.dumps(SAMPLE_MEMO_JSON)
        )

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ):
            memo = analyzer.classify(
                stock_data={"ticker": "WMT"},
                portfolio_state={"cash": 1000},
            )

        assert memo.ticker == "WMT"
        assert memo.lynch_category == "Fast Grower"
        fake_client._throttle.assert_called_once()

    def test_empty_response_raises(self, fake_client: MagicMock) -> None:
        analyzer = CategoryClassifier(client=fake_client, playbook="PB")
        fake_model = MagicMock()
        fake_model.generate_content.return_value = _build_response("")

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ), pytest.raises(LLMError, match="empty"):
            analyzer.classify(stock_data={}, portfolio_state={})

    def test_invalid_schema_raises(self, fake_client: MagicMock) -> None:
        analyzer = CategoryClassifier(client=fake_client, playbook="PB")
        bad = dict(SAMPLE_MEMO_JSON)
        bad["lynch_category"] = "NONSENSE"
        fake_model = MagicMock()
        fake_model.generate_content.return_value = _build_response(
            json.dumps(bad)
        )

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ), pytest.raises(LLMError, match="schema"):
            analyzer.classify(stock_data={}, portfolio_state={})

    def test_sdk_error_wrapped(self, fake_client: MagicMock) -> None:
        analyzer = CategoryClassifier(client=fake_client, playbook="PB")
        fake_model = MagicMock()
        fake_model.generate_content.side_effect = RuntimeError("API down")

        with patch(
            "google.generativeai.GenerativeModel", return_value=fake_model
        ), pytest.raises(LLMError, match="failed"):
            analyzer.classify(stock_data={}, portfolio_state={})
