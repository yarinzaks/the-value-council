"""The reconstruction, and what it is and is not allowed to overwrite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.live.trade_ledger import LedgerEntry
from scripts import backfill_trade_ledger as bf


def _book(day: str, positions: list[dict]) -> dict:
    return {"last_open_date": day, "positions": positions}


def _pos(ticker: str, shares: float, entry_price: float, entry_date: str,
         mark: float) -> dict:
    return {"ticker": ticker, "shares": shares, "entry_price": entry_price,
            "entry_date": entry_date, "current_price": mark}


class TestReconstruct:
    def test_a_closed_line_carries_the_entry_the_book_knew(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        books = {
            "a": _book("2026-05-06", [_pos("CRI", 16, 33.34, "2026-05-06", 33.34)]),
            "b": _book("2026-05-12", []),
        }
        monkeypatch.setattr(bf, "_history", lambda agent: ["a", "b"])
        monkeypatch.setattr(bf, "_book_at", lambda sha, agent: books[sha])
        by_day = bf.reconstruct("g")
        (row,) = by_day["2026-05-12"]
        assert row.side == "SELL" and row.shares == 16
        assert row.source == "reconstructed"
        assert row.entry_date == "2026-05-06"
        assert row.entry_price == 33.34

    def test_a_trim_keeps_the_entry_of_what_remains(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        books = {
            "a": _book("2026-05-06", [_pos("HOG", 74, 25.9457, "2026-05-06", 30.0)]),
            "b": _book("2026-05-20", [_pos("HOG", 50, 25.9457, "2026-05-06", 32.0)]),
        }
        monkeypatch.setattr(bf, "_history", lambda agent: ["a", "b"])
        monkeypatch.setattr(bf, "_book_at", lambda sha, agent: books[sha])
        (row,) = bf.reconstruct("g")["2026-05-20"]
        assert row.shares == 24
        assert row.price == 32.0  # the mark the book carried, not a fill
        assert row.entry_date == "2026-05-06"

    def test_a_book_with_no_entry_date_leaves_it_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        books = {
            "a": _book("2026-05-06", [{"ticker": "X", "shares": 1, "entry_price": 1.0,
                                       "current_price": 1.0}]),
            "b": _book("2026-05-07", []),
        }
        monkeypatch.setattr(bf, "_history", lambda agent: ["a", "b"])
        monkeypatch.setattr(bf, "_book_at", lambda sha, agent: books[sha])
        (row,) = bf.reconstruct("g")["2026-05-07"]
        assert row.entry_date is None


class TestWriteDays:
    def _entry(self, day: str, source: str) -> LedgerEntry:
        return LedgerEntry(date=day, agent="g", ticker="X", side="SELL", shares=1,
                           price=1, gross_value=1, cost_paid=0, realized_pnl_usd=0,
                           source=source)

    def test_a_recorded_day_is_never_touched(self, tmp_path: Path) -> None:
        day = tmp_path / "g" / "2026-09-03.json"
        day.parent.mkdir()
        day.write_text(json.dumps([self._entry("2026-09-03", "executed").to_dict()]))
        before = day.read_text()
        written, kept = bf.write_days(
            tmp_path, "g", {"2026-09-03": [self._entry("2026-09-03", "reconstructed")]},
            refresh_reconstructed=True,
        )
        assert (written, kept) == (0, 1)
        assert day.read_text() == before

    def test_a_reconstructed_day_is_kept_unless_asked(self, tmp_path: Path) -> None:
        day = tmp_path / "g" / "2026-05-12.json"
        day.parent.mkdir()
        day.write_text(json.dumps([self._entry("2026-05-12", "reconstructed").to_dict()]))
        fresh = LedgerEntry(date="2026-05-12", agent="g", ticker="X", side="SELL",
                            shares=1, price=1, gross_value=1, cost_paid=0,
                            realized_pnl_usd=0, source="reconstructed",
                            entry_date="2026-05-06", entry_price=1.0)
        assert bf.write_days(tmp_path, "g", {"2026-05-12": [fresh]}) == (0, 1)
        assert "entry_date" not in day.read_text()
        assert bf.write_days(tmp_path, "g", {"2026-05-12": [fresh]},
                             refresh_reconstructed=True) == (1, 0)
        assert json.loads(day.read_text())[0]["entry_date"] == "2026-05-06"

    def test_a_day_mixing_sources_is_kept(self, tmp_path: Path) -> None:
        day = tmp_path / "g" / "2026-10-30.json"
        day.parent.mkdir()
        day.write_text(json.dumps([
            self._entry("2026-10-30", "reconstructed").to_dict(),
            self._entry("2026-10-30", "caught-up").to_dict(),
        ]))
        assert bf.write_days(tmp_path, "g", {"2026-10-30": []},
                             refresh_reconstructed=True) == (0, 1)
