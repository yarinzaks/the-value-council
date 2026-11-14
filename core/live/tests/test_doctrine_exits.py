"""Doctrine exits: every strategy's own keep-or-sell verdict, journaled.

Audit finding SF-X1 (critical): no sell rule from any playbook ran.
The only sale in the engine was "the name left today's target list"
after the 30-day floor, and every one of 823 EXIT rows said exactly
that, with ``exit_trigger`` empty — while Lynch's strategy computed a
reasoned verdict for each held name into ``last_exits`` and nothing
read it.

This file pins the plumbing, not any one doctrine:

* a strategy publishes ``last_exit_decisions`` (or, for Lynch, the
  ``last_exits`` it already wrote) and the adapter carries them on the
  scan;
* in ``shadow`` mode — every agent's default — the runner journals each
  verdict beside what it actually did and trades exactly as before;
* in ``enforce`` mode the verdict governs the held name;
* an EXIT row names the doctrine's reason when the doctrine gave one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from loguru import logger as loguru_logger

from agents.lynch.exits import ExitDecision
from core.backtest.decision_logger import DecisionLogger
from core.live import doctrine_exits
from core.live.agent_adapter import (
    AgentAdapter,
    LiveTarget,
    LiveWatch,
    LynchLive,
    ScanResult,
)
from core.live.doctrine_exits import (
    DEFAULT_EXIT_MODE,
    DOCTRINE_EXIT_MODES,
    ExitVerdict,
    exit_mode_for,
    parse_exit_mode,
)
from core.live.portfolio import LivePortfolio, Position
from core.live.runner import DailyRunner
from core.live.trade_ledger import read_ledger

AS_OF = date(2026, 8, 5)
REPO = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------
@contextmanager
def _logged(level: str) -> Iterator[list[str]]:
    """What the project's loguru logger wrote at ``level`` or above.

    ``caplog`` cannot see loguru, which does not propagate to stdlib
    logging; a sink can.
    """
    messages: list[str] = []
    sink_id = loguru_logger.add(lambda m: messages.append(str(m)), level=level)
    try:
        yield messages
    finally:
        loguru_logger.remove(sink_id)


class _Prices:
    def __init__(self, prices: dict[str, float | None]) -> None:
        self._prices = prices

    def get_price_on(
        self, ticker: str, as_of: date, *, force_refresh: bool = False
    ) -> float | None:
        return self._prices.get(ticker)

    def cached_range(self, ticker: str) -> tuple[date, date] | None:
        return None

    def splits_between(
        self, ticker: str, start: date, end: date
    ) -> list[tuple[date, float]]:
        return []


class _Adapter:
    """A scan with fixed targets and fixed verdicts."""

    def __init__(
        self,
        targets: list[str],
        verdicts: list[ExitVerdict],
        *,
        mode: str = "shadow",
        forced: list[str] | None = None,
    ) -> None:
        self.name = "stub_agent"
        self.exit_mode = mode
        self._targets = targets
        self._verdicts = verdicts
        self._forced = forced or []

    def run_scan(  # type: ignore[no-untyped-def]
        self, as_of, universe, prices, fundamentals, *, held=None
    ) -> ScanResult:
        return ScanResult(
            targets=[
                LiveTarget(
                    ticker=t,
                    weight=1.0 / len(self._targets),
                    rank=i,
                    why_en="stub",
                    why_he="stub",
                    score=None,
                )
                for i, t in enumerate(self._targets, start=1)
            ],
            watchlist=[],
            universe_size=len(universe),
            forced_exits=list(self._forced),
            exit_verdicts=list(self._verdicts),
        )


def _runner(root: Path, monkeypatch: pytest.MonkeyPatch, prices: dict[str, float | None]) -> DailyRunner:
    monkeypatch.setattr("core.live.runner.save_snapshot", lambda snap: None)
    return DailyRunner(
        market="US",
        adapters=[],
        portfolio_dir=root / "portfolios",
        published_dir=root / "published",
        ledger_dir=root / "trades",
        price_loader=_Prices(prices),  # type: ignore[arg-type]
        universe=object(),  # type: ignore[arg-type]
        pit_loader=object(),  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
        decision_logger=DecisionLogger(root=root / "decisions"),
        exit_verdict_logger=DecisionLogger(root=root / "doctrine_exits"),
    )


def _position(ticker: str, *, days: int, entry: float = 50.0, mark: float = 50.0) -> Position:
    return Position(
        ticker=ticker,
        shares=10.0,
        entry_price=entry,
        entry_date=(AS_OF - timedelta(days=days)).isoformat(),
        current_price=mark,
        why_en="seeded",
        why_he="seeded",
    )


def _seed(runner: DailyRunner, positions: list[Position], cash: float = 2_000.0) -> None:
    book = LivePortfolio(agent="stub_agent", cash=cash)
    # August is a Council rebalance month; the stub does not honour run
    # types, but keep the book unambiguous anyway.
    book.last_rebalance_date = "2026-08-03"
    book.positions.extend(positions)
    book.save(directory=runner.portfolio_dir)


#: The book every runner test starts from.
#:
#: KEEP   — a current target, aged; the doctrine says sell.
#: GONE   — off the list, aged; the doctrine says keep.
#: BROKE  — off the list, aged; the doctrine says sell.
#: YOUNG  — off the list, 12 days old; the doctrine says sell.
#: PLAIN  — off the list, aged; the doctrine is silent.
PRICES: dict[str, float | None] = {
    "KEEP": 60.0,
    "GONE": 55.0,
    "BROKE": 40.0,
    "YOUNG": 45.0,
    "PLAIN": 52.0,
    "NEW": 20.0,
}
TARGETS = ["KEEP", "NEW"]
VERDICTS = [
    ExitVerdict("KEEP", sell=True, reason="P/E 21.0 above the market's 18.0"),
    ExitVerdict("GONE", sell=False, reason="still 30% below intrinsic value"),
    ExitVerdict("BROKE", sell=True, reason="no longer passes the quality gates"),
    ExitVerdict("YOUNG", sell=True, reason="thesis broke"),
    # Not held: a strategy may say something about a name the book does
    # not own, and the runner must not invent a position to journal.
    ExitVerdict("GHOST", sell=True, reason="never owned"),
]


def _book() -> list[Position]:
    return [
        _position("KEEP", days=200),
        _position("GONE", days=200),
        _position("BROKE", days=200),
        _position("YOUNG", days=12),
        _position("PLAIN", days=200),
    ]


def _run(root: Path, monkeypatch: pytest.MonkeyPatch, adapter: _Adapter):  # type: ignore[no-untyped-def]
    runner = _runner(root, monkeypatch, PRICES)
    _seed(runner, _book())
    result = runner._run_one(
        adapter,  # type: ignore[arg-type]
        AS_OF,
        list(PRICES),
        dict(PRICES),
        dict.fromkeys(PRICES),
    )
    return runner, result


def _rows(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted((root / "stub_agent").glob("*.json")):
        payload = json.loads(path.read_text())
        out.extend(payload if isinstance(payload, list) else [payload])
    return out


def _book_state(portfolio: LivePortfolio) -> dict[str, Any]:
    """Everything about a book except the wall-clock stamps."""
    state = portfolio.to_dict()
    for stamp in ("last_updated", "last_open_run", "last_close_run"):
        state.pop(stamp, None)
    return state


# ---------------------------------------------------------------------------
# Shadow: the proof that nothing about trading moved
# ---------------------------------------------------------------------------
class TestShadowTradesAreUnchanged:
    def test_the_same_day_trades_identically_with_and_without_verdicts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, without = _run(tmp_path / "a", monkeypatch, _Adapter(TARGETS, []))
        _, shadow = _run(
            tmp_path / "b", monkeypatch, _Adapter(TARGETS, VERDICTS, mode="shadow")
        )

        assert [t.to_dict() for t in shadow.trades] == [
            t.to_dict() for t in without.trades
        ]
        assert _book_state(shadow.portfolio) == _book_state(without.portfolio)
        assert read_ledger("stub_agent", directory=tmp_path / "b" / "trades") == read_ledger(
            "stub_agent", directory=tmp_path / "a" / "trades"
        )
        # And the scenario is not vacuous: shadow saw sells it ignored
        # and a keep it overrode.
        sold = {t.ticker for t in shadow.trades if t.side == "SELL"}
        assert sold == {"GONE", "BROKE", "PLAIN"}
        assert shadow.portfolio.has("KEEP")

    def test_the_default_mode_of_a_bare_adapter_is_shadow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An adapter that never declares a mode is in shadow."""
        adapter = _Adapter(TARGETS, VERDICTS)
        del adapter.exit_mode
        _, bare = _run(tmp_path / "a", monkeypatch, adapter)
        _, without = _run(tmp_path / "b", monkeypatch, _Adapter(TARGETS, []))
        assert [t.to_dict() for t in bare.trades] == [
            t.to_dict() for t in without.trades
        ]

    def test_a_journal_that_cannot_be_written_does_not_touch_trading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Broken(DecisionLogger):
            def log(self, decision):  # type: ignore[no-untyped-def]
                raise OSError("disk full")

        runner = _runner(tmp_path / "a", monkeypatch, PRICES)
        runner.exit_verdict_logger = _Broken(root=tmp_path / "a" / "x")
        _seed(runner, _book())
        with _logged("WARNING") as messages:
            broken = runner._run_one(
                _Adapter(TARGETS, VERDICTS),  # type: ignore[arg-type]
                AS_OF,
                list(PRICES),
                dict(PRICES),
                dict.fromkeys(PRICES),
            )
        _, without = _run(tmp_path / "b", monkeypatch, _Adapter(TARGETS, []))

        assert [t.to_dict() for t in broken.trades] == [
            t.to_dict() for t in without.trades
        ]
        assert any("doctrine exit journal" in m for m in messages)


