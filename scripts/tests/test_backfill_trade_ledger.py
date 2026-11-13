"""The reconstruction, and what it is and is not allowed to overwrite."""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.live.trade_ledger import LedgerEntry
from scripts import backfill_trade_ledger as bf


def _book(day: str, positions: list[dict]) -> dict:
    return {"last_open_date": day, "positions": positions}


def _pos(ticker: str, shares: float, entry_price: float, entry_date: str,
         mark: float) -> dict:
    return {"ticker": ticker, "shares": shares, "entry_price": entry_price,
            "entry_date": entry_date, "current_price": mark}


@pytest.fixture(autouse=True)
def _empty_repo_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run every test from an empty directory.

    The journal and price lookups resolve ``data/...`` against the
    working directory; from the repo root they would read the real
    committed data and the tests would depend on it.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _history_of(books: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    return [(sha, "data/portfolios/g.json") for sha in books]


class TestReconstruct:
    def test_a_closed_line_carries_the_entry_the_book_knew(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        books = {
            "a": _book("2026-05-06", [_pos("CRI", 16, 33.34, "2026-05-06", 33.34)]),
            "b": _book("2026-05-12", []),
        }
        monkeypatch.setattr(bf, "_history", lambda agent: _history_of(books))
        monkeypatch.setattr(bf, "_book_at", lambda sha, path: books[sha])
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
        monkeypatch.setattr(bf, "_history", lambda agent: _history_of(books))
        monkeypatch.setattr(bf, "_book_at", lambda sha, path: books[sha])
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
        monkeypatch.setattr(bf, "_history", lambda agent: _history_of(books))
        monkeypatch.setattr(bf, "_book_at", lambda sha, path: books[sha])
        (row,) = bf.reconstruct("g")["2026-05-07"]
        assert row.entry_date is None


_SETTLEMENT = "f897dc47460e7baac1b2dd34a143c4396ae33112"


def _journal(root: Path, agent: str, day: str, rows: list[dict[str, Any]]) -> None:
    folder = root / "data" / "decisions" / agent
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.json").write_text(json.dumps(rows))


def _exit_row(ticker: str, entry: float, exit_: float,
              decision: str = "EXIT") -> dict[str, Any]:
    return {"ticker": ticker, "decision": decision,
            "rationale": f"Closed live position: entry ${entry:.2f}, "
                         f"exit ${exit_:.2f} (+0.00%)"}


def _prices(root: Path, ticker: str, points: list[tuple[str, float]]) -> None:
    folder = root / "data" / "prices"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{ticker}.json").write_text(json.dumps(
        {"ticker": ticker, "points": [{"d": d, "c": c} for d, c in points]}))


def _run(monkeypatch: pytest.MonkeyPatch, books: dict[str, dict[str, Any]],
         agent: str = "g") -> dict[str, list[LedgerEntry]]:
    monkeypatch.setattr(bf, "_history", lambda a: _history_of(books))
    monkeypatch.setattr(bf, "_book_at", lambda sha, path: books[sha])
    return bf.reconstruct(agent)


