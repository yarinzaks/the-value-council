"""Unit tests for the PeterLynch strategy class."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from agents.lynch.category_classifier import LynchMemo
from agents.lynch.garp import PeterLynch
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit
from .test_category_classifier import SAMPLE_MEMO_JSON


class _StaticPriceLookup:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def get(self, ticker: str) -> float | None:  # noqa: D401
        return self._prices.get(ticker)


class _StaticFundamentalsLookup:
    def __init__(self, fins: dict[str, object]) -> None:
        self._fins = fins

    def get(self, ticker: str) -> object | None:  # noqa: D401
        return self._fins.get(ticker)


class TestStrategyConfig:
    def test_default_name(self, fast_grower_cache: EdgarCache) -> None:
        s = PeterLynch(edgar_cache=fast_grower_cache)
        assert s.name == "peter_lynch"

    def test_invalid_portfolio_size(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        with pytest.raises(ValueError):
            PeterLynch(edgar_cache=fast_grower_cache, portfolio_size=0)

    def test_invalid_market_cap(self, fast_grower_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            PeterLynch(edgar_cache=fast_grower_cache, min_market_cap=0)

    def test_invalid_max_de(self, fast_grower_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            PeterLynch(edgar_cache=fast_grower_cache, max_de=0)

    def test_missing_cache_raises(self) -> None:
        with pytest.raises(ValueError):
            PeterLynch(edgar_cache=None)  # type: ignore[arg-type]


class TestSelectEdgeCases:
    def test_empty_universe(self, fast_grower_cache: EdgarCache) -> None:
        s = PeterLynch(edgar_cache=fast_grower_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=[],
            prices=_StaticPriceLookup({}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({}),  # type: ignore[arg-type]
        )
        assert out == {}

    def test_below_market_cap_returns_cash(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        # Tiny mcap → quality fails.
        f = make_pit("TINY", shares=1_000_000, eps_diluted=2.0)
        s = PeterLynch(edgar_cache=fast_grower_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["TINY"],
            prices=_StaticPriceLookup({"TINY": 1.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"TINY": f}),  # type: ignore[arg-type]
        )
        assert out == {}


class TestQuantOnlyHappyPath:
    def test_fast_grower_qualifies(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,
        )
        s = PeterLynch(edgar_cache=fast_grower_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
        )
        assert "FASTY" in out
        assert out["FASTY"] == pytest.approx(1.0)


class TestLlmFilterIntegration:
    def test_llm_reject_drops_candidate(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,
        )
        rejected = dict(SAMPLE_MEMO_JSON)
        rejected["ticker"] = "FASTY"
        rejected["decision"] = "REJECT"
        memo = LynchMemo.model_validate(rejected)

        classifier = MagicMock()
        classifier.classify.return_value = memo

        s = PeterLynch(
            edgar_cache=fast_grower_cache, category_classifier=classifier
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
        )
        assert out == {}
        classifier.classify.assert_called_once()
        assert s.last_memos == [memo]

    def test_llm_buy_keeps(self, fast_grower_cache: EdgarCache) -> None:
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,
        )
        bought = dict(SAMPLE_MEMO_JSON)
        bought["ticker"] = "FASTY"
        memo = LynchMemo.model_validate(bought)
        classifier = MagicMock()
        classifier.classify.return_value = memo

        s = PeterLynch(
            edgar_cache=fast_grower_cache, category_classifier=classifier
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
        )
        assert "FASTY" in out

    def test_llm_exception_falls_back(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,
        )
        classifier = MagicMock()
        classifier.classify.side_effect = RuntimeError("API down")

        s = PeterLynch(
            edgar_cache=fast_grower_cache, category_classifier=classifier
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
        )
        # LLM failure should NOT silently drop the candidate.
        assert "FASTY" in out


class TestSelectionHistory:
    def test_records(self, fast_grower_cache: EdgarCache) -> None:
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,
        )
        s = PeterLynch(edgar_cache=fast_grower_cache)
        s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
        )
        recs = s.selections_to_records()
        assert len(recs) == 1
        rec = recs[0]
        assert rec["as_of"] == "2024-06-30"
        assert "FASTY" in rec["selected_tickers"]
        assert rec["top_peg"] is not None
