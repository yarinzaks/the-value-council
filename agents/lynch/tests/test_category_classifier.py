"""Unit tests for the heuristic + LLM category classifier."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.evidence_rules import with_evidence_rules
from agents.lynch.category_classifier import (
    _LYNCH_SYSTEM_PROMPT,
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
    """A client exposing the single method an analyzer now calls."""
    c = MagicMock()
    c.complete_memo = MagicMock()
    return c


def _build_response(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    return r


class TestClassify:
    """What is left of this analyzer once the transport moved out.

    Render the prompt, name the persona, name the schema, and stay out
    of the way of an error. The empty-response, unparsable-JSON and
    API-failure cases used to be tested here against a stubbed vendor
    SDK — they belong to the client now and are covered once in
    core/llm/tests/test_claude_client.py, instead of five near-copies
    each covering one analyzer.
    """

    def a_memo(self):
        return LynchMemo.model_validate(SAMPLE_MEMO_JSON)

    def test_the_memo_comes_back(self, fake_client: MagicMock) -> None:
        memo = self.a_memo()
        fake_client.complete_memo.return_value = memo

        got = CategoryClassifier(client=fake_client, playbook="PB").classify(
            stock_data={"ticker": "QUALITY"}, portfolio_state={"cash": 1000}
        )

        assert got is memo

    def test_the_request_names_the_persona_and_the_schema(
        self, fake_client: MagicMock
    ) -> None:
        """Lynch's whole contribution to the call."""
        fake_client.complete_memo.return_value = self.a_memo()

        CategoryClassifier(client=fake_client, playbook="PB").classify(
            stock_data={}, portfolio_state={}
        )

        sent = fake_client.complete_memo.call_args.kwargs
        assert sent["system"] == with_evidence_rules(_LYNCH_SYSTEM_PROMPT)
        assert sent["schema"] is LynchMemo
        assert "temperature" not in sent, "the parameter no longer exists"
        assert "config" not in sent, "no vendor config object survives"

    def test_the_prompt_carries_the_playbook_and_the_stock(
        self, fake_client: MagicMock
    ) -> None:
        fake_client.complete_memo.return_value = self.a_memo()

        CategoryClassifier(client=fake_client, playbook="THE PLAYBOOK TEXT").classify(
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
            CategoryClassifier(client=fake_client, playbook="PB").classify(
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

        source = _Path(importlib.import_module("agents.lynch.category_classifier").__file__).read_text()
        for marker in ("google.genai", "genai", "_sdk", "_parse_json"):
            assert marker not in source, f"{marker} is back in agents.lynch.category_classifier"
