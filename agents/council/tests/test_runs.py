"""Heartbeat and close. Neither may trade — that is the point of both."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

import agents.council.strategy as strategy_mod
from agents.council.assemble import Assembled
from agents.council.cadence import RunType
from agents.council.journal import Classification, Journal, KillCriterion, Thesis
from agents.council.pipeline import Selection
from agents.council.published_list import PublishedList, publish
from agents.council.rank import Ranked, RankInputs
from agents.council.regime import Regime, Signal, Stance
from agents.council.runs import Book, close, heartbeat, write_result
from agents.council.screen import Financials, ScreenResult
from agents.council.universe import UniverseInputs
from core.backtest.decision_logger import (
    Decision,
    DecisionLogger,
    DecisionLoggerError,
)
from core.backtest.strategy_runner import HeldPosition
from core.live.agent_adapter import PabraiLive
from core.live.portfolio import LivePortfolio, Position
from core.live.runner import AgentRunResult, DailyRunner, band_for

SINCE = date(2026, 6, 1)


def _cik(ticker: str) -> int:
    return 1


def _no_filings(cik: int) -> dict:
    return {"filings": {"recent": {"form": [], "filingDate": [], "items": [],
                                   "accessionNumber": []}}}


def _book(**kw) -> Book:
    """A book that breaches nothing: two 30% names, 40% cash.

    Sized deliberately under every Part 4 limit so that a test asserting
    all_clear is asserting the run's logic rather than the fixture's.
    """
    base = {
        "nav": 10_000.0,
        "cash": 4_000.0,
        "peak_nav": 10_000.0,
        "positions": [
            {"ticker": "AAA", "shares": 100.0, "current_price": 30.0},
            {"ticker": "BBB", "shares": 100.0, "current_price": 30.0},
        ],
        "adv": {"AAA": 9e6, "BBB": 9e6},
        "clusters": {"AAA": "one", "BBB": "two"},
    }
    base.update(kw)
    return Book(**base)  # type: ignore[arg-type]


def _regime(risk_on: int) -> Regime:
    stances = [Stance.RISK_ON] * risk_on + [Stance.RISK_OFF] * (4 - risk_on)
    return Regime(
        signals=tuple(
            Signal(series=f"S{i}", stance=s, value=1.0, as_of=SINCE,
                   threshold=0.0, reason="fixture")
            for i, s in enumerate(stances)
        ),
        as_of=SINCE,
    )


class TestHeartbeat:
    def test_a_clean_book_is_all_clear(self) -> None:
        result = heartbeat(_book(), since=SINCE, fetch=_no_filings, resolve_cik=_cik)
        assert result["all_clear"] is True
        assert result["breaches"] == []
        assert result["circuit_breaker"] is False

    def test_it_reports_a_breach_without_acting_on_it(self) -> None:
        """This run may not trade. A forced exit is proposed, never taken."""
        book = _book(
            positions=[{"ticker": "AAA", "shares": 100.0, "current_price": 45.0}],
            cash=5_500.0,
            adv={"AAA": 9e6},
            clusters={"AAA": "one"},
        )
        result = heartbeat(book, since=SINCE, fetch=_no_filings, resolve_cik=_cik)
        assert result["all_clear"] is False
        # A 45% single name is past the 35% trim level.
        assert any("trim_above" in b["limit"] for b in result["breaches"])
        assert result["forced_exits_proposed"]
        assert "orders" not in result

    def test_the_circuit_breaker_reports_but_forces_nothing(self) -> None:
        result = heartbeat(
            _book(nav=7_000.0, peak_nav=10_000.0),
            since=SINCE,
            fetch=_no_filings,
            resolve_cik=_cik,
        )
        assert result["circuit_breaker"] is True
        assert result["drawdown_from_peak"] == pytest.approx(-0.30)
        # It blocks entry; it never liquidates.
        assert all(
            not c["forces_action"]
            for c in result["limits"]
            if c["limit"] == "drawdown_circuit_breaker"
        )

    def test_unmeasurable_limits_break_all_clear(self) -> None:
        """A limit that could not be judged is not a pass."""
        book = _book(adv={}, clusters={})
        result = heartbeat(book, since=SINCE, fetch=_no_filings, resolve_cik=_cik)
        assert result["all_clear"] is False
        assert result["unknown_limits"]

    def test_kill_criteria_are_listed_as_unevaluated(self, tmp_path: Path) -> None:
        """Free text set at entry. Reporting them as not-triggered would
        be the failure this run exists to prevent."""
        j = Journal(directory=tmp_path)
        j.record(
            Thesis(
                ticker="AAA",
                opened=SINCE,
                classification=Classification.UNDERSTANDING,
                claim="c",
                structural_floor="net cash",
                second_order_belief="b",
                probability=0.6,
                size=0.1,
                kill_criteria=tuple(
                    KillCriterion(condition=f"k{i}", measured_in="10-Q",
                                  action="exit")
                    for i in range(3)
                ),
            )
        )
        result = heartbeat(
            _book(), journal=j, since=SINCE, fetch=_no_filings, resolve_cik=_cik
        )
        assert len(result["kill_criteria"]) == 3
        assert {k["state"] for k in result["kill_criteria"]} == {"NOT_EVALUATED"}


class TestClose:
    def test_it_reads_the_dial_and_queues_nothing_on_a_quiet_day(self) -> None:
        result = close(
            _book(),
            regime=_regime(3),
            since=SINCE,
            fetch=_no_filings,
            resolve_cik=_cik,
        )
        assert result["regime"]["risk_on_count"] == 3
        assert result["queued_for_reading"] == []
        assert result["grounds_triggered"] == []

    def test_a_critical_filing_on_a_holding_queues_it(self) -> None:
        def fetch(cik: int) -> dict:
            return {
                "filings": {
                    "recent": {
                        "form": ["8-K"],
                        "filingDate": ["2026-06-15"],
                        "items": ["2.01"],
                        "accessionNumber": ["acc-0"],
                    }
                }
            }

        result = close(
            _book(), regime=_regime(4), since=SINCE, fetch=fetch, resolve_cik=_cik
        )
        assert result["queued_for_reading"]
        assert result["grounds_triggered"][0]["ground"] == "holding_event"

    def test_it_reports_the_punch_card(self, tmp_path: Path) -> None:
        j = Journal(directory=tmp_path)
        result = close(
            _book(),
            journal=j,
            regime=_regime(2),
            since=SINCE,
            fetch=_no_filings,
            resolve_cik=_cik,
        )
        assert result["punch_card"] == {"used": 0, "remaining": 20}

    def test_it_never_produces_a_trade(self) -> None:
        result = close(
            _book(), regime=_regime(4), since=SINCE,
            fetch=_no_filings, resolve_cik=_cik
        )
        assert "orders" not in result
        assert "weights" not in result


class TestPersistence:
    def test_a_run_is_written_once_per_day(self, tmp_path: Path) -> None:
        result = heartbeat(_book(), since=SINCE, fetch=_no_filings, resolve_cik=_cik)
        write_result(result, directory=tmp_path)
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        assert files[0].name.endswith("_heartbeat.json")
        assert json.loads(files[0].read_text())["run"] == "heartbeat"


# ----------------------------------------------------------------------
# Audit item 19: the runs reach the path that trades.
#
# The two classes above test the Part 7 records. These drive the live
# runner with the real PabraiLive adapter, because that is where §6's
# "nothing else sells" was being broken: E8 is a quarterly rule, the
# quarterly rank ran every session, and a held name that slipped past
# rank 40 on a Tuesday was sold on the Tuesday (YELP 10-06, ZUMZ 10-26,
# CRTO/HOG/CLMB 10-30, NATR 11-10, all "left today's target list").
# ----------------------------------------------------------------------

#: A Wednesday in a rebalance month; the 3rd was that week's Monday.
RUN_DAY = date(2026, 8, 5)


class _Prices:
    """The runner's price loader, answering from a dict and nothing else."""

    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def get_price_on(self, ticker, as_of, *, force_refresh=False):  # type: ignore[no-untyped-def]
        return self._prices.get(ticker)

    def cached_range(self, ticker):  # type: ignore[no-untyped-def]
        return None

    def splits_between(self, ticker, start, end):  # type: ignore[no-untyped-def]
        return []

    def dividends_between(self, ticker, start, end):  # type: ignore[no-untyped-def]
        return []