# ---------------------------------------------------------------------------
# Shadow: the journal
# ---------------------------------------------------------------------------
class TestShadowJournal:
    def test_each_held_verdict_is_journaled_beside_what_the_engine_did(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _run(tmp_path, monkeypatch, _Adapter(TARGETS, VERDICTS, mode="shadow"))

        rows = {r["ticker"]: r for r in _rows(tmp_path / "doctrine_exits")}
        assert set(rows) == {"KEEP", "GONE", "BROKE", "YOUNG"}

        assert rows["KEEP"]["decision"] == "SELL"
        assert rows["KEEP"]["market_conditions"] == {
            "exit_mode": "shadow",
            "engine_action": "kept",
        }
        assert rows["KEEP"]["criteria_met"] == ["P/E 21.0 above the market's 18.0"]
        assert rows["KEEP"]["exit_trigger"] == "P/E 21.0 above the market's 18.0"

        assert rows["GONE"]["decision"] == "HOLD"
        assert rows["GONE"]["market_conditions"]["engine_action"] == "sold"
        assert rows["GONE"]["exit_trigger"] is None

        assert rows["BROKE"]["market_conditions"]["engine_action"] == "sold"
        assert rows["YOUNG"]["market_conditions"]["engine_action"] == "kept"
        assert all(r["timestamp"].startswith("2026-08-05") for r in rows.values())

    def test_the_journal_stays_out_of_the_decision_journal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A shadow SELL in the dashboard's journal would read as a sale."""
        _run(tmp_path, monkeypatch, _Adapter(TARGETS, VERDICTS, mode="shadow"))
        decisions = {r["decision"] for r in _rows(tmp_path / "decisions")}
        assert "SELL" not in decisions
        assert "HOLD" not in decisions

    def test_a_silent_doctrine_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _run(tmp_path, monkeypatch, _Adapter(TARGETS, []))
        assert _rows(tmp_path / "doctrine_exits") == []


class TestExitRowNamesTheReason:
    def test_an_exit_the_doctrine_called_carries_its_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _run(tmp_path, monkeypatch, _Adapter(TARGETS, VERDICTS, mode="shadow"))
        exits = {r["ticker"]: r for r in _rows(tmp_path / "decisions") if r["decision"] == "EXIT"}

        broke = exits["BROKE"]
        assert broke["criteria_met"] == [
            "no longer passes the quality gates",
            "left today's target list",
        ]
        assert broke["exit_trigger"] == "no longer passes the quality gates"
        assert broke["market_conditions"] == {
            "doctrine_exit_mode": "shadow",
            "doctrine_verdict": "sell",
            "doctrine_reason": "no longer passes the quality gates",
        }

    def test_an_exit_the_doctrine_opposed_says_so(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _run(tmp_path, monkeypatch, _Adapter(TARGETS, VERDICTS, mode="shadow"))
        exits = {r["ticker"]: r for r in _rows(tmp_path / "decisions") if r["decision"] == "EXIT"}

        gone = exits["GONE"]
        assert gone["criteria_met"] == ["left today's target list"]
        assert gone["exit_trigger"] is None
        assert gone["market_conditions"]["doctrine_verdict"] == "keep"
        assert gone["market_conditions"]["doctrine_reason"] == "still 30% below intrinsic value"

    def test_an_exit_with_no_verdict_is_written_as_before(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _run(tmp_path, monkeypatch, _Adapter(TARGETS, VERDICTS, mode="shadow"))
        exits = {r["ticker"]: r for r in _rows(tmp_path / "decisions") if r["decision"] == "EXIT"}

        plain = exits["PLAIN"]
        assert plain["criteria_met"] == ["left today's target list"]
        assert plain["exit_trigger"] is None
        assert plain["market_conditions"] == {}


# ---------------------------------------------------------------------------
# Enforce: the verdict governs the held name
# ---------------------------------------------------------------------------
class TestEnforce:
    def test_the_verdict_decides_and_the_floor_still_holds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, result = _run(
            tmp_path, monkeypatch, _Adapter(TARGETS, VERDICTS, mode="enforce")
        )
        sold = {t.ticker for t in result.trades if t.side == "SELL"}
        bought = {t.ticker for t in result.trades if t.side == "BUY"}

        # KEEP: doctrine sell beats being a target, and is not rebought.
        assert "KEEP" in sold and "KEEP" not in bought
        assert not result.portfolio.has("KEEP")
        # GONE: doctrine keep beats leaving the list.
        assert result.portfolio.has("GONE")
        # BROKE and PLAIN sell as before; YOUNG waits out the floor.
        assert {"BROKE", "PLAIN"} <= sold
        assert result.portfolio.has("YOUNG")

        rows = {r["ticker"]: r for r in _rows(tmp_path / "doctrine_exits")}
        assert rows["KEEP"]["market_conditions"] == {
            "exit_mode": "enforce",
            "engine_action": "sold",
        }
        assert rows["GONE"]["market_conditions"]["engine_action"] == "kept"

        exits = {r["ticker"]: r for r in _rows(tmp_path / "decisions") if r["decision"] == "EXIT"}
        # It never left the list, so the row does not claim it did.
        assert exits["KEEP"]["criteria_met"] == ["P/E 21.0 above the market's 18.0"]

    def test_an_empty_scan_still_never_liquidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed scan's verdicts are as unreliable as its targets."""
        runner = _runner(tmp_path, monkeypatch, PRICES)
        _seed(runner, [_position("BROKE", days=200)])
        result = runner._run_one(
            _Adapter([], [ExitVerdict("BROKE", True, "no longer passes the quality gates")], mode="enforce"),  # type: ignore[arg-type]
            AS_OF,
            list(PRICES),
            dict(PRICES),
            dict.fromkeys(PRICES),
        )
        assert result.trades == []
        assert result.portfolio.has("BROKE")

    def test_a_forced_exit_beats_a_doctrine_keep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _runner(tmp_path, monkeypatch, PRICES)
        _seed(runner, [_position("GONE", days=200)])
        result = runner._run_one(
            _Adapter(
                ["NEW"],
                [ExitVerdict("GONE", False, "still cheap")],
                mode="enforce",
                forced=["GONE"],
            ),  # type: ignore[arg-type]
            AS_OF,
            list(PRICES),
            dict(PRICES),
            dict.fromkeys(PRICES),
        )
        assert "GONE" in {t.ticker for t in result.trades if t.side == "SELL"}

    def test_the_only_target_sold_by_doctrine_still_runs_the_sell_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Removing the doctrine's sells must not turn the scan into a no-trade day."""
        runner = _runner(tmp_path, monkeypatch, PRICES)
        _seed(runner, [_position("KEEP", days=200), _position("PLAIN", days=200)])
        result = runner._run_one(
            _Adapter(["KEEP"], [ExitVerdict("KEEP", True, "fair value reached")], mode="enforce"),  # type: ignore[arg-type]
            AS_OF,
            list(PRICES),
            dict(PRICES),
            dict.fromkeys(PRICES),
        )
        assert {t.ticker for t in result.trades if t.side == "SELL"} == {"KEEP", "PLAIN"}


# ---------------------------------------------------------------------------
# The verdict type and the mode switch
# ---------------------------------------------------------------------------
class TestVerdictAndMode:
    def test_a_verdict_needs_a_reason(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            ExitVerdict("AAA", sell=True, reason="   ")

    def test_a_verdict_needs_a_ticker(self) -> None:
        with pytest.raises(ValueError, match="ticker"):
            ExitVerdict("", sell=True, reason="x")

    def test_sell_must_be_a_bool(self) -> None:
        with pytest.raises(TypeError, match="sell"):
            ExitVerdict("AAA", sell="yes", reason="x")  # type: ignore[arg-type]

    def test_an_unknown_mode_is_refused(self) -> None:
        with pytest.raises(ValueError, match="enforce"):
            parse_exit_mode("enforced")

    def test_every_agent_starts_in_shadow(self) -> None:
        assert DEFAULT_EXIT_MODE == "shadow"
        assert dict(DOCTRINE_EXIT_MODES) == {}
        assert exit_mode_for("peter_lynch") == "shadow"

    def test_the_owner_flips_one_agent_at_a_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            doctrine_exits, "DOCTRINE_EXIT_MODES", {"peter_lynch": "enforce"}
        )
        assert exit_mode_for("peter_lynch") == "enforce"
        assert exit_mode_for("warren_buffett") == "shadow"


# ---------------------------------------------------------------------------
# The adapter reads the strategy's verdicts
# ---------------------------------------------------------------------------
@dataclass
class _Selection:
    top_scores: list[Any]


class _Strategy:
    name = "stub_agent"

    def __init__(self, verdicts: object = None) -> None:
        if verdicts is not None:
            self.last_exit_decisions = verdicts
        self.selection_history: list[_Selection] = []

    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: object,
        fundamentals: object,
        *,
        held: object = None,
    ) -> dict[str, float]:
        self.selection_history.append(_Selection(top_scores=[]))
        return {}


class _PlainAdapter(AgentAdapter):
    name = "stub_agent"

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        return []

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        return []


def _scan(adapter: AgentAdapter) -> ScanResult:
    return adapter.run_scan(AS_OF, [], {}, {}, held={})  # type: ignore[arg-type]


class TestAdapterReadsVerdicts:
    def test_the_strategy_verdicts_reach_the_scan(self) -> None:
        verdicts = [ExitVerdict("AAA", True, "fair value reached")]
        scan = _scan(_PlainAdapter(_Strategy(verdicts)))  # type: ignore[arg-type]
        assert scan.exit_verdicts == verdicts

    def test_a_strategy_with_no_opinion_gives_none(self) -> None:
        assert _scan(_PlainAdapter(_Strategy())).exit_verdicts == []  # type: ignore[arg-type]

    def test_a_malformed_verdict_is_dropped_loudly_in_shadow(self) -> None:
        good = ExitVerdict("AAA", True, "fair value reached")
        adapter = _PlainAdapter(_Strategy([good, ("BBB", True)]))  # type: ignore[arg-type]
        with _logged("ERROR") as messages:
            scan = _scan(adapter)
        assert scan.exit_verdicts == [good]
        assert any("malformed exit verdict" in m for m in messages)

    def test_a_malformed_verdict_stops_an_enforcing_agent(self) -> None:
        adapter = _PlainAdapter(_Strategy([("BBB", True)]), exit_mode="enforce")  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="malformed exit verdict"):
            _scan(adapter)

    def test_the_adapter_mode_comes_from_the_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            doctrine_exits, "DOCTRINE_EXIT_MODES", {"stub_agent": "enforce"}
        )
        assert _PlainAdapter(_Strategy()).exit_mode == "enforce"  # type: ignore[arg-type]
        assert _PlainAdapter(_Strategy(), exit_mode="shadow").exit_mode == "shadow"  # type: ignore[arg-type]

    def test_an_adapter_refuses_an_unknown_mode(self) -> None:
        with pytest.raises(ValueError):
            _PlainAdapter(_Strategy(), exit_mode="on")  # type: ignore[arg-type]

    def test_lynch_publishes_the_verdicts_it_already_computes(self) -> None:
        """``last_exits`` was written every run and read by nothing."""

        class _Lynch(_Strategy):
            name = "peter_lynch"

            def select(
                self,
                as_of: date,
                universe: list[str],
                prices: object,
                fundamentals: object,
                *,
                held: object = None,
            ) -> dict[str, float]:
                self.last_exits = [
                    ExitDecision("CRS", False, "no longer passes the quality gates"),
                    ExitDecision("EXEL", True, "Fast Grower at PEG 0.50 — still within the story"),
                ]
                return super().select(as_of, universe, prices, fundamentals, held=held)

        scan = _scan(LynchLive(_Lynch()))  # type: ignore[arg-type]
        assert scan.exit_verdicts == [
            ExitVerdict("CRS", True, "no longer passes the quality gates"),
            ExitVerdict("EXEL", False, "Fast Grower at PEG 0.50 — still within the story"),
        ]
        assert LynchLive(_Lynch()).exit_mode == "shadow"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# A failing shadow verdict does not cost the agent its day (audit L3)
