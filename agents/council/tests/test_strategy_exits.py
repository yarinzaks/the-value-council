"""What the strategy hands the runner when a holding must go.

E2 is the only exit that fires on the statistical path, and it has to
reach the runner as a named forced exit rather than as an absence from
the target list — otherwise the thirty-day holding floor delays a
terminal filing by three more weeks.
"""

from __future__ import annotations

from datetime import date, timedelta

from agents.council.assemble import Assembled
from agents.council.rank import RankInputs
from agents.council.screen import Financials
from agents.council.strategy import MohnishPabrai
from agents.council.universe import UniverseInputs
from core.backtest.strategy_runner import HeldPosition

AS_OF = date(2026, 8, 14)


def row(ticker: str, *, filed_days_ago: int = 45) -> Assembled:
    return Assembled(
        ticker=ticker,
        universe=UniverseInputs(
            ticker=ticker,
            latest_filing=AS_OF - timedelta(days=filed_days_ago),
        ),
        financials=Financials(ticker=ticker),
        rank=RankInputs(ticker=ticker),
    )


def held(ticker: str) -> dict[str, HeldPosition]:
    return {
        ticker: HeldPosition(
            ticker=ticker,
            shares=10.0,
            entry_price=50.0,
            entry_date=AS_OF - timedelta(days=12),
            current_price=48.0,
        )
    }


def strategy(**kw) -> MohnishPabrai:
    return MohnishPabrai(**kw)


class TestForcedExits:
    def test_a_quiet_holding_forces_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr("agents.council.events.scan", lambda *a, **k: [])
        s = strategy()
        s._evaluate_exits(["AAA"], [row("AAA")], AS_OF, held=held("AAA"))
        assert s.last_forced_exits == []

    def test_stale_filings_force_an_exit(self, monkeypatch) -> None:
        monkeypatch.setattr("agents.council.events.scan", lambda *a, **k: [])
        s = strategy()
        s._evaluate_exits(
            ["AAA"], [row("AAA", filed_days_ago=401)], AS_OF, held=held("AAA")
        )
        assert s.last_forced_exits == ["AAA"]
        assert "E2" in s.last_exit_reasons["AAA"]

    def test_a_terminal_filing_forces_an_exit(self, monkeypatch) -> None:
        class _Event:
            ticker, form, code = "AAA", "25", "25"
            meaning = "delisting"

            @property
            def severity(self):
                from agents.council.events import Severity

                return Severity.CRITICAL

        monkeypatch.setattr(
            "agents.council.events.scan", lambda *a, **k: [_Event()]
        )
        s = strategy()
        s._evaluate_exits(["AAA"], [row("AAA")], AS_OF, held=held("AAA"))
        assert s.last_forced_exits == ["AAA"]

    def test_a_filings_outage_does_not_invent_an_exit(self, monkeypatch) -> None:
        def boom(*a, **k):
            raise OSError("SEC unreachable")

        monkeypatch.setattr("agents.council.events.scan", boom)
        s = strategy()
        s._evaluate_exits(["AAA"], [row("AAA")], AS_OF, held=held("AAA"))
        assert s.last_forced_exits == []

    def test_an_empty_book_costs_no_request(self, monkeypatch) -> None:
        calls: list[object] = []
        monkeypatch.setattr(
            "agents.council.events.scan",
            lambda *a, **k: calls.append(a) or [],
        )
        s = strategy()
        s._evaluate_exits([], [], AS_OF, held={})
        assert calls == []
        assert s.last_forced_exits == []

    def test_each_run_clears_the_previous_verdicts(self, monkeypatch) -> None:
        monkeypatch.setattr("agents.council.events.scan", lambda *a, **k: [])
        s = strategy()
        s._evaluate_exits(
            ["AAA"], [row("AAA", filed_days_ago=401)], AS_OF, held=held("AAA")
        )
        assert s.last_forced_exits == ["AAA"]
        s._evaluate_exits(["AAA"], [row("AAA")], AS_OF, held=held("AAA"))
        assert s.last_forced_exits == []


class TestAdapterSurfacesThem:
    def test_the_scan_result_carries_the_forced_exits(self) -> None:
        from core.live.agent_adapter import PabraiLive

        class _Stub:
            name = "mohnish_pabrai"
            last_selection = None

            def __init__(self) -> None:
                self.last_forced_exits = ["BAD"]

            def select(self, *a, **k):
                return {}

        adapter = PabraiLive(_Stub())  # type: ignore[arg-type]
        result = adapter.run_scan(AS_OF, [], object(), object(), held=None)
        assert result.forced_exits == ["BAD"]

    def test_a_strategy_with_no_opinion_forces_nothing(self) -> None:
        from core.live.agent_adapter import PabraiLive

        class _Stub:
            name = "mohnish_pabrai"
            last_selection = None

            def select(self, *a, **k):
                return {}

        adapter = PabraiLive(_Stub())  # type: ignore[arg-type]
        result = adapter.run_scan(AS_OF, [], object(), object(), held=None)
        assert result.forced_exits == []
