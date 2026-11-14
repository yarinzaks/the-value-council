"""Which per-day files a reader takes — the Python twin of day-files.ts.

macOS file sync resolves a write collision by keeping both sides and
renaming one "<day> 2.json". The dashboard already reads one file per
day; these tests pin the same rule for the Python readers (audit RK-03).
"""

from __future__ import annotations

from pathlib import Path

from core.day_files import canonical_day_files


def _names(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


class TestOneFilePerDay:
    def test_the_original_wins_over_its_collision_copy(self) -> None:
        got = canonical_day_files(
            [Path("d/2026-10-30 2.json"), Path("d/2026-10-30.json")]
        )
        assert _names(got) == ["2026-10-30.json"]

    def test_days_come_back_sorted(self) -> None:
        got = canonical_day_files(
            [Path("d/2026-11-02.json"), Path("d/2026-10-30.json")]
        )
        assert _names(got) == ["2026-10-30.json", "2026-11-02.json"]

    def test_non_json_files_are_not_read(self) -> None:
        # The decision logger's atomic write leaves "<day>.tmp" briefly.
        got = canonical_day_files([Path("d/2026-10-30.tmp"), Path("d/2026-10-30.json")])
        assert _names(got) == ["2026-10-30.json"]

    def test_a_day_with_only_copies_keeps_the_lowest_numbered(self) -> None:
        # Dropping the day would lose real rows — the worse failure.
        got = canonical_day_files(
            [Path("d/2026-10-30 3.json"), Path("d/2026-10-30 2.json")]
        )
        assert _names(got) == ["2026-10-30 2.json"]

    def test_an_empty_listing_is_empty(self) -> None:
        assert canonical_day_files([]) == []

    def test_a_name_with_a_number_but_no_space_is_its_own_day(self) -> None:
        # Mirrors the dashboard: only " <n>" before ".json" marks a copy.
        got = canonical_day_files([Path("d/summary2.json"), Path("d/summary.json")])
        assert _names(got) == ["summary.json", "summary2.json"]
