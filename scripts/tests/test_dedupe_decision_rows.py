"""The repair must drop repetitions and nothing else."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.dedupe_decision_rows import _is_collision_copy, dedupe


def _row(ticker: str = "INGR", decision: str = "BUY", **extra) -> dict:
    return {
        "ticker": ticker,
        "decision": decision,
        "agent": "benjamin_graham",
        "timestamp": "2026-11-02T00:00:00+00:00",
        "criteria_values": {"pe": 8.74},
        **extra,
    }


class TestDedupe:
    def test_an_identical_repeat_goes(self) -> None:
        kept, dropped = dedupe([_row(), _row()])
        assert dropped == 1
        assert kept == [_row()]

    def test_a_whole_rerun_collapses(self) -> None:
        book = ["SEB", "UVV", "SFD", "INGR", "HOG", "CRI"]
        rows = [_row(t) for t in book] * 2
        kept, dropped = dedupe(rows)
        assert dropped == 6
        assert [r["ticker"] for r in kept] == book

    def test_order_is_preserved(self) -> None:
        rows = [_row("A"), _row("B"), _row("A"), _row("C")]
        kept, _ = dedupe(rows)
        assert [r["ticker"] for r in kept] == ["A", "B", "C"]

    def test_a_changed_value_is_not_a_duplicate(self) -> None:
        kept, dropped = dedupe(
            [
                _row(criteria_values={"pe": 8.74}),
                _row(criteria_values={"pe": 8.80}),
            ]
        )
        assert dropped == 0
        assert len(kept) == 2

    def test_an_execution_is_not_its_intent(self) -> None:
        kept, dropped = dedupe([_row(decision="BUY"), _row(decision="FILL")])
        assert dropped == 0
        assert [r["decision"] for r in kept] == ["BUY", "FILL"]

    def test_key_order_does_not_make_two_rows_distinct(self) -> None:
        a = {"ticker": "INGR", "decision": "BUY"}
        b = {"decision": "BUY", "ticker": "INGR"}
        kept, dropped = dedupe([a, b])
        assert dropped == 1
        assert len(kept) == 1

    def test_nothing_to_do_is_a_no_op(self) -> None:
        rows = [_row("A"), _row("B")]
        kept, dropped = dedupe(rows)
        assert dropped == 0
        assert kept == rows


class TestCollisionCopies:
    def test_a_sync_copy_is_recognised(self) -> None:
        assert _is_collision_copy(Path("data/x/2026-10-30 2.json"))

    def test_an_ordinary_day_is_not(self) -> None:
        assert not _is_collision_copy(Path("data/x/2026-10-30.json"))

    def test_a_name_ending_in_a_digit_is_not(self) -> None:
        assert not _is_collision_copy(Path("data/x/summary2.json"))


class TestEndToEnd:
    def test_the_file_is_rewritten_as_strict_json(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        agent = tmp_path / "benjamin_graham"
        agent.mkdir()
        day = agent / "2026-11-02.json"
        day.write_text(json.dumps([_row("SEB"), _row("SEB"), _row("UVV")]))
        # The collision copy must be left exactly as it is.
        copy = agent / "2026-11-02 2.json"
        copy.write_text(json.dumps([_row("SEB"), _row("SEB")]))

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.dedupe_decision_rows",
                "--root",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        assert "1 duplicate rows dropped" in result.stdout
        assert [r["ticker"] for r in json.loads(day.read_text())] == [
            "SEB",
            "UVV",
        ]
        assert len(json.loads(copy.read_text())) == 2

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        agent = tmp_path / "benjamin_graham"
        agent.mkdir()
        day = agent / "2026-11-02.json"
        before = json.dumps([_row("SEB"), _row("SEB")])
        day.write_text(before)

        subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.dedupe_decision_rows",
                "--dry-run",
                "--root",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        assert day.read_text() == before


class TestNonFiniteRepair:
    def test_a_nan_becomes_null(self) -> None:
        from scripts.dedupe_decision_rows import count_non_finite, strict

        row = {"pe": float("nan"), "pb": 1.44, "deep": {"x": float("inf")}}
        assert count_non_finite(row) == 2
        assert strict(row) == {"pe": None, "pb": 1.44, "deep": {"x": None}}

    def test_a_nan_only_file_is_repaired(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        agent = tmp_path / "benjamin_graham"
        agent.mkdir()
        day = agent / "2026-06-19.json"
        # No duplicates here — only the unparseable token.
        day.write_text('[{"ticker": "INGR", "criteria_values": {"p_ncav": NaN}}]')

        result = subprocess.run(
            [sys.executable, "-m", "scripts.dedupe_decision_rows",
             "--root", str(tmp_path)],
            capture_output=True, text=True, check=True,
        )

        assert "1 non-finite values nulled" in result.stdout
        text = day.read_text()
        assert "NaN" not in text
        assert json.loads(text)[0]["criteria_values"] == {"p_ncav": None}
