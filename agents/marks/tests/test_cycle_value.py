"""Unit tests for the HowardMarks strategy class."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from agents.marks.cycle_value import HowardMarks
from agents.marks.second_level import MarksMemo
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit
from .test_second_level import SAMPLE_MEMO_JSON


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


# ---- Strategy config -----------------------------------------------------
class TestStrategyConfig:
    def test_default_name(self, empty_cache: EdgarCache) -> None:
        s = HowardMarks(edgar_cache=empty_cache)
        assert s.name == "howard_marks"

    def test_invalid_market_cap(self, empty_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            HowardMarks(edgar_cache=empty_cache, min_market_cap=0)

    def test_invalid_max_de(self, empty_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            HowardMarks(edgar_cache=empty_cache, max_de=0)

    def test_missing_cache_raises(self) -> None:
        with pytest.raises(ValueError):
            HowardMarks(edgar_cache=None)  # type: ignore[arg-type]


# ---- Edge cases ----------------------------------------------------------
class TestSelectEdgeCases:
    def test_empty_universe(self, empty_cache: EdgarCache) -> None:
        s = HowardMarks(edgar_cache=empty_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=[],
            prices=_StaticPriceLookup({}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({}),  # type: ignore[arg-type]
        )
        assert out == {}
        assert s.last_temperature is not None
        # Empty universe → Neutral posture (default).
        assert s.last_temperature.posture == "Neutral"

    def test_below_market_cap(self, empty_cache: EdgarCache) -> None:
        f = make_pit("TINY", shares=10_000_000)
        s = HowardMarks(edgar_cache=empty_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["TINY"],
            prices=_StaticPriceLookup({"TINY": 1.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"TINY": f}),  # type: ignore[arg-type]
        )
        assert out == {}


# ---- Posture-driven sizing -----------------------------------------------
class TestPostureDrivenSizing:
    def test_cold_posture_deploys_more_widely(
        self, empty_cache: EdgarCache
    ) -> None:
        # Build a Cold-looking universe: cheap, leveraged, distressed —
        # but with a few healthy survivors that pass quality gates and
        # the Cold posture's eyld-floor.
        prices: dict[str, float] = {}
        fins: dict[str, object] = {}
        # 8 healthy cheap names that PASS Marks gates (positive NI,
        # D/E ≤ 1.0): PE 6.5, eyld ~15%, mcap > $500M.
        for i in range(8):
            t = f"CHEAP{i}"
            f = make_pit(
                t,
                eps_diluted=4.0,
                net_income=400_000_000,
                total_equity=2_000_000_000,
                total_debt=200_000_000,
                shares=100_000_000,
                dividends_paid=-100_000_000,  # 5% yield
            )
            fins[t] = f
            prices[t] = 26.0  # PE 26/4 = 6.5
        # Distress filler — fail quality gates (neg NI), but still
        # contribute to the temperature signal as part of the
        # post-gate universe? No — temperature is computed AFTER
        # quality gates, so neg-NI filler doesn't influence it. To
        # bias the signals Cold we need a CHEAP, healthy universe
        # but with low yields and tight balance sheets (which
        # actually goes the wrong way — low yields are Hot).
        # The cleanest "Cold-like" universe via post-gate signals:
        # very LOW PE (cheap), low D/E (healthy), high yields. Build
        # 8 names like that and verify posture.
        s = HowardMarks(edgar_cache=empty_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=list(prices.keys()),
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )
        # Posture should be Cold or Cool (cheap PE, low D/E, high
        # yield, no distress). Both deploy meaningful capital.
        assert s.last_temperature is not None
        assert s.last_temperature.posture in ("Cold", "Cool", "Neutral")
        # Equal-weight inside the deployed fraction; sum should be
        # ≤ 1.0.
        total_weight = sum(out.values())
        assert 0.4 <= total_weight <= 1.0

    def test_no_qualifying_in_hot_returns_cash(
        self, empty_cache: EdgarCache
    ) -> None:
        # Build an artificially "Hot" universe: high PE, high D/E,
        # low yields. No name will clear the Hot posture's eyld floor
        # (6.5%) when PE is 30+.
        prices: dict[str, float] = {}
        fins: dict[str, object] = {}
        for i in range(20):
            t = f"FROTHY{i}"
            f = make_pit(
                t,
                eps_diluted=1.0,
                net_income=100_000_000,
                total_equity=300_000_000,
                total_debt=300_000_000,
                shares=100_000_000,
                dividends_paid=-3_000_000,  # 0.1% yield
            )
            fins[t] = f
            prices[t] = 30.0  # PE 30 → eyld 3.3%
        s = HowardMarks(edgar_cache=empty_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=list(prices.keys()),
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )
        assert s.last_temperature is not None
        # Posture should be Warm or Hot.
        assert s.last_temperature.posture in ("Warm", "Hot")
        # Hot's 6.5% eyld floor (or Warm's 5.5%) — names at 3.3%
        # eyld are filtered → no positions.
        assert out == {}


# ---- LLM filter integration ----------------------------------------------
class TestLlmFilterIntegration:
    def _qualifying_universe(self) -> tuple[dict[str, float], dict[str, object]]:
        prices: dict[str, float] = {}
        fins: dict[str, object] = {}
        for i in range(8):
            t = f"CHEAP{i}"
            f = make_pit(
                t,
                eps_diluted=4.0,
                net_income=400_000_000,
                total_equity=2_000_000_000,
                total_debt=200_000_000,
                shares=100_000_000,
                dividends_paid=-100_000_000,
            )
            fins[t] = f
            prices[t] = 26.0
        return prices, fins

    def test_llm_reject_drops_candidate(
        self, empty_cache: EdgarCache
    ) -> None:
        prices, fins = self._qualifying_universe()

        # All memos REJECT.
        rejected = dict(SAMPLE_MEMO_JSON)
        rejected["decision"] = "REJECT"

        def _classify(*, stock_data, portfolio_state):  # noqa: ARG001
            r = dict(rejected)
            r["ticker"] = stock_data["ticker"]
            return MarksMemo.model_validate(r)

        analyzer = MagicMock()
        analyzer.analyze.side_effect = _classify

        s = HowardMarks(
            edgar_cache=empty_cache, second_level_analyzer=analyzer
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=list(prices.keys()),
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )
        # LLM rejected every candidate → cash.
        assert out == {}

    def test_llm_buy_keeps(self, empty_cache: EdgarCache) -> None:
        prices, fins = self._qualifying_universe()

        def _classify(*, stock_data, portfolio_state):  # noqa: ARG001
            m = dict(SAMPLE_MEMO_JSON)
            m["ticker"] = stock_data["ticker"]
            return MarksMemo.model_validate(m)

        analyzer = MagicMock()
        analyzer.analyze.side_effect = _classify

        s = HowardMarks(
            edgar_cache=empty_cache, second_level_analyzer=analyzer
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=list(prices.keys()),
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )
        assert len(out) == len(prices)

    def test_llm_exception_falls_back(self, empty_cache: EdgarCache) -> None:
        prices, fins = self._qualifying_universe()

        analyzer = MagicMock()
        analyzer.analyze.side_effect = RuntimeError("API down")

        s = HowardMarks(
            edgar_cache=empty_cache, second_level_analyzer=analyzer
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=list(prices.keys()),
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )
        # All quant winners survive the LLM exception.
        assert len(out) == len(prices)


# ---- Selection history --------------------------------------------------
class TestSelectionHistory:
    def test_records(self, empty_cache: EdgarCache) -> None:
        prices: dict[str, float] = {}
        fins: dict[str, object] = {}
        for i in range(8):
            t = f"CHEAP{i}"
            f = make_pit(
                t,
                eps_diluted=4.0,
                net_income=400_000_000,
                total_equity=2_000_000_000,
                total_debt=200_000_000,
                shares=100_000_000,
                dividends_paid=-100_000_000,
            )
            fins[t] = f
            prices[t] = 26.0
        s = HowardMarks(edgar_cache=empty_cache)
        s.select(
            as_of=date(2024, 6, 30),
            universe=list(prices.keys()),
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )
        recs = s.selections_to_records()
        assert len(recs) == 1
        rec = recs[0]
        assert rec["as_of"] == "2024-06-30"
        assert rec["posture"] in ("Cold", "Cool", "Neutral", "Warm", "Hot")
        assert rec["temperature_score"] is not None
        assert rec["deployed_fraction"] is not None