# ---------------------------------------------------------------------------
def _strategies(cache_dir: Path) -> list[tuple[str, Any, Any]]:
    """Every strategy that computes verdicts inside ``select``.

    (exits module whose ``verdicts`` is patched, the strategy, the
    agent slug the error must name.)
    """
    from agents.buffett import wonderful_business
    from agents.dreman import contrarian
    from agents.fisher import quality_growth
    from agents.graham import net_net
    from agents.klarman import margin_of_safety
    from agents.marks import cycle_value
    from agents.neff import total_return
    from agents.schloss import deep_value
    from core.data.edgar_cache import EdgarCache

    cache = EdgarCache(cache_dir=cache_dir)
    return [
        (wonderful_business.buffett_exits, wonderful_business.WarrenBuffett(edgar_cache=cache), "warren_buffett"),
        (contrarian.dreman_exits, contrarian.DavidDreman(edgar_cache=cache), "david_dreman"),
        (quality_growth.fisher_exits, quality_growth.PhilipFisher(edgar_cache=cache), "philip_fisher"),
        (net_net.graham_exits, net_net.BenjaminGraham(edgar_cache=cache), "benjamin_graham"),
        (margin_of_safety.klarman_exits, margin_of_safety.SethKlarman(edgar_cache=cache), "seth_klarman"),
        (cycle_value.marks_exits, cycle_value.HowardMarks(edgar_cache=cache), "howard_marks"),
        (total_return.neff_exits, total_return.JohnNeff(edgar_cache=cache), "john_neff"),
        (deep_value.schloss_exits, deep_value.WalterSchloss(edgar_cache=cache), "walter_schloss"),
    ]


