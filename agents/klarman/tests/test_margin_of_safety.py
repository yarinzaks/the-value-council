"""Unit tests for the SethKlarman strategy class + cash-as-residual."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from agents.klarman.downside import KlarmanMemo
from agents.klarman.margin_of_safety import (
    DEFAULT_MAX_PORTFOLIO_SIZE,
    SethKlarman,
    _deployment_for,
)
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit
from .test_downside import SAMPLE_MEMO_JSON


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


# ---- Cash-as-residual sizing ---------------------------------------------
class TestDeploymentFor:
    def test_zero_qualifying_minimal_deploy(self) -> None:
        d = _deployment_for(0)
        assert d.portfolio_size == 0
        assert d.deployed_fraction == 0.40

    def test_few_qualifying_low_deploy(self) -> None:
        d = _deployment_for(2)
        assert d.portfolio_size == 2
        assert d.deployed_fraction == 0.40

    def test_3_to_7_mid_deploy(self) -> None:
        d = _deployment_for(5)
        assert d.portfolio_size == 5
        assert d.deployed_fraction == 0.65

    def test_8_to_15_high_deploy(self) -> None:
        d = _deployment_for(10)
        assert d.portfolio_size == 10
        assert d.deployed_fraction == 0.85

    def test_15_plus_max_deploy(self) -> None:
        d = _deployment_for(30)
        assert d.portfolio_size == DEFAULT_MAX_PORTFOLIO_SIZE
        assert d.deployed_fraction == 0.92

    def test_monotone_deployment_in_qualifying(self) -> None:
        deploys = [_deployment_for(n).deployed_fraction for n in (2, 5, 10, 30)]
        assert deploys == sorted(deploys)


# ---- Strategy config -----------------------------------------------------
class TestStrategyConfig:
    def test_default_name(self, empty_cache: EdgarCache) -> None:
        s = SethKlarman(edgar_cache=empty_cache)
        assert s.name == "seth_klarman"

    def test_invalid_market_cap(self, empty_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            SethKlarman(edgar_cache=empty_cache, min_market_cap=0)

    def test_invalid_max_de(self, empty_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            SethKlarman(edgar_cache=empty_cache, max_de=0)

    def test_invalid_portfolio_size(self, empty_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            SethKlarman(edgar_cache=empty_cache, max_portfolio_size=0)

    def test_invalid_position_pct(self, empty_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            SethKlarman(edgar_cache=empty_cache, max_position_pct=0)
        with pytest.raises(ValueError):
            SethKlarman(edgar_cache=empty_cache, max_position_pct=200)

    def test_missing_cache_raises(self) -> None:
        with pytest.raises(ValueError):
            SethKlarman(edgar_cache=None)  # type: ignore[arg-type]


# ---- Edge cases ----------------------------------------------------------
class TestSelectEdgeCases:
    def test_empty_universe(self, empty_cache: EdgarCache) -> None:
        s = SethKlarman(edgar_cache=empty_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=[],
            prices=_StaticPriceLookup({}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({}),  # type: ignore[arg-type]
        )
        assert out == {}

    def test_below_market_cap(self, empty_cache: EdgarCache) -> None:
        f = make_pit("TINY", shares=10_000_000)
        s = SethKlarman(edgar_cache=empty_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["TINY"],
            prices=_StaticPriceLookup({"TINY": 1.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"TINY": f}),  # type: ignore[arg-type]
        )
        assert out == {}


# ---- Quant-only happy path ------------------------------------------------
class TestQuantOnlyHappyPath:
    def test_undervalued_qualifies(
        self, steady_fcf_cache: EdgarCache
    ) -> None:
        f = make_pit("STEADY", shares=100_000_000)
        s = SethKlarman(edgar_cache=steady_fcf_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["STEADY"],
            prices=_StaticPriceLookup({"STEADY": 15.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"STEADY": f}),  # type: ignore[arg-type]
        )
        assert "STEADY" in out
        # 1 candidate → deploy 40%; that's also the max position for
        # a single name (subject to the 8% per-name cap).
        # Per-name cap dominates here.
        assert out["STEADY"] <= 0.08 + 1e-9


# ---- LLM filter integration ----------------------------------------------
class TestLlmFilterIntegration:
    def test_llm_reject_drops_candidate(
        self, steady_fcf_cache: EdgarCache
    ) -> None:
        f = make_pit("STEADY", shares=100_000_000)
        rejected = dict(SAMPLE_MEMO_JSON)
        rejected["ticker"] = "STEADY"
        rejected["decision"] = "REJECT"
        memo = KlarmanMemo.model_validate(rejected)

        analyzer = MagicMock()
        analyzer.analyze.return_value = memo

        s = SethKlarman(
            edgar_cache=steady_fcf_cache, downside_analyzer=analyzer
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["STEADY"],
            prices=_StaticPriceLookup({"STEADY": 15.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"STEADY": f}),  # type: ignore[arg-type]
        )
        assert out == {}
        analyzer.analyze.assert_called_once()
        assert s.last_memos == [memo]

    def test_llm_buy_keeps(self, steady_fcf_cache: EdgarCache) -> None:
        f = make_pit("STEADY", shares=100_000_000)
        bought = dict(SAMPLE_MEMO_JSON)
        bought["ticker"] = "STEADY"
        memo = KlarmanMemo.model_validate(bought)
        analyzer = MagicMock()
        analyzer.analyze.return_value = memo

        s = SethKlarman(
            edgar_cache=steady_fcf_cache, downside_analyzer=analyzer
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["STEADY"],
            prices=_StaticPriceLookup({"STEADY": 15.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"STEADY": f}),  # type: ignore[arg-type]
        )
        assert "STEADY" in out

    def test_llm_exception_falls_back(
        self, steady_fcf_cache: EdgarCache
    ) -> None:
        f = make_pit("STEADY", shares=100_000_000)
        analyzer = MagicMock()
        analyzer.analyze.side_effect = RuntimeError("API down")

        s = SethKlarman(
            edgar_cache=steady_fcf_cache, downside_analyzer=analyzer
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["STEADY"],
            prices=_StaticPriceLookup({"STEADY": 15.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"STEADY": f}),  # type: ignore[arg-type]
        )
        assert "STEADY" in out


# ---- Selection history ---------------------------------------------------
class TestSelectionHistory:
    def test_records(self, steady_fcf_cache: EdgarCache) -> None:
        f = make_pit("STEADY", shares=100_000_000)
        s = SethKlarman(edgar_cache=steady_fcf_cache)
        s.select(
            as_of=date(2024, 6, 30),
            universe=["STEADY"],
            prices=_StaticPriceLookup({"STEADY": 15.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"STEADY": f}),  # type: ignore[arg-type]
        )
        recs = s.selections_to_records()
        assert len(recs) == 1
        rec = recs[0]
        assert rec["as_of"] == "2024-06-30"
        assert rec["deployed_fraction"] is not None
        assert rec["top_mos_pct"] is not None
