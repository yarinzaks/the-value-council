"""Unit tests for the WarrenBuffett strategy class."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from agents.buffett.moat_analyzer import BuffettMemo
from agents.buffett.wonderful_business import WarrenBuffett
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit
from .test_moat_analyzer import SAMPLE_MEMO_JSON


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


class TestStrategyConfig:
    def test_default_name(self, buffett_quality_cache: EdgarCache) -> None:
        s = WarrenBuffett(edgar_cache=buffett_quality_cache)
        assert s.name == "warren_buffett"

    def test_invalid_portfolio_size(self, buffett_quality_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            WarrenBuffett(edgar_cache=buffett_quality_cache, portfolio_size=0)

    def test_invalid_max_de(self, buffett_quality_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            WarrenBuffett(edgar_cache=buffett_quality_cache, max_de=0)

    def test_invalid_market_cap(self, buffett_quality_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            WarrenBuffett(edgar_cache=buffett_quality_cache, min_market_cap=0)

    def test_missing_cache_raises(self) -> None:
        with pytest.raises(ValueError):
            WarrenBuffett(edgar_cache=None)  # type: ignore[arg-type]


class TestSelectEdgeCases:
    def test_empty_universe(self, buffett_quality_cache: EdgarCache) -> None:
        s = WarrenBuffett(edgar_cache=buffett_quality_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=[],
            prices=_StaticPriceLookup({}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({}),  # type: ignore[arg-type]
        )
        assert out == {}
        assert len(s.selection_history) == 1
        assert s.selection_history[0].selected_tickers == []

    def test_no_qualifying_returns_cash(
        self, buffett_quality_cache: EdgarCache, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Universe of one stock, but mcap too small → quality fails.
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        f = make_pit("TINY")
        s = WarrenBuffett(edgar_cache=buffett_quality_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["TINY"],
            prices=_StaticPriceLookup({"TINY": 1.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"TINY": f}),  # type: ignore[arg-type]
        )
        assert out == {}

    def test_share_class_excluded(
        self, buffett_quality_cache: EdgarCache, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        f = make_pit("BRK-B")
        s = WarrenBuffett(edgar_cache=buffett_quality_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["BRK-B"],
            prices=_StaticPriceLookup({"BRK-B": 100.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"BRK-B": f}),  # type: ignore[arg-type]
        )
        assert out == {}


class TestQuantOnlyHappyPath:
    def test_undervalued_qualifies(
        self,
        buffett_quality_cache: EdgarCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        # A wonderful business priced cheap.
        f = make_pit(
            "WONDERFUL",
            net_income=1_700_000_000,
            total_equity=6_400_000_000,
            total_debt=1_500_000_000,
            shares=1_000_000_000,
        )
        s = WarrenBuffett(edgar_cache=buffett_quality_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["WONDERFUL"],
            prices=_StaticPriceLookup({"WONDERFUL": 20.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(  # type: ignore[arg-type]
                {"WONDERFUL": f}
            ),
        )
        assert "WONDERFUL" in out
        assert out["WONDERFUL"] == pytest.approx(1.0)


class TestLlmFilterIntegration:
    def test_llm_reject_drops_candidate(
        self,
        buffett_quality_cache: EdgarCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        f = make_pit(
            "WONDERFUL",
            net_income=1_700_000_000,
            total_equity=6_400_000_000,
            total_debt=1_500_000_000,
            shares=1_000_000_000,
        )

        # Build a memo that REJECTs.
        rejected_payload = dict(SAMPLE_MEMO_JSON)
        rejected_payload["ticker"] = "WONDERFUL"
        rejected_payload["decision"] = "REJECT"
        memo = BuffettMemo.model_validate(rejected_payload)

        analyzer = MagicMock()
        analyzer.analyze.return_value = memo

        s = WarrenBuffett(
            edgar_cache=buffett_quality_cache, moat_analyzer=analyzer
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["WONDERFUL"],
            prices=_StaticPriceLookup({"WONDERFUL": 20.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(  # type: ignore[arg-type]
                {"WONDERFUL": f}
            ),
        )
        # LLM rejected → no holding, even though quant passed.
        assert out == {}
        analyzer.analyze.assert_called_once()
        assert s.last_memos == [memo]

    def test_llm_buy_keeps_candidate(
        self,
        buffett_quality_cache: EdgarCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        f = make_pit(
            "WONDERFUL",
            net_income=1_700_000_000,
            total_equity=6_400_000_000,
            total_debt=1_500_000_000,
            shares=1_000_000_000,
        )

        memo = BuffettMemo.model_validate(SAMPLE_MEMO_JSON)
        analyzer = MagicMock()
        analyzer.analyze.return_value = memo

        s = WarrenBuffett(
            edgar_cache=buffett_quality_cache, moat_analyzer=analyzer
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["WONDERFUL"],
            prices=_StaticPriceLookup({"WONDERFUL": 20.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(  # type: ignore[arg-type]
                {"WONDERFUL": f}
            ),
        )
        assert "WONDERFUL" in out

    def test_llm_exception_falls_back_to_quant(
        self,
        buffett_quality_cache: EdgarCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        f = make_pit(
            "WONDERFUL",
            net_income=1_700_000_000,
            total_equity=6_400_000_000,
            total_debt=1_500_000_000,
            shares=1_000_000_000,
        )

        analyzer = MagicMock()
        analyzer.analyze.side_effect = RuntimeError("API down")

        s = WarrenBuffett(
            edgar_cache=buffett_quality_cache, moat_analyzer=analyzer
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["WONDERFUL"],
            prices=_StaticPriceLookup({"WONDERFUL": 20.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(  # type: ignore[arg-type]
                {"WONDERFUL": f}
            ),
        )
        # LLM failure should NOT silently drop the candidate — quant
        # verdict survives.
        assert "WONDERFUL" in out


class TestSelectionHistory:
    def test_records_selection(
        self,
        buffett_quality_cache: EdgarCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agents.buffett.filters.sic_for", lambda _t: 2080
        )
        f = make_pit(
            "WONDERFUL",
            net_income=1_700_000_000,
            total_equity=6_400_000_000,
            total_debt=1_500_000_000,
            shares=1_000_000_000,
        )
        s = WarrenBuffett(edgar_cache=buffett_quality_cache)
        s.select(
            as_of=date(2024, 6, 30),
            universe=["WONDERFUL"],
            prices=_StaticPriceLookup({"WONDERFUL": 20.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(  # type: ignore[arg-type]
                {"WONDERFUL": f}
            ),
        )
        records = s.selections_to_records()
        assert len(records) == 1
        rec = records[0]
        assert rec["as_of"] == "2024-06-30"
        assert "WONDERFUL" in rec["selected_tickers"]
        assert rec["top_mos_pct"] is not None
