"""The record E7 counts, and the counting.

§6's E7 sells a core holding after eight quarterly reviews with no
progress toward the thesis. The rule was implemented and unreachable:
``PositionState.quarterly_reviews_without_progress`` defaulted to
``None`` and nothing in the live path ever supplied it, because the
REVIEW run produced no record to count.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agents.council.review import (
    TIME_STOP_QUARTERS,
    ReviewRecord,
    Verdict,
    quarters_without_progress,
    record_review,
    reviews_for,
)

QUARTERS = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31)]


def _review(period_end: date, verdict: Verdict, ticker: str = "AAA") -> ReviewRecord:
    return ReviewRecord(
        ticker=ticker,
        reviewed_on=period_end,
        period_end=period_end,
        verdict=verdict,
        note=f"{verdict} on {period_end}",
    )


class TestRecording:
    def test_a_review_round_trips(self, tmp_path: Path) -> None:
        original = _review(QUARTERS[0], Verdict.PROGRESS)
        record_review(original, directory=tmp_path)
        assert reviews_for("AAA", directory=tmp_path) == [original]

    def test_reviews_come_back_oldest_period_first(self, tmp_path: Path) -> None:
        for q in reversed(QUARTERS):
            record_review(_review(q, Verdict.NO_PROGRESS), directory=tmp_path)
        got = reviews_for("AAA", directory=tmp_path)
        assert [r.period_end for r in got] == QUARTERS

    def test_re_reviewing_a_period_corrects_it(self, tmp_path: Path) -> None:
        """A retry must not count as a second quarter.

        E7 sells on eight. A manual dispatch after a scheduled run would
        otherwise bring the stop forward by a quarter for nothing.
        """
        record_review(_review(QUARTERS[0], Verdict.NO_PROGRESS), directory=tmp_path)
        record_review(_review(QUARTERS[0], Verdict.PROGRESS), directory=tmp_path)

        got = reviews_for("AAA", directory=tmp_path)
        assert len(got) == 1
        assert got[0].verdict is Verdict.PROGRESS

    def test_tickers_do_not_mix(self, tmp_path: Path) -> None:
        record_review(_review(QUARTERS[0], Verdict.NO_PROGRESS, "AAA"), directory=tmp_path)
        record_review(_review(QUARTERS[0], Verdict.NO_PROGRESS, "BBB"), directory=tmp_path)
        assert len(reviews_for("AAA", directory=tmp_path)) == 1

    def test_a_ticker_never_reviewed_is_empty(self, tmp_path: Path) -> None:
        assert reviews_for("NONE", directory=tmp_path) == []

    def test_an_unreadable_record_is_skipped(self, tmp_path: Path) -> None:
        """And skipping shortens the count, which delays an exit.

        Failing in the direction of holding is the right way round for a
        rule that sells.
        """
        record_review(_review(QUARTERS[0], Verdict.NO_PROGRESS), directory=tmp_path)
        (tmp_path / "AAA" / "2024-06-30.json").write_text("{ not json")
        assert len(reviews_for("AAA", directory=tmp_path)) == 1

    def test_the_note_survives(self, tmp_path: Path) -> None:
        """A count with no reasoning behind it cannot be argued with."""
        record_review(
            ReviewRecord(
                ticker="AAA",
                reviewed_on=QUARTERS[0],
                period_end=QUARTERS[0],
                verdict=Verdict.NO_PROGRESS,
                note="margin flat for a fourth quarter; thesis said 400bps by now",
            ),
            directory=tmp_path,
        )
        got = reviews_for("AAA", directory=tmp_path)[0]
        assert "400bps" in got.note

    def test_the_file_is_readable_json(self, tmp_path: Path) -> None:
        record_review(_review(QUARTERS[0], Verdict.KILL), directory=tmp_path)
        data = json.loads((tmp_path / "AAA" / "2024-03-31.json").read_text())
        assert data["verdict"] == "kill"


class TestCounting:
    def test_no_reviews_is_zero(self, tmp_path: Path) -> None:
        assert quarters_without_progress("AAA", directory=tmp_path) == 0

    def test_consecutive_no_progress_counts(self, tmp_path: Path) -> None:
        for q in QUARTERS:
            record_review(_review(q, Verdict.NO_PROGRESS), directory=tmp_path)
        assert quarters_without_progress("AAA", directory=tmp_path) == 4

    def test_progress_resets_the_count(self, tmp_path: Path) -> None:
        """A quarter of real progress buys another eight.

        Counting lifetime no-progress quarters instead would mean a name
        that stumbled early could never recover its standing however
        well it later did.
        """
        record_review(_review(QUARTERS[0], Verdict.NO_PROGRESS), directory=tmp_path)
        record_review(_review(QUARTERS[1], Verdict.NO_PROGRESS), directory=tmp_path)
        record_review(_review(QUARTERS[2], Verdict.PROGRESS), directory=tmp_path)
        record_review(_review(QUARTERS[3], Verdict.NO_PROGRESS), directory=tmp_path)

        assert quarters_without_progress("AAA", directory=tmp_path) == 1

    def test_a_kill_stops_the_count(self, tmp_path: Path) -> None:
        """A dead thesis exits on its own rule, not on a time stop."""
        record_review(_review(QUARTERS[0], Verdict.NO_PROGRESS), directory=tmp_path)
        record_review(_review(QUARTERS[1], Verdict.KILL), directory=tmp_path)
        assert quarters_without_progress("AAA", directory=tmp_path) == 0

    def test_the_count_is_by_period_not_by_review_date(
        self, tmp_path: Path
    ) -> None:
        """Reviews arriving out of order still count in period order."""
        record_review(
            ReviewRecord(
                ticker="AAA",
                reviewed_on=date(2025, 1, 5),
                period_end=QUARTERS[0],
                verdict=Verdict.PROGRESS,
            ),
            directory=tmp_path,
        )
        record_review(
            ReviewRecord(
                ticker="AAA",
                reviewed_on=date(2024, 4, 5),
                period_end=QUARTERS[1],
                verdict=Verdict.NO_PROGRESS,
            ),
            directory=tmp_path,
        )
        assert quarters_without_progress("AAA", directory=tmp_path) == 1

    def test_eight_is_what_the_doctrine_says(self) -> None:
        assert TIME_STOP_QUARTERS == 8

    def test_the_stop_is_reachable(self, tmp_path: Path) -> None:
        """The point of the whole module: E7 can now actually fire."""
        for i in range(TIME_STOP_QUARTERS):
            record_review(
                ReviewRecord(
                    ticker="AAA",
                    reviewed_on=date(2024, 1, 1),
                    period_end=date(2024 + i // 4, 3 * (i % 4) + 3, 28),
                    verdict=Verdict.NO_PROGRESS,
                ),
                directory=tmp_path,
            )
        assert (
            quarters_without_progress("AAA", directory=tmp_path) >= TIME_STOP_QUARTERS
        )


class TestVerdictValue:
    @pytest.mark.parametrize("bad", ["ok", "sell", ""])
    def test_an_unknown_verdict_is_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            Verdict(bad)

    def test_the_three_verdicts(self) -> None:
        assert {v.value for v in Verdict} == {"progress", "no_progress", "kill"}
