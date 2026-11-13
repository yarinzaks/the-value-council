"""What tagging the closed-session files must and must not do (audit RK-06)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.live.snapshots import load_snapshots
from core.live.trade_ledger import read_ledger
from scripts import tag_closed_sessions as mod


def _snapshot(day: str) -> dict[str, Any]:
    return {
        "agent": "a", "date": day, "nav": 1.0, "cash": 1.0, "invested": 0.0,
        "pnl_usd": 0.0, "pnl_pct": 0.0, "position_count": 0,
        "watchlist_count": 0, "buys": [], "sells": [], "trade_count": 0,
    }


def _row(day: str, tags: list[str] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "date": day, "agent": "a", "ticker": "X", "side": "SELL",
        "shares": 1.0, "price": 2.0, "gross_value": 2.0, "cost_paid": 0.0,
        "realized_pnl_usd": 0.0, "source": "reconstructed",
    }
    if tags is not None:
        row["tags"] = tags
    return row


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    snaps, trades = tmp_path / "snapshots", tmp_path / "trades"
    (snaps / "a").mkdir(parents=True)
    (trades / "a").mkdir(parents=True)
    # Labor Day, a Sunday, and an ordinary Tuesday.
    for day in ("2026-09-07", "2026-08-16", "2026-09-08"):
        (snaps / "a" / f"{day}.json").write_text(
            json.dumps(_snapshot(day), indent=2)
        )
    (trades / "a" / "2026-09-07.json").write_text(
        json.dumps([_row("2026-09-07", ["exit-from-journal"])], indent=1)
    )
    (trades / "a" / "2026-09-08.json").write_text(
        json.dumps([_row("2026-09-08")], indent=1)
    )
    return snaps, trades


class TestTagging:
    def test_a_closed_day_is_tagged_in_both_stores(
        self, roots: tuple[Path, Path]
    ) -> None:
        snaps, trades = roots

        report = mod.tag(snaps, trades, apply=True)

        assert report.errors == []
        assert sorted(report.snapshots) == [
            "a/2026-08-16.json", "a/2026-09-07.json",
        ]
        assert report.ledger_rows == 1
        loaded = {s.date: s.closed_session for s in load_snapshots("a", root=snaps)}
        assert loaded == {
            "2026-08-16": True, "2026-09-07": True, "2026-09-08": False,
        }
        rows = {r.date: r.tags for r in read_ledger("a", directory=trades)}
        assert rows["2026-09-07"] == ("exit-from-journal", "closed-session")
        assert rows["2026-09-08"] == ()

    def test_an_open_day_is_left_byte_for_byte(
        self, roots: tuple[Path, Path]
    ) -> None:
        snaps, trades = roots
        before = {
            p: p.read_bytes()
            for p in (snaps / "a" / "2026-09-08.json", trades / "a" / "2026-09-08.json")
        }

        mod.tag(snaps, trades, apply=True)

        assert {p: p.read_bytes() for p in before} == before

    def test_without_apply_nothing_is_written(
        self, roots: tuple[Path, Path]
    ) -> None:
        snaps, trades = roots
        before = {p: p.read_bytes() for p in sorted(snaps.rglob("*.json"))}
        before |= {p: p.read_bytes() for p in sorted(trades.rglob("*.json"))}

        report = mod.tag(snaps, trades, apply=False)

        assert len(report.snapshots) == 2  # it still says what it would do
        assert {p: p.read_bytes() for p in before} == before

    def test_a_second_run_changes_nothing(
        self, roots: tuple[Path, Path]
    ) -> None:
        snaps, trades = roots
        mod.tag(snaps, trades, apply=True)
        after_first = {p: p.read_bytes() for p in sorted(snaps.rglob("*.json"))}

        again = mod.tag(snaps, trades, apply=True)

        assert again.snapshots == [] and again.ledger_rows == 0
        assert {p: p.read_bytes() for p in sorted(snaps.rglob("*.json"))} == after_first

    def test_an_unreadable_file_is_reported_and_not_rewritten(
        self, roots: tuple[Path, Path]
    ) -> None:
        snaps, trades = roots
        broken = trades / "a" / "2026-07-03.json"
        broken.write_text("[{not json")

        report = mod.tag(snaps, trades, apply=True)

        assert [e.split(":")[0] for e in report.errors] == ["a/2026-07-03.json"]
        assert broken.read_text() == "[{not json"
        assert mod.main(
            ["--snapshots", str(snaps), "--trades", str(trades)]
        ) == 1

    def test_a_name_that_is_not_a_day_is_ignored(
        self, roots: tuple[Path, Path]
    ) -> None:
        # A sync-collision copy is gitignored and not a day of its own;
        # anything else that is not "<YYYY-MM-DD>.json" is not ours.
        snaps, trades = roots
        copy = snaps / "a" / "2026-09-07 2.json"
        copy.write_text(json.dumps(_snapshot("2026-09-07")))

        report = mod.tag(snaps, trades, apply=True)

        assert "a/2026-09-07 2.json" not in report.snapshots
        assert "closed_session" not in json.loads(copy.read_text())

