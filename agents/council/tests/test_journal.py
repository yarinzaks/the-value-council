"""The journal, the punch card, and the two rules enforced by construction."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agents.council.journal import (
    PUNCH_CARD_TOTAL,
    Calibration,
    Classification,
    Journal,
    JournalError,
    KillCriterion,
    Outcome,
    Thesis,
    calibrate,
    shrink,
    summary,
)

OPENED = date(2026, 8, 14)


def _kills(n: int = 3) -> tuple[KillCriterion, ...]:
    return tuple(
        KillCriterion(
            condition=f"metric {i} breaches its floor",
            measured_in="10-Q",
            action="exit",
        )
        for i in range(n)
    )


def _thesis(ticker: str = "X", **kw) -> Thesis:
    base = {
        "ticker": ticker,
        "opened": OPENED,
        "classification": Classification.UNDERSTANDING,
        "claim": "the legacy decline is contractually fixed",
        "structural_floor": "net cash is 41% of the market cap",
        "second_order_belief": "the market treats a fixed decline as terminal",
        "probability": 0.65,
        "size": 0.18,
        "kill_criteria": _kills(),
    }
    base.update(kw)
    return Thesis(**base)  # type: ignore[arg-type]


class TestThesisConstruction:
    def test_fewer_than_three_kill_criteria_cannot_be_recorded(self) -> None:
        """A thesis with no kill criteria is an opinion with formatting.
        Recording one must be impossible, not discouraged."""
        with pytest.raises(JournalError, match="kill criteria"):
            _thesis(kill_criteria=_kills(2))

    def test_no_structural_floor_cannot_be_recorded(self) -> None:
        with pytest.raises(JournalError, match="structural floor"):
            _thesis(structural_floor="   ")

    def test_probability_outside_zero_to_one_is_refused(self) -> None:
        with pytest.raises(JournalError, match="probability"):
            _thesis(probability=1.4)

    def test_a_kill_criterion_needs_an_action(self) -> None:
        with pytest.raises(JournalError, match="no action"):
            KillCriterion(condition="margin collapses", measured_in="10-Q", action="")

    def test_resolved_without_a_date_is_refused(self) -> None:
        with pytest.raises(JournalError, match="no date"):
            _thesis(outcome=Outcome.RIGHT)

    def test_round_trips_through_json_shape(self) -> None:
        t = _thesis()
        assert Thesis.from_dict(t.to_dict()) == t


class TestJournal:
    def test_records_and_reads_back(self, tmp_path: Path) -> None:
        j = Journal(directory=tmp_path)
        j.record(_thesis("AAA"))
        entries = j.entries()
        assert [t.ticker for t in entries] == ["AAA"]

    def test_append_only_refuses_to_overwrite(self, tmp_path: Path) -> None:
        """An entry editable after the outcome is known cannot be used to
        judge the decision."""
        j = Journal(directory=tmp_path)
        j.record(_thesis("AAA"))
        with pytest.raises(JournalError, match="append-only"):
            j.record(_thesis("AAA"))

    def test_resolving_is_the_only_permitted_edit(self, tmp_path: Path) -> None:
        j = Journal(directory=tmp_path)
        j.record(_thesis("AAA"))
        resolved = j.resolve(
            "AAA", OPENED, outcome=Outcome.RIGHT, when=date(2027, 1, 1), note="paid"
        )
        assert resolved.outcome is Outcome.RIGHT
        assert j.open_entries() == []

    def test_resolving_twice_is_refused(self, tmp_path: Path) -> None:
        j = Journal(directory=tmp_path)
        j.record(_thesis("AAA"))
        j.resolve("AAA", OPENED, outcome=Outcome.RIGHT, when=date(2027, 1, 1))
        with pytest.raises(JournalError, match="already resolved"):
            j.resolve("AAA", OPENED, outcome=Outcome.WRONG, when=date(2027, 2, 1))


class TestPunchCard:
    def test_it_is_derived_from_the_entries(self, tmp_path: Path) -> None:
        """A stored counter could disagree with the journal, and then one
        of them is wrong with no way to tell which."""
        j = Journal(directory=tmp_path)
        assert j.punches_remaining() == PUNCH_CARD_TOTAL
        j.record(_thesis("AAA"))
        assert j.punches_used() == 1
        assert j.punches_remaining() == PUNCH_CARD_TOTAL - 1

    def test_statistical_positions_do_not_spend_a_punch(self, tmp_path: Path) -> None:
        j = Journal(directory=tmp_path)
        j.record(_thesis("AAA", classification=Classification.STATISTICAL))
        assert j.punches_used() == 0

    def test_there_is_no_twenty_first(self, tmp_path: Path) -> None:
        j = Journal(directory=tmp_path)
        for i in range(PUNCH_CARD_TOTAL):
            j.record(_thesis(f"T{i:02d}"))
        with pytest.raises(JournalError, match="punch card is spent"):
            j.record(_thesis("ONEMORE"))

    def test_a_spent_card_still_allows_statistical_positions(
        self, tmp_path: Path
    ) -> None:
        j = Journal(directory=tmp_path)
        for i in range(PUNCH_CARD_TOTAL):
            j.record(_thesis(f"T{i:02d}"))
        j.record(_thesis("STAT", classification=Classification.STATISTICAL))
        assert len(j.entries()) == PUNCH_CARD_TOTAL + 1


class TestCalibration:
    def test_no_resolved_entries_means_no_shrinkage(self) -> None:
        cal = calibrate([_thesis("A")])
        assert cal.resolved == 0
        assert cal.brier is None
        assert cal.shrinkage == 1.0

    def test_perfect_calibration_leaves_probabilities_alone(self) -> None:
        entries = [
            _thesis(f"T{i}", probability=1.0, outcome=Outcome.RIGHT,
                    resolved=date(2027, 1, 1))
            for i in range(4)
        ]
        cal = calibrate(entries)
        assert cal.brier == 0.0
        assert cal.shrinkage == 1.0
        assert shrink(0.80, cal) == pytest.approx(0.80)

    def test_confident_and_wrong_shrinks_toward_a_coin_flip(self) -> None:
        entries = [
            _thesis(f"T{i}", probability=0.9, outcome=Outcome.WRONG,
                    resolved=date(2027, 1, 1))
            for i in range(4)
        ]
        cal = calibrate(entries)
        assert cal.shrinkage == 0.0
        assert shrink(0.90, cal) == pytest.approx(0.50)

    def test_shrinkage_never_inflates(self) -> None:
        """No amount of past accuracy licenses raising a stated
        probability above what was claimed."""
        entries = [
            _thesis(f"T{i}", probability=0.51, outcome=Outcome.RIGHT,
                    resolved=date(2027, 1, 1))
            for i in range(6)
        ]
        cal = calibrate(entries)
        assert cal.shrinkage <= 1.0
        assert shrink(0.70, cal) <= 0.70

    def test_summary_shape_for_the_dashboard(self, tmp_path: Path) -> None:
        j = Journal(directory=tmp_path)
        j.record(_thesis("AAA"))
        s = summary(j)
        assert s["punch_card"] == {
            "total": PUNCH_CARD_TOTAL,
            "used": 1,
            "remaining": PUNCH_CARD_TOTAL - 1,
        }
        assert s["open"] == 1
        assert isinstance(s["calibration"], dict)


class TestCalibrationDataclass:
    def test_serialises(self) -> None:
        cal = Calibration(resolved=0, brier=None, buckets=(), shrinkage=1.0)
        assert cal.to_dict()["shrinkage"] == 1.0