class _Strategy:
    """What PabraiLive reads off MohnishPabrai, fixed by the test."""

    name = "mohnish_pabrai"
    last_selection = None

    def __init__(self, weights: dict[str, float], forced: list[str] = ()) -> None:  # type: ignore[assignment]
        self._weights = weights
        self.last_forced_exits = list(forced)

    def select(self, *a, **k):  # type: ignore[no-untyped-def]
        return dict(self._weights)


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DailyRunner:
    """A runner whose every write lands under tmp_path."""
    monkeypatch.setattr("core.live.runner.save_snapshot", lambda snap: None)
    return DailyRunner(
        market="US",
        adapters=[],
        portfolio_dir=tmp_path / "portfolios",
        published_dir=tmp_path / "published",
        ledger_dir=tmp_path / "trades",
        price_loader=_Prices({}),  # type: ignore[arg-type]
        universe=object(),  # type: ignore[arg-type]
        pit_loader=object(),  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
        decision_logger=DecisionLogger(root=tmp_path / "decisions"),
        exit_verdict_logger=DecisionLogger(root=tmp_path / "doctrine_exits"),
    )


def _position(ticker: str, *, shares: float, price: float) -> Position:
    # Opened well before the 30-day floor, so the floor is not what
    # keeps a name in any of these tests.
    return Position(
        ticker=ticker,
        shares=shares,
        entry_price=price,
        entry_date="2026-05-04",
        current_price=price,
        why_en="seeded",
        why_he="seeded",
    )