class TestExitPrice:
    """CE-1b: the exit is the price the sale got, when anything wrote it down."""

    def test_the_exit_price_is_read_from_a_row_that_names_its_shares(
        self
    ) -> None:
        # RK-05 appended the share count to the EXIT rationale; the
        # price this script reads from it must not move.
        row = ("Closed live position: entry $22.88, exit $22.70 (-0.79%), "
               "sold 60 shares")
        match = bf._EXIT.search(row)
        assert match is not None
        assert match.group(1) == "22.70"

    def test_reconstructed_exit_uses_journal_price_when_available(
        self, monkeypatch: pytest.MonkeyPatch, _empty_repo_cwd: Path
    ) -> None:
        # Buffett's CPB, 2026-11-09: the book's last mark was 23.13, the
        # journal says the sale got 22.70, and the cash agrees with 22.70.
        _journal(_empty_repo_cwd, "warren_buffett", "2026-11-09",
                 [{"ticker": "CPB", "decision": "WATCH", "rationale": "x"},
                  _exit_row("CPB", 22.88, 22.70)])
        books = {
            "a": _book("2026-11-06", [_pos("CPB", 60, 22.88, "2026-08-07", 23.13)]),
            "b": _book("2026-11-09", []),
        }
        (row,) = _run(monkeypatch, books, "warren_buffett")["2026-11-09"]
        assert row.price == pytest.approx(22.70)
        assert row.gross_value == pytest.approx(60 * 22.70)
        assert row.realized_pnl_usd == pytest.approx((22.70 - 22.88) * 60)
        assert "exit-from-journal" in row.tags

    def test_an_old_sell_row_is_read_too(
        self, monkeypatch: pytest.MonkeyPatch, _empty_repo_cwd: Path
    ) -> None:
        """Before EXIT existed the runner logged the same line as SELL."""
        _journal(_empty_repo_cwd, "g", "2026-06-12",
                 [_exit_row("MOS", 20.71, 22.79, decision="SELL")])
        books = {
            "a": _book("2026-06-11", [_pos("MOS", 10, 20.71, "2026-05-06", 22.0)]),
            "b": _book("2026-06-12", []),
        }
        (row,) = _run(monkeypatch, books)["2026-06-12"]
        assert row.price == pytest.approx(22.79)

    def test_without_a_journal_row_it_falls_back_to_the_mark_and_says_so(
        self, monkeypatch: pytest.MonkeyPatch, _empty_repo_cwd: Path
    ) -> None:
        _journal(_empty_repo_cwd, "g", "2026-06-12",
                 [_exit_row("OTHER", 1.0, 2.0)])
        books = {
            "a": _book("2026-06-11", [_pos("MOS", 10, 20.71, "2026-05-06", 22.0)]),
            "b": _book("2026-06-12", []),
        }
        (row,) = _run(monkeypatch, books)["2026-06-12"]
        assert row.price == pytest.approx(22.0)
        assert "exit-at-last-mark" in row.tags

    def test_two_different_journal_exits_are_not_guessed_between(
        self, monkeypatch: pytest.MonkeyPatch, _empty_repo_cwd: Path
    ) -> None:
        _journal(_empty_repo_cwd, "g", "2026-06-12",
                 [_exit_row("MOS", 20.71, 22.79), _exit_row("MOS", 20.71, 23.10)])
        books = {
            "a": _book("2026-06-11", [_pos("MOS", 10, 20.71, "2026-05-06", 22.0)]),
            "b": _book("2026-06-12", []),
        }
        (row,) = _run(monkeypatch, books)["2026-06-12"]
        assert row.price == pytest.approx(22.0)
        assert "exit-at-last-mark" in row.tags

    def test_an_unparseable_journal_day_falls_back_rather_than_crashing(
        self, monkeypatch: pytest.MonkeyPatch, _empty_repo_cwd: Path
    ) -> None:
        folder = _empty_repo_cwd / "data" / "decisions" / "g"
        folder.mkdir(parents=True)
        (folder / "2026-06-12.json").write_text("{not json")
        books = {
            "a": _book("2026-06-11", [_pos("MOS", 10, 20.71, "2026-05-06", 22.0)]),
            "b": _book("2026-06-12", []),
        }
        (row,) = _run(monkeypatch, books)["2026-06-12"]
        assert row.price == pytest.approx(22.0)


