"""What pruning stale dividend and split records must and must not do."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.live.portfolio import LivePortfolio, Position
from scripts import prune_event_history as mod


def _book_text(
    paid: dict[str, list[str]], *, splits: dict[str, list[str]] | None = None
) -> str:
    book = LivePortfolio(agent="t", cash=1_234.5678)
    book.positions = [
        Position(
            ticker="KEEP", shares=5.0, entry_price=10.0,
            entry_date="2026-11-04", current_price=12.3456,
        )
    ]
    book.mark_to_market({})
    book.cumulative_dividends = 7.25
    book.paid_dividends = paid
    book.applied_splits = splits or {}
    return json.dumps(book.to_dict(), indent=2, ensure_ascii=False)


class TestPruneText:
    def test_only_the_two_maps_change(self) -> None:
        text = _book_text(
            {"KEEP": ["2026-10-28", "2026-11-10"], "SEB": ["2026-08-14"]},
            splits={"GONE": ["2026-07-01"]},
        )

        new_text, removed = mod.prune_text(text)

        old: dict[str, Any] = json.loads(text)
        new: dict[str, Any] = json.loads(new_text)
        assert new["paid_dividends"] == {"KEEP": ["2026-11-10"]}
        assert new["applied_splits"] == {}
        for key in old.keys() - {"paid_dividends", "applied_splits"}:
            assert new[key] == old[key], key
        assert list(new) == list(old)  # key order, so the diff is the maps
        assert removed["paid_dividends"] == {
            "KEEP": ["2026-10-28"], "SEB": ["2026-08-14"],
        }

    def test_a_clean_book_is_returned_byte_for_byte(self) -> None:
        text = _book_text({"KEEP": ["2026-11-10"]})

        new_text, removed = mod.prune_text(text)

        assert new_text == text
        assert removed == {"paid_dividends": {}, "applied_splits": {}}

    def test_a_prune_that_would_move_money_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The method cannot move cash today; if a change ever made it,
        the script stops instead of writing the book."""
        def leaky(self: LivePortfolio) -> dict[str, dict[str, list[str]]]:
            self.paid_dividends.clear()
            self.cash += 1.0
            return {"paid_dividends": {"SEB": ["2026-08-14"]}, "applied_splits": {}}

        monkeypatch.setattr(LivePortfolio, "prune_event_history", leaky)

        with pytest.raises(mod.PruneRefusedError):
            mod.prune_text(_book_text({"SEB": ["2026-08-14"]}))


class TestMain:
    def _dir(self, tmp_path: Path, paid: dict[str, list[str]]) -> Path:
        d = tmp_path / "portfolios"
        d.mkdir()
        (d / "a.json").write_text(_book_text(paid))
        (d / "b.json").write_text(_book_text({"KEEP": ["2026-11-10"]}))
        return d

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        d = self._dir(tmp_path, {"SEB": ["2026-08-14"]})
        before = {p.name: p.read_text() for p in d.iterdir()}

        assert mod.main(["--dir", str(d)]) == 0

        assert {p.name: p.read_text() for p in d.iterdir()} == before

    def test_apply_writes_only_the_book_that_changes(self, tmp_path: Path) -> None:
        d = self._dir(tmp_path, {"SEB": ["2026-08-14"]})
        untouched = (d / "b.json").read_text()

        assert mod.main(["--dir", str(d), "--apply"]) == 0

        assert json.loads((d / "a.json").read_text())["paid_dividends"] == {}
        assert (d / "b.json").read_text() == untouched
        assert sorted(p.name for p in d.iterdir()) == ["a.json", "b.json"]

    def test_an_empty_directory_is_an_error(self, tmp_path: Path) -> None:
        assert mod.main(["--dir", str(tmp_path)]) == 2

    def test_a_refusal_writes_no_book(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        d = self._dir(tmp_path, {"SEB": ["2026-08-14"]})
        before = {p.name: p.read_text() for p in d.iterdir()}

        def refuse(text: str) -> tuple[str, dict[str, dict[str, list[str]]]]:
            raise mod.PruneRefusedError("moved money")

        monkeypatch.setattr(mod, "prune_text", refuse)

        assert mod.main(["--dir", str(d), "--apply"]) == 1
        assert {p.name: p.read_text() for p in d.iterdir()} == before