def _pabrai_book(
    live: DailyRunner,
    holdings: dict[str, tuple[float, float]],
    *,
    cash: float = 1_000.0,
    last_rebalance: str | None = "2026-08-03",
    last_open: str | None = "2026-08-04",
) -> None:
    book = LivePortfolio(agent="mohnish_pabrai", cash=cash)
    for ticker, (shares, price) in holdings.items():
        book.positions.append(_position(ticker, shares=shares, price=price))
    if last_rebalance is not None:
        book.last_rebalance_date = last_rebalance
    if last_open is not None:
        book.last_open_date = last_open
    book.save(directory=live.portfolio_dir)


def _run(
    live: DailyRunner,
    strategy: _Strategy,
    prices: dict[str, float],
    *,
    run_type: RunType | None,
) -> AgentRunResult:
    live.price_loader = _Prices(prices)  # type: ignore[assignment]
    return live._run_one(
        PabraiLive(strategy),  # type: ignore[arg-type]
        RUN_DAY,
        list(prices),
        dict(prices),
        dict.fromkeys(prices),
        run_type=run_type,
    )


def _sold(result: AgentRunResult) -> list[str]:
    return sorted(t.ticker for t in result.trades if t.side == "SELL")


class TestBetweenRebalancesOnlyForcedExitsSell:
    """§6: "Nothing else sells." E8 is evaluated at the quarterly rebalance."""

    def test_heartbeat_sells_only_forced_exits(self, live: DailyRunner) -> None:
        """KEEP still ranks, DRIFTED slipped past rank 40, BAD filed a 4.02.

        Only BAD may go. DRIFTED is E8's to decide, and E8 runs at the
        quarterly rebalance — not on a Wednesday heartbeat.
        """
        _pabrai_book(
            live,
            {"KEEP": (10.0, 50.0), "DRIFTED": (10.0, 50.0), "BAD": (10.0, 50.0)},
        )
        result = _run(
            live,
            _Strategy({"KEEP": 0.05}, forced=["BAD"]),
            {"KEEP": 50.0, "DRIFTED": 50.0, "BAD": 50.0},
            run_type=RunType.HEARTBEAT,
        )

        assert result.error is None
        assert _sold(result) == ["BAD"]
        assert result.portfolio.has("DRIFTED")
        assert result.portfolio.has("KEEP")

    def test_a_weekly_council_outside_the_rebalance_does_not_sell_on_rank(
        self, live: DailyRunner
    ) -> None:
        """The weekly council may exit — but E8 is still a quarterly rule."""
        _pabrai_book(live, {"KEEP": (10.0, 50.0), "DRIFTED": (10.0, 50.0)})
        result = _run(
            live,
            _Strategy({"KEEP": 0.05}),
            {"KEEP": 50.0, "DRIFTED": 50.0},
            run_type=RunType.COUNCIL,
        )

        assert _sold(result) == []
        assert result.portfolio.has("DRIFTED")

    def test_the_quarterly_rebalance_still_sells_what_left_the_list(
        self, live: DailyRunner
    ) -> None:
        """The hold is between rebalances only; E8 itself still fires."""
        _pabrai_book(
            live,
            {"KEEP": (10.0, 50.0), "DRIFTED": (10.0, 50.0)},
            last_rebalance="2026-05-04",
            last_open="2026-07-31",
        )
        publish(
            PublishedList(
                published_on=RUN_DAY - timedelta(days=3),
                as_of=RUN_DAY - timedelta(days=3),
                tickers=("KEEP",),
                weights={"KEEP": 0.05},
                note="the cooled-off list",
            ),
            directory=live.published_dir,
        )
        result = _run(
            live,
            _Strategy({"KEEP": 0.05}),
            {"KEEP": 50.0, "DRIFTED": 50.0},
            run_type=None,
        )

        assert result.portfolio.last_rebalance_date == RUN_DAY.isoformat()
        assert _sold(result) == ["DRIFTED"]

    def test_a_rebalance_with_nothing_published_sells_nothing(
        self, live: DailyRunner
    ) -> None:
        """No cooled-off list means no E8 — the book is held, not traded."""
        _pabrai_book(
            live,
            {"KEEP": (10.0, 50.0), "DRIFTED": (10.0, 50.0)},
            last_rebalance="2026-05-04",
            last_open="2026-07-31",
        )
        result = _run(
            live,
            _Strategy({"KEEP": 0.05}),
            {"KEEP": 50.0, "DRIFTED": 50.0},
            run_type=None,
        )

        assert _sold(result) == []
        assert result.portfolio.has("DRIFTED")


