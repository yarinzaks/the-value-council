"""Unit tests for DecisionLogger."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from core.backtest.decision_logger import (
    VALID_DECISION_TYPES,
    Decision,
    DecisionLogger,
    DecisionLoggerError,
    make_decision,
)


def _decision(
    *,
    ticker: str = "AAPL",
    decision: str = "BUY",
    agent: str = "greenblatt",
    timestamp: str = "2024-06-30T12:00:00+00:00",
    **kwargs,
) -> Decision:
    return make_decision(
        ticker=ticker,
        decision=decision,  # type: ignore[arg-type]
        agent=agent,
        timestamp=timestamp,
        **kwargs,
    )


class TestDecisionConstruction:
    def test_basic(self) -> None:
        d = _decision()
        assert d.ticker == "AAPL"
        assert d.decision == "BUY"
        assert d.agent == "greenblatt"

    def test_normalizes_ticker_uppercase(self) -> None:
        d = make_decision(
            ticker="aapl",
            decision="BUY",
            agent="greenblatt",
            timestamp="2024-06-30T12:00:00+00:00",
        )
        assert d.ticker == "AAPL"

    def test_invalid_decision_raises(self) -> None:
        with pytest.raises(DecisionLoggerError):
            _decision(decision="MAYBE")

    def test_confidence_out_of_range(self) -> None:
        with pytest.raises(DecisionLoggerError):
            _decision(confidence=1.5)
        with pytest.raises(DecisionLoggerError):
            _decision(confidence=-0.1)

    def test_empty_ticker(self) -> None:
        with pytest.raises(DecisionLoggerError):
            make_decision(
                ticker="",
                decision="BUY",
                agent="greenblatt",
                timestamp="2024-06-30T12:00:00+00:00",
            )

    def test_empty_agent(self) -> None:
        with pytest.raises(DecisionLoggerError):
            make_decision(
                ticker="AAPL",
                decision="BUY",
                agent="",
                timestamp="2024-06-30T12:00:00+00:00",
            )

    def test_full_payload_round_trip(self) -> None:
        d = _decision(
            criteria_met=["P/B < 0.75", "D/E < 1.0"],
            criteria_failed=["dividend > 4%"],
            criteria_values={"P/B": 0.42, "D/E": 0.31, "ROE": 0.18},
            market_conditions={"SP500_PE": 21.4, "VIX": 14.5},
            confidence=0.75,
            entry_price=34.20,
            target_price=45.00,
            exit_trigger="P/E approaches market average",
            rationale="Schloss-grade name at 0.42 P/B with positive trailing earnings",
        )
        round_tripped = Decision.from_dict(d.to_dict())
        assert round_tripped == d


class TestValidDecisionTypes:
    def test_all_documented_types_accepted(self) -> None:
        for dt in VALID_DECISION_TYPES:
            d = _decision(decision=dt)
            assert d.decision == dt


class TestLog:
    def test_writes_to_correct_path(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        d = _decision(timestamp="2024-06-30T09:30:00+00:00")
        log.log(d)
        path = tmp_path / "greenblatt" / "2024-06-30.json"
        assert path.exists()

    def test_multiple_decisions_appended(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        for _i, t in enumerate(["AAPL", "MSFT", "GOOG"]):
            log.log(_decision(ticker=t, timestamp="2024-06-30T12:00:00+00:00"))
        loaded = log.read_day("greenblatt", date(2024, 6, 30))
        assert [d.ticker for d in loaded] == ["AAPL", "MSFT", "GOOG"]

    def test_different_dates_different_files(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        log.log(_decision(ticker="A", timestamp="2024-06-30T12:00:00+00:00"))
        log.log(_decision(ticker="B", timestamp="2024-07-01T12:00:00+00:00"))
        files = sorted((tmp_path / "greenblatt").iterdir())
        assert [f.name for f in files] == ["2024-06-30.json", "2024-07-01.json"]

    def test_different_agents_different_dirs(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        log.log(_decision(agent="greenblatt"))
        log.log(_decision(agent="schloss"))
        assert (tmp_path / "greenblatt").is_dir()
        assert (tmp_path / "schloss").is_dir()

    def test_invalid_timestamp_raises(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        with pytest.raises(DecisionLoggerError):
            log.log(_decision(timestamp="not-a-date"))

    def test_corrupt_file_recovers(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        # Pre-write garbage to where logger will append
        agent_dir = tmp_path / "greenblatt"
        agent_dir.mkdir()
        (agent_dir / "2024-06-30.json").write_text("{ not valid json")
        # log should overwrite cleanly
        log.log(_decision(timestamp="2024-06-30T12:00:00+00:00"))
        loaded = log.read_day("greenblatt", date(2024, 6, 30))
        assert len(loaded) == 1


class TestLogMany:
    def test_batch_appended(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        ds = [
            _decision(ticker=f"T{i}", timestamp="2024-06-30T12:00:00+00:00")
            for i in range(5)
        ]
        log.log_many(ds)
        loaded = log.read_day("greenblatt", date(2024, 6, 30))
        assert len(loaded) == 5


class TestRead:
    def test_read_missing_returns_empty(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        assert log.read_day("missing_agent", date(2024, 6, 30)) == []

    def test_read_all_for_agent(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        log.log(_decision(timestamp="2024-06-29T12:00:00+00:00"))
        log.log(_decision(timestamp="2024-06-30T12:00:00+00:00"))
        all_d = log.read_all_for_agent("greenblatt")
        assert len(all_d) == 2

    def test_agents_lists_subdirs(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        log.log(_decision(agent="a"))
        log.log(_decision(agent="b"))
        assert log.agents() == ["a", "b"]

    def test_stats_aggregates(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        # Distinct names: two byte-identical rows are now one row, and
        # this test is about the aggregation, not about that.
        log.log(_decision(ticker="AAPL", decision="BUY"))
        log.log(_decision(ticker="MSFT", decision="REJECT"))
        log.log(_decision(ticker="GOOG", decision="BUY"))
        s = log.stats()
        assert s["total_decisions"] == 3
        assert s["agents"]["greenblatt"]["by_decision"]["BUY"] == 2
        assert s["agents"]["greenblatt"]["by_decision"]["REJECT"] == 1


class TestThreadSafety:
    def test_concurrent_logs_dont_corrupt(self, tmp_path: Path) -> None:
        import threading

        log = DecisionLogger(root=tmp_path)

        def worker(agent: str, start: int, n: int) -> None:
            for i in range(start, start + n):
                log.log(
                    _decision(
                        ticker=f"{agent}_{i}",
                        agent=agent,
                        timestamp="2024-06-30T12:00:00+00:00",
                    )
                )

        # The two greenblatt threads take disjoint names. They used to
        # write the same fifty, which now collapses to fifty rows and
        # would hide the thing this test is for: two threads writing
        # one file must not lose or mangle a row.
        threads = [
            threading.Thread(target=worker, args=("greenblatt", 0, 50)),
            threading.Thread(target=worker, args=("greenblatt", 50, 50)),
            threading.Thread(target=worker, args=("schloss", 0, 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 100 entries to greenblatt, 50 to schloss — none lost
        assert len(log.read_all_for_agent("greenblatt")) == 100
        assert len(log.read_all_for_agent("schloss")) == 50


class TestNonFiniteValuesAreWritable:
    """A missing metric must not cost the dashboard the whole day.

    ``json.dumps`` writes a bare ``NaN`` by default. Python reads that
    back happily, so the backend never noticed; ``JSON.parse`` rejects
    it, and the dashboard's loader skips any file it cannot parse. Five
    agent-days went missing that way and the only visible symptom was a
    position card that listed six exits where the journal held seven.
    """

    def test_a_nan_metric_is_written_as_null(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        log.log(
            _decision(criteria_values={"p_ncav": float("nan"), "pe": 9.1})
        )

        path = tmp_path / "greenblatt" / "2024-06-30.json"
        text = path.read_text()
        assert "NaN" not in text
        assert json.loads(text)[0]["criteria_values"] == {
            "p_ncav": None,
            "pe": 9.1,
        }

    def test_infinities_are_written_as_null(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        log.log(
            _decision(
                criteria_values={
                    "up": float("inf"),
                    "down": float("-inf"),
                }
            )
        )

        text = (tmp_path / "greenblatt" / "2024-06-30.json").read_text()
        assert "Infinity" not in text
        assert json.loads(text)[0]["criteria_values"] == {
            "up": None,
            "down": None,
        }

    def test_a_nested_non_finite_is_reached(self, tmp_path: Path) -> None:
        # market_conditions is a free-form dict and nests in practice.
        log = DecisionLogger(root=tmp_path)
        log.log(
            _decision(
                market_conditions={"vix": {"close": float("nan")}, "spx": 1.0}
            )
        )

        text = (tmp_path / "greenblatt" / "2024-06-30.json").read_text()
        assert "NaN" not in text
        assert json.loads(text)[0]["market_conditions"] == {
            "vix": {"close": None},
            "spx": 1.0,
        }

    def test_the_file_stays_strict_json_across_appends(
        self, tmp_path: Path
    ) -> None:
        log = DecisionLogger(root=tmp_path)
        log.log(_decision(ticker="AAA", criteria_values={"x": 1.0}))
        log.log(_decision(ticker="BBB", criteria_values={"x": float("nan")}))
        log.log(_decision(ticker="CCC", criteria_values={"x": 2.0}))

        rows = json.loads((tmp_path / "greenblatt" / "2024-06-30.json").read_text())
        assert [r["ticker"] for r in rows] == ["AAA", "BBB", "CCC"]
        assert [r["criteria_values"]["x"] for r in rows] == [1.0, None, 2.0]


class TestARerunDoesNotDuplicate:
    """Two runs in one day are two readings of one verdict, not two.

    The US open and US close runs each scan the whole book and each
    log the strategy's intent, so every held name landed in the day's
    file twice, byte-identical. The dashboard renders the journal as
    written: the agent's recent-decisions list showed each ticker
    twice, and React warned about duplicate keys because two movements
    shared a date and a kind.

    Equality is on the whole payload. Two genuinely different events
    differ somewhere — price, confidence, rationale — so only a
    re-emission of the identical row is dropped.
    """

    def test_the_same_row_twice_is_stored_once(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        d = _decision(ticker="INGR", criteria_values={"pe": 8.74})
        log.log(d)
        log.log(d)

        assert len(log.read_day("greenblatt", date(2024, 6, 30))) == 1

    def test_a_changed_value_is_a_new_row(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        log.log(_decision(ticker="INGR", criteria_values={"pe": 8.74}))
        log.log(_decision(ticker="INGR", criteria_values={"pe": 8.80}))

        assert len(log.read_day("greenblatt", date(2024, 6, 30))) == 2

    def test_a_different_decision_on_the_same_name_is_kept(
        self, tmp_path: Path
    ) -> None:
        # The intent and its execution are two rows by design.
        log = DecisionLogger(root=tmp_path)
        log.log(_decision(ticker="INGR", decision="BUY"))
        log.log(_decision(ticker="INGR", decision="FILL"))

        loaded = log.read_day("greenblatt", date(2024, 6, 30))
        assert [d.decision for d in loaded] == ["BUY", "FILL"]

    def test_a_whole_rerun_collapses(self, tmp_path: Path) -> None:
        log = DecisionLogger(root=tmp_path)
        book = ["SEB", "UVV", "SFD", "INGR", "HOG", "CRI"]
        for _run in range(2):
            for t in book:
                log.log(_decision(ticker=t))

        loaded = log.read_day("greenblatt", date(2024, 6, 30))
        assert [d.ticker for d in loaded] == book

    def test_rows_already_duplicated_on_disk_are_left_alone(
        self, tmp_path: Path
    ) -> None:
        # The logger deduplicates what it writes; it does not rewrite
        # history it did not write, which would silently edit the
        # audit trail.
        path = tmp_path / "greenblatt"
        path.mkdir(parents=True)
        row = _decision(ticker="INGR").to_dict()
        (path / "2024-06-30.json").write_text(json.dumps([row, row]))

        log = DecisionLogger(root=tmp_path)
        log.log(_decision(ticker="HOG"))

        loaded = log.read_day("greenblatt", date(2024, 6, 30))
        assert [d.ticker for d in loaded] == ["INGR", "INGR", "HOG"]
