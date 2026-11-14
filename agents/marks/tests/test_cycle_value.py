"""Unit tests for the HowardMarks strategy class."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from loguru import logger as loguru_logger

from agents.marks import cycle_value
from agents.marks.cycle_value import HowardMarks
from agents.marks.second_level import MarksMemo
from agents.marks.temperature import (
    TemperatureAssessment,
    TemperatureSignals,
    posture_for_score,
)
from core.backtest.decision_logger import DecisionLogger, make_decision
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit
from .test_second_level import SAMPLE_MEMO_JSON


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

        def _classify(*, stock_data, portfolio_state):
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

        def _classify(*, stock_data, portfolio_state):
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


class TestTemperatureReadsTheMarketNotTheSurvivors:
    """Stage order: the pendulum question comes before the screen.

    ``select`` used to gate first and assess temperature over the
    survivors. On the real 2026-04-01 universe that read Neutral where
    the market was Cool — 537 measurable tickers scoring -2.0 against
    107 survivors scoring -1.0 — because two of the five signals were
    measuring the gate rather than the market.
    """

    @staticmethod
    def _mixed_market() -> tuple[dict[str, object], dict[str, float]]:
        """Sixteen names: half sound, half distressed and over-levered.

        The distressed half fails the D/E gate, so it exists in the
        market and not in the survivor set — which is the whole point.
        """
        fins: dict[str, object] = {}
        prices: dict[str, float] = {}
        for i in range(8):
            fins[f"OK{i}"] = make_pit(
                f"OK{i}",
                eps_diluted=2.0,
                net_income=200_000_000,
                total_equity=2_000_000_000,
                total_debt=400_000_000,
                shares=100_000_000,
                dividends_paid=-40_000_000,
            )
            prices[f"OK{i}"] = 24.0
        for i in range(8):
            fins[f"SICK{i}"] = make_pit(
                f"SICK{i}",
                eps_diluted=-1.0,
                net_income=-150_000_000,
                total_equity=300_000_000,
                total_debt=1_200_000_000,  # D/E 4.0 — fails max_de
                shares=100_000_000,
                dividends_paid=0.0,
            )
            prices[f"SICK{i}"] = 12.0
        return fins, prices

    def test_the_distress_in_the_market_reaches_the_thermometer(
        self, empty_cache: EdgarCache
    ) -> None:
        fins, prices = self._mixed_market()
        s = HowardMarks(edgar_cache=empty_cache, min_market_cap=1.0)

        s.select(
            as_of=date(2024, 6, 30),
            universe=sorted(fins),
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )

        assert s.last_temperature is not None
        # Half the market is losing money. Read after the gates this is
        # 0.0 and votes +2 (euphoria); read before, it is 0.5 and votes
        # -2 (widespread distress).
        assert s.last_temperature.signals.frac_negative_ni == pytest.approx(0.5)
        assert s.last_temperature.votes["frac_negative_ni"] == -2

    def test_the_over_levered_cohort_reaches_it_too(
        self, empty_cache: EdgarCache
    ) -> None:
        fins, prices = self._mixed_market()
        s = HowardMarks(edgar_cache=empty_cache, min_market_cap=1.0)

        s.select(
            as_of=date(2024, 6, 30),
            universe=sorted(fins),
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )

        assert s.last_temperature is not None
        assert s.last_temperature.signals.frac_high_de == pytest.approx(0.5)
        assert s.last_temperature.signals.universe_size == 16

    def test_the_gates_still_reject_the_distressed_names(
        self, empty_cache: EdgarCache
    ) -> None:
        # Guard against over-correction: seeing the sick names must not
        # mean buying them.
        fins, prices = self._mixed_market()
        s = HowardMarks(edgar_cache=empty_cache, min_market_cap=1.0)

        out = s.select(
            as_of=date(2024, 6, 30),
            universe=sorted(fins),
            prices=_StaticPriceLookup(prices),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup(fins),  # type: ignore[arg-type]
        )

        assert not any(t.startswith("SICK") for t in out)


# ---- Posture persistence across sessions (audit item 21) -----------------
def _measured(score: float, as_of: date) -> TemperatureAssessment:
    """A fixed reading with one measurable name, so it counts as a market."""
    return TemperatureAssessment(
        as_of=as_of,
        score=score,
        posture=posture_for_score(score),
        signals=TemperatureSignals(
            universe_size=1,
            median_pe=15.0,
            frac_negative_ni=0.2,
            median_de=0.5,
            frac_high_de=0.2,
            median_yield_pct=2.0,
        ),
        votes={},
    )


def _journal_posture(
    log: DecisionLogger, day: date, score: float, posture: str
) -> None:
    log.log(
        make_decision(
            ticker="ANY",
            decision="BUY",
            agent="howard_marks",
            timestamp=f"{day.isoformat()}T00:00:00+00:00",
            criteria_values={"posture": posture, "temperature_score": score},
        )
    )


def _select_on(s: HowardMarks, day: date) -> None:
    s.select(
        as_of=day,
        universe=[],
        prices=_StaticPriceLookup({}),  # type: ignore[arg-type]
        fundamentals=_StaticFundamentalsLookup({}),  # type: ignore[arg-type]
    )


class TestPosturePersistsAcrossSessions:
    """The live agent is a fresh process every day, so the posture it
    held is read back from its own journal; a backtest keeps one
    instance and reads its own ``selection_history``."""

    def test_live_agent_holds_its_journaled_posture_through_a_one_day_dip(
        self,
        empty_cache: EdgarCache,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log = DecisionLogger(root=tmp_path / "decisions")
        for d in (date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)):
            _journal_posture(log, d, -2.0, "Cool")
        # A runner row for the same agent carries no posture: ignored.
        log.log(
            make_decision(
                ticker="XYZ",
                decision="EXIT",
                agent="howard_marks",
                timestamp="2026-11-11T21:00:00+00:00",
            )
        )
        monkeypatch.setattr(
            cycle_value,
            "assess_market_temperature",
            lambda measurable, *, as_of: _measured(-1.0, as_of),
        )
        s = HowardMarks(
            edgar_cache=empty_cache,
            decision_logger=log,
            posture_from_journal=True,
        )

        _select_on(s, date(2026, 11, 12))

        assert s.last_temperature is not None
        assert s.last_temperature.posture == "Cool"
        assert s.last_temperature.measured_posture == "Neutral"
        assert s.last_temperature.score == -1.0
        assert s.selection_history[-1].posture == "Cool"

    def test_backtest_instance_uses_its_own_history(
        self, empty_cache: EdgarCache, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scores = iter([-2.0, -2.0, -2.0, -1.0])
        monkeypatch.setattr(
            cycle_value,
            "assess_market_temperature",
            lambda measurable, *, as_of: _measured(next(scores), as_of),
        )
        s = HowardMarks(edgar_cache=empty_cache)

        for day in (1, 2, 3, 4):
            _select_on(s, date(2024, 7, day))

        assert [h.posture for h in s.selection_history] == ["Cool"] * 4

    def test_journal_mode_without_a_logger_is_refused(
        self, empty_cache: EdgarCache
    ) -> None:
        with pytest.raises(ValueError, match="decision_logger"):
            HowardMarks(edgar_cache=empty_cache, posture_from_journal=True)

    def test_an_empty_market_is_not_settled_against_history(
        self, empty_cache: EdgarCache, tmp_path: Path
    ) -> None:
        # No measurable name: the Neutral default is no reading, and
        # the exit verdicts already refuse to judge against it.
        log = DecisionLogger(root=tmp_path / "decisions")
        for d in (date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)):
            _journal_posture(log, d, -2.0, "Cool")
        s = HowardMarks(
            edgar_cache=empty_cache,
            decision_logger=log,
            posture_from_journal=True,
        )

        _select_on(s, date(2026, 11, 12))

        assert s.last_temperature is not None
        assert s.last_temperature.posture == "Neutral"

    def test_the_live_roster_reads_posture_from_the_journal(
        self, empty_cache: EdgarCache, tmp_path: Path
    ) -> None:
        from core.backtest.data_loader import PriceDataLoader
        from core.live.runner import build_default_adapters

        log = DecisionLogger(root=tmp_path / "decisions")
        adapters = build_default_adapters(
            decision_logger=log,
            edgar_cache=empty_cache,
            price_loader=PriceDataLoader(cache_path=tmp_path / "prices.db"),
        )

        (marks,) = [a for a in adapters if a.name == "howard_marks"]
        assert isinstance(marks.strategy, HowardMarks)
        assert marks.strategy.posture_from_journal is True
        assert marks.strategy.decision_logger is log

    def test_an_unhashable_journaled_posture_skips_the_row_not_the_run(
        self, empty_cache: EdgarCache, tmp_path: Path
    ) -> None:
        # A hand-edited or corrupted journal row whose posture is a list
        # used to reach a dict lookup and raise TypeError out of select.
        log = DecisionLogger(root=tmp_path / "decisions")
        days = (date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11))
        for d in days:
            _journal_posture(log, d, -2.0, "Cool")
        (bad,) = [
            p
            for p in (log.root / "howard_marks").glob("*.json")
            if p.name.startswith("2026-11-11")
        ]
        rows = json.loads(bad.read_text())
        rows.append(
            {"criteria_values": {"posture": ["Hot"], "temperature_score": 2.0}}
        )
        bad.write_text(json.dumps(rows))
        s = HowardMarks(
            edgar_cache=empty_cache,
            decision_logger=log,
            posture_from_journal=True,
        )

        messages: list[str] = []
        sink = loguru_logger.add(
            lambda m: messages.append(str(m)), level="WARNING"
        )
        try:
            readings = s._journal_readings(date(2026, 11, 12))
        finally:
            loguru_logger.remove(sink)

        assert [(r.as_of, r.posture) for r in readings] == [
            (d, "Cool") for d in days
        ]
        assert any(str(bad) in m and "posture" in m for m in messages)