class TestTheRunnerDoesNotTrimTheCouncil:
    """SIG and REPX were cut on 10-30 by the runner's 25% band.

    E9 and E6 are the doctrine's only trims, and neither is a drift band.
    """

    def test_the_adapter_declares_no_band(self) -> None:
        adapter = PabraiLive(_Strategy({}))  # type: ignore[arg-type]
        assert band_for(adapter, default=0.25) is None

    def test_an_overweight_holding_is_not_trimmed(self, live: DailyRunner) -> None:
        # $5,000 of a name whose target is 5% of a ~$6,000 book.
        _pabrai_book(live, {"BIG": (100.0, 50.0)})
        result = _run(
            live,
            _Strategy({"BIG": 0.05}),
            {"BIG": 50.0},
            run_type=RunType.COUNCIL,
        )

        assert _sold(result) == []
        assert result.portfolio.positions[0].shares == 100.0


# ----------------------------------------------------------------------
# SF-X7: the twelfth agent wrote no intent rows at all.
# ----------------------------------------------------------------------


class _Loader:
    def median_dollar_volume(self, tickers, as_of, sessions):  # type: ignore[no-untyped-def]
        return {}

    def trailing_return(self, *a, **k):  # type: ignore[no-untyped-def]
        return None


def _selection_for(as_of: date) -> Selection:
    def ranked(ticker: str, composite: float) -> Ranked:
        return Ranked(
            ticker=ticker,
            composite=composite,
            value=None,
            quality=None,
            momentum=None,
            knife_guarded=False,
            sic2=None,
            insider_cluster=False,
        )

    basket = [ranked("AAA", 0.91), ranked("BBB", 0.80)]
    return Selection(
        as_of=as_of,
        weights={"AAA": 0.0225, "BBB": 0.0225},
        ranked=[*basket, ranked("CCC", 0.10)],
        screened={
            "AAA": ScreenResult(ticker="AAA", floor="net cash 60% of market cap"),
            "BBB": ScreenResult(ticker="BBB", floor="price below tangible book"),
        },
        basket=basket,
    )


@pytest.fixture
def quiet_council(monkeypatch: pytest.MonkeyPatch) -> None:
    """MohnishPabrai.select with no network and a fixed selection."""
    monkeypatch.setattr(
        "agents.council.strategy.assemble_universe", lambda *a, **k: []
    )
    monkeypatch.setattr(
        "agents.council.strategy.run_selection",
        lambda rows, as_of, **k: _selection_for(as_of),
    )
    monkeypatch.setattr("agents.council.events.scan", lambda *a, **k: [])


