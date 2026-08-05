"""Unit tests for the PhilipFisher strategy class."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from agents.fisher.quality_growth import (
    DEFAULT_MAX_PORTFOLIO_SIZE,
    PhilipFisher,
)
from agents.fisher.scuttlebutt import FisherMemo
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit
from .test_scuttlebutt import SAMPLE_MEMO_JSON


class _StaticPriceLookup:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def get(self, ticker: str) -> float | None:
        return self._prices.get(ticker)


class _StaticFundamentalsLookup:
    def __init__(self, fins: dict[str, object]) -> None:
        self._fins = fins

    def get(self, ticker: str) -> object | None:
        return self._fins.get(ticker)


# ---- Strategy config -----------------------------------------------------
class TestStrategyConfig:
    def test_default_name(self, empty_cache: EdgarCache) -> None:
        s = PhilipFisher(edgar_cache=empty_cache)
        assert s.name == "philip_fisher"

    def test_invalid_market_cap(self, empty_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            PhilipFisher(edgar_cache=empty_cache, min_market_cap=0)

    def test_invalid_max_de(self, empty_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            PhilipFisher(edgar_cache=empty_cache, max_de=0)

    def test_invalid_portfolio_size(self, empty_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            PhilipFisher(edgar_cache=empty_cache, max_portfolio_size=0)

    def test_missing_cache_raises(self) -> None:
        with pytest.raises(ValueError):
            PhilipFisher(edgar_cache=None)  # type: ignore[arg-type]

    def test_default_portfolio_size(self) -> None:
        assert DEFAULT_MAX_PORTFOLIO_SIZE == 15


# ---- Edge cases ----------------------------------------------------------
class TestSelectEdgeCases:
    def test_empty_universe(self, empty_cache: EdgarCache) -> None:
        s = PhilipFisher(edgar_cache=empty_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=[],
            prices=_StaticPriceLookup({}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({}),  # type: ignore[arg-type]
        )
        assert out == {}

    def test_below_market_cap(self, empty_cache: EdgarCache) -> None:
        f = make_pit("TINY", shares=10_000_000)
        s = PhilipFisher(edgar_cache=empty_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["TINY"],
            prices=_StaticPriceLookup({"TINY": 1.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"TINY": f}),  # type: ignore[arg-type]
        )
        assert out == {}


# ---- Quant-only happy path ------------------------------------------------
class TestQuantOnlyHappyPath:
    def test_tier_a_qualifies(
        self, fisher_quality_cache: EdgarCache
    ) -> None:
        f = make_pit(
            "QUALITY",
            revenue=5_000_000_000,
            operating_income=1_000_000_000,
            shares=200_000_000,
            eps_diluted=4.0,
        )
        # mcap = $80 * 200M = $16B, P/E = 80/4 = 20
        s = PhilipFisher(edgar_cache=fisher_quality_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["QUALITY"],
            prices=_StaticPriceLookup({"QUALITY": 80.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"QUALITY": f}),  # type: ignore[arg-type]
        )
        assert "QUALITY" in out
        # Tier A → 12% target weight.
        assert out["QUALITY"] == pytest.approx(0.12)


# ---- LLM filter integration ----------------------------------------------
class TestLlmFilterIntegration:
    def _quality_universe(
        self,
    ) -> tuple[dict[str, float], dict[str, object]]:
        prices: dict[str, float] = {}
        fins: dict[str, object] = {}
        f = make_pit(
            "QUALITY",
            revenue=5_000_000_000,
            operating_income=1_000_000_000,
            shares=200_000_000,
            eps_diluted=4.0,
        )
        fins["QUALITY"] = f
        prices["QUALITY"] = 80.0
        return prices, fins

    def test_integrity_failure_drops(
        self, fisher_quality_cache: EdgarCache
    ) -> None:
        prices, fins = self._quality_universe()

        bad = dict(SAMPLE_MEMO_JSON)
        bad["ticker"] = "QUALITY"
        bad["integrity_check_passed"] = False
        bad["decision"] = "BUY"  # even with BUY, integrity-fail vetoes
        bad["fifteen_points_score"] = dict(bad["fifteen_points_score"])
        bad["fifteen_points_score"]["point_15_management_integrity"] = "FAIL"
        memo = FisherMemo.model_validate(bad)

        analyzer = MagicMock()
        analyzer.analyze.return_value = memo

        s = PhilipFisher(
            edgar_cache=fisher_quality_cache,
            scuttlebutt_analyzer=analyzer,
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["QUALITY"],
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )
        # Integrity check is non-negotiable — drops the buy.
        assert out == {}
        analyzer.analyze.assert_called_once()

    def test_reject_decision_drops(
        self, fisher_quality_cache: EdgarCache
    ) -> None:
        prices, fins = self._quality_universe()

        rej = dict(SAMPLE_MEMO_JSON)
        rej["ticker"] = "QUALITY"
        rej["decision"] = "REJECT"
        memo = FisherMemo.model_validate(rej)

        analyzer = MagicMock()
        analyzer.analyze.return_value = memo

        s = PhilipFisher(
            edgar_cache=fisher_quality_cache,
            scuttlebutt_analyzer=analyzer,
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["QUALITY"],
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )
        assert out == {}

    def test_buy_keeps(self, fisher_quality_cache: EdgarCache) -> None:
        prices, fins = self._quality_universe()

        good = dict(SAMPLE_MEMO_JSON)
        good["ticker"] = "QUALITY"
        memo = FisherMemo.model_validate(good)
        analyzer = MagicMock()
        analyzer.analyze.return_value = memo

        s = PhilipFisher(
            edgar_cache=fisher_quality_cache,
            scuttlebutt_analyzer=analyzer,
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["QUALITY"],
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )
        assert "QUALITY" in out

    def test_llm_exception_falls_back(
        self, fisher_quality_cache: EdgarCache
    ) -> None:
        prices, fins = self._quality_universe()
        analyzer = MagicMock()
        analyzer.analyze.side_effect = RuntimeError("API down")

        s = PhilipFisher(
            edgar_cache=fisher_quality_cache,
            scuttlebutt_analyzer=analyzer,
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["QUALITY"],
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )
        # Fall back to quant-only verdict.
        assert "QUALITY" in out


# ---- Selection history ---------------------------------------------------
class TestSelectionHistory:
    def test_records(self, fisher_quality_cache: EdgarCache) -> None:
        f = make_pit(
            "QUALITY",
            revenue=5_000_000_000,
            operating_income=1_000_000_000,
            shares=200_000_000,
            eps_diluted=4.0,
        )
        s = PhilipFisher(edgar_cache=fisher_quality_cache)
        s.select(
            as_of=date(2024, 6, 30),
            universe=["QUALITY"],
            prices=_StaticPriceLookup({"QUALITY": 80.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"QUALITY": f}),  # type: ignore[arg-type]
        )
        recs = s.selections_to_records()
        assert len(recs) == 1
        rec = recs[0]
        assert rec["as_of"] == "2024-06-30"
        assert "QUALITY" in rec["selected_tickers"]
        assert rec["tier_a_count"] == 1
        assert rec["tier_b_count"] == 0