_STRATEGY_IDS = [
    "warren_buffett",
    "david_dreman",
    "philip_fisher",
    "benjamin_graham",
    "seth_klarman",
    "howard_marks",
    "john_neff",
    "walter_schloss",
]


class _NoFundamentals:
    def get(self, ticker: str) -> None:
        return None


class _NoPrices:
    def get(self, ticker: str) -> None:
        return None


def _boom(*args: object, **kwargs: object) -> list[ExitVerdict]:
    raise RuntimeError("verdict exploded")


class TestShadowVerdictGuard:
    @pytest.mark.parametrize("index", range(8), ids=_STRATEGY_IDS)
    def test_a_raising_shadow_verdict_still_returns_the_days_weights(
        self, index: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exits_module, strategy, agent = _strategies(tmp_path)[index]
        monkeypatch.setattr(exits_module, "verdicts", _boom)
        strategy.last_exit_decisions = [ExitVerdict("OLD", True, "yesterday")]

        with _logged("ERROR") as errors:
            weights = strategy.select(
                AS_OF, [], _NoPrices(), _NoFundamentals(), held={}
            )

        assert weights == {}
        # Yesterday's verdicts are not passed off as today's.
        assert strategy.last_exit_decisions == []
        (message,) = errors
        assert agent in message
        assert "verdict exploded" in message
        assert "Traceback" in message

    def test_an_enforcing_agent_still_stops(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import MappingProxyType

        monkeypatch.setattr(
            doctrine_exits,
            "DOCTRINE_EXIT_MODES",
            MappingProxyType({"howard_marks": "enforce"}),
        )
        with pytest.raises(RuntimeError, match="verdict exploded"):
            doctrine_exits.guarded_verdicts("howard_marks", _boom)

    def test_a_working_verdict_passes_through(self) -> None:
        verdicts = [ExitVerdict("AAA", False, "still cheap")]
        assert doctrine_exits.guarded_verdicts("howard_marks", lambda: verdicts) == verdicts


# ---------------------------------------------------------------------------
# The playbooks say what runs
# ---------------------------------------------------------------------------
_PLAYBOOKS = sorted((REPO / "agents").glob("*/playbook.md"))


def test_there_are_playbooks_to_check() -> None:
    assert len(_PLAYBOOKS) >= 10


@pytest.mark.parametrize("path", _PLAYBOOKS, ids=lambda p: p.parent.name)
def test_each_playbook_exit_section_says_it_runs_in_shadow(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index("## 7. Exit Rules")
    end = text.find("\n## 8.", start)
    section = text[start : end if end != -1 else len(text)]
    assert "exits run in shadow until enforced" in section