def _pabrai(
    tmp_path: Path, logger: DecisionLogger | None
) -> strategy_mod.MohnishPabrai:
    return strategy_mod.MohnishPabrai(
        edgar_cache=object(),
        price_loader=_Loader(),
        drawdown_reader=lambda: 0.0,
        reviews_dir=tmp_path / "reviews",
        decision_logger=logger,
    )


class TestIntentsAreJournaled:
    def test_intent_rows_are_journaled(
        self,
        tmp_path: Path,
        quiet_council: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """BUY per target, SELL per forced exit, each with its trigger."""
        journal = DecisionLogger(root=tmp_path / "decisions")
        s = _pabrai(tmp_path, journal)
        held = {
            "OLD": HeldPosition(
                ticker="OLD",
                shares=10.0,
                entry_price=40.0,
                entry_date=RUN_DAY - timedelta(days=200),
                current_price=41.0,
            )
        }
        # OLD's filings are 401 days old: E2 forces it out.
        stale = Assembled(
            ticker="OLD",
            universe=UniverseInputs(
                ticker="OLD", latest_filing=RUN_DAY - timedelta(days=401)
            ),
            financials=Financials(ticker="OLD"),
            rank=RankInputs(ticker="OLD"),
        )
        monkeypatch.setattr(strategy_mod, "assemble_universe", lambda *a, **k: [stale])

        prices = {"AAA": 12.0, "BBB": 8.0, "OLD": 41.0}
        weights = s.select(
            RUN_DAY, ["AAA", "BBB", "OLD"], prices, None, held=held
        )

        assert weights == {"AAA": 0.0225, "BBB": 0.0225}
        rows = journal.read_day("mohnish_pabrai", RUN_DAY)
        by = {(r.decision, r.ticker): r for r in rows}
        assert set(by) == {("BUY", "AAA"), ("BUY", "BBB"), ("SELL", "OLD")}

        aaa = by[("BUY", "AAA")]
        assert aaa.agent == "mohnish_pabrai"
        assert aaa.entry_price == 12.0
        assert "net cash 60% of market cap" in aaa.criteria_met
        assert aaa.criteria_values["composite_rank"] == 1
        assert aaa.criteria_values["target_weight"] == 0.0225
        assert aaa.exit_trigger is not None and "E8" in aaa.exit_trigger

        old = by[("SELL", "OLD")]
        assert any("E2" in c for c in old.criteria_met)
        assert old.entry_price == 41.0

    def test_no_journal_means_no_rows_and_the_same_weights(
        self, tmp_path: Path, quiet_council: None
    ) -> None:
        s = _pabrai(tmp_path, None)
        weights = s.select(RUN_DAY, ["AAA", "BBB"], {"AAA": 12.0}, None, held={})
        assert weights == {"AAA": 0.0225, "BBB": 0.0225}
        assert not (tmp_path / "decisions").exists()

    def test_a_journal_that_cannot_write_does_not_cost_the_run(
        self, tmp_path: Path, quiet_council: None
    ) -> None:
        class _Broken(DecisionLogger):
            def log(self, decision: Decision) -> None:
                raise DecisionLoggerError("disk full")

        # The project logs through loguru, which caplog never sees.
        seen: list[str] = []
        sink_id = loguru_logger.add(
            lambda m: seen.append(m.record["message"]), level="WARNING"
        )
        try:
            s = _pabrai(tmp_path, _Broken(root=tmp_path / "broken"))
            weights = s.select(RUN_DAY, ["AAA", "BBB"], {}, None, held={})
        finally:
            loguru_logger.remove(sink_id)

        assert weights == {"AAA": 0.0225, "BBB": 0.0225}
        assert sum("disk full" in m for m in seen) == 2

    def test_a_target_without_a_price_is_journaled_without_one(
        self, tmp_path: Path, quiet_council: None
    ) -> None:
        """The lookup raising is not a reason to drop the intent."""

        class _Raising:
            def get(self, ticker: str) -> float:
                raise KeyError(ticker)

        journal = DecisionLogger(root=tmp_path / "decisions")
        s = _pabrai(tmp_path, journal)
        s.select(RUN_DAY, ["AAA", "BBB"], _Raising(), None, held={})

        rows = journal.read_day("mohnish_pabrai", RUN_DAY)
        assert sorted(r.ticker for r in rows) == ["AAA", "BBB"]
        assert all(r.entry_price is None for r in rows)
