"""Heartbeat and close. Neither may trade — that is the point of both."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agents.council.journal import Classification, Journal, KillCriterion, Thesis
from agents.council.regime import Regime, Signal, Stance
from agents.council.runs import Book, close, heartbeat, write_result

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