class TestCorporateActions:
    def test_thr_is_settled_at_the_merger_price_not_the_stale_mark(
        self, monkeypatch: pytest.MonkeyPatch, _empty_repo_cwd: Path
    ) -> None:
        _prices(_empty_repo_cwd, "THR", [("2026-05-28", 60.0), ("2026-05-29", 61.14)])
        books = {
            "a": _book("2026-08-07", [_pos("THR", 4, 69.29, "2026-05-27", 61.14)]),
            _SETTLEMENT: _book("2026-08-07", []),
        }
        (row,) = _run(monkeypatch, books, "peter_lynch")["2026-08-07"]
        assert row.price == pytest.approx(63.89)
        assert row.realized_pnl_usd == pytest.approx(-21.60)
        assert "cash-merger" in row.tags
        assert "frozen-price" not in row.tags

    def test_the_asgn_rename_realizes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lot became EFOR; the gain or loss is realized when EFOR is sold."""
        books = {
            "a": _book("2026-08-07", [_pos("ASGN", 26, 21.02, "2026-05-06", 19.08)]),
            _SETTLEMENT: _book("2026-08-07",
                               [_pos("EFOR", 26, 21.02, "2026-05-06", 19.08)]),
        }
        (row,) = _run(monkeypatch, books, "benjamin_graham")["2026-08-07"]
        assert row.ticker == "ASGN"
        assert row.realized_pnl_usd == 0.0
        assert "rename" in row.tags

    def test_the_same_tickers_in_an_ordinary_run_are_not_tagged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        books = {
            "a": _book("2026-06-24", [_pos("THR", 4, 69.29, "2026-05-27", 61.0)]),
            "b": _book("2026-06-25", []),
        }
        (row,) = _run(monkeypatch, books)["2026-06-25"]
        assert "cash-merger" not in row.tags and "rename" not in row.tags
        assert row.price == pytest.approx(61.0)


class TestFrozenPrice:
    def test_a_round_trip_at_a_dead_symbols_last_close_is_tagged(
        self, monkeypatch: pytest.MonkeyPatch, _empty_repo_cwd: Path
    ) -> None:
        # Marks' ASGN, 2026-06-23 -> 06-25, both at the 06-18 close.
        _prices(_empty_repo_cwd, "ASGN", [("2026-06-17", 20.06), ("2026-06-18", 19.08)])
        books = {
            "a": _book("2026-06-23", [_pos("ASGN", 23, 19.08, "2026-06-23", 19.08)]),
            "b": _book("2026-06-25", []),
        }
        (row,) = _run(monkeypatch, books, "howard_marks")["2026-06-25"]
        assert "frozen-price" in row.tags

    def test_a_fresh_exit_at_the_same_number_is_not(
        self, monkeypatch: pytest.MonkeyPatch, _empty_repo_cwd: Path
    ) -> None:
        """Within the carry-forward window the last close is a live quote."""
        _prices(_empty_repo_cwd, "ASGN", [("2026-06-17", 20.06), ("2026-06-18", 19.08)])
        books = {
            "a": _book("2026-06-17", [_pos("ASGN", 23, 20.0, "2026-06-01", 19.08)]),
            "b": _book("2026-06-22", []),
        }
        (row,) = _run(monkeypatch, books)["2026-06-22"]
        assert "frozen-price" not in row.tags

    def test_without_a_price_file_nothing_is_claimed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        books = {
            "a": _book("2026-06-23", [_pos("ZZZ", 1, 5.0, "2026-06-23", 5.0)]),
            "b": _book("2026-09-25", []),
        }
        (row,) = _run(monkeypatch, books)["2026-09-25"]
        assert "frozen-price" not in row.tags


    def test_the_staleness_bound_is_the_runners(self) -> None:
        # Imported by name so the script, and a type check of this file,
        # do not load the whole backtest package for one integer.
        loader = importlib.import_module("core.backtest.data_loader")
        assert loader.MAX_CARRY_FORWARD_DAYS == bf.MAX_CARRY_FORWARD_DAYS


class TestFractionalEra:
    @pytest.mark.parametrize(("day", "tagged"), [
        ("2026-08-06", False), ("2026-08-07", True),
        ("2026-10-30", True), ("2026-10-31", False),
    ])
    def test_the_era_is_bounded_by_the_two_commits(
        self, monkeypatch: pytest.MonkeyPatch, day: str, tagged: bool
    ) -> None:
        books = {
            "a": _book("2026-08-01", [_pos("X", 1.5, 10.0, "2026-08-01", 11.0)]),
            "b": _book(day, []),
        }
        (row,) = _run(monkeypatch, books)[day]
        assert ("fractional-era" in row.tags) is tagged


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


class TestHistory:
    """RK-02: --reverse with --follow stops at the rename and returns one commit."""

    def test_history_walks_back_through_a_rename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        books = repo / "data" / "portfolios"
        books.mkdir(parents=True)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@example.invalid")
        _git(repo, "config", "user.name", "t")
        _git(repo, "config", "commit.gpgsign", "false")
        body = {"last_open_date": "2026-08-13", "cash": 1.0,
                "positions": [{"ticker": "A", "shares": 1, "entry_price": 1.0,
                               "current_price": 1.0, "pad": "x" * 400}]}
        (books / "the_council.json").write_text(json.dumps(body, indent=1))
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "one")
        _git(repo, "mv", "data/portfolios/the_council.json",
             "data/portfolios/mohnish_pabrai.json")
        _git(repo, "commit", "-q", "-m", "rename")
        body["positions"] = []
        body["last_open_date"] = "2026-08-17"
        (books / "mohnish_pabrai.json").write_text(json.dumps(body, indent=1))
        _git(repo, "commit", "-q", "-am", "sell")

        monkeypatch.chdir(repo)
        history = bf._history("mohnish_pabrai")

        assert [p for _, p in history] == [
            "data/portfolios/the_council.json",
            "data/portfolios/mohnish_pabrai.json",
            "data/portfolios/mohnish_pabrai.json",
        ]
        first = bf._book_at(history[0][0], history[0][1])
        assert first is not None and first["positions"][0]["ticker"] == "A"


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


class TestClosedSession:
    """A row dated to a day the market was shut says so (audit RK-06).

    The books were diffed per commit and dated by the commit, so a
    commit on Labor Day produced sales on Labor Day. The rows stay —
    deleting history is the owner's call — but carry ``closed-session``,
    so a rerun with ``--refresh-reconstructed`` keeps the tag the
    one-off tagging wrote.
    """

    @pytest.mark.parametrize(("day", "tagged"), [
        ("2026-09-07", True),   # Labor Day
        ("2026-08-16", True),   # a Sunday
        ("2026-09-08", False),  # an ordinary Tuesday
    ])
    def test_a_sale_on_a_shut_day_is_tagged(
        self, monkeypatch: pytest.MonkeyPatch, day: str, tagged: bool
    ) -> None:
        books = {
            "a": _book("2026-09-01", [_pos("X", 2, 10.0, "2026-09-01", 11.0)]),
            "b": _book(day, []),
        }
        (row,) = _run(monkeypatch, books)[day]
        assert ("closed-session" in row.tags) is tagged
