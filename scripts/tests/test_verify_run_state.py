"""Tests for the post-run freshness gate.

The gate exists because the previous check could not fail. These tests
pin down the one behaviour that matters: a portfolio that did not get
written by today's run must produce a non-zero exit.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts.verify_run_state import (
    STAMP_FIELD,
    check_portfolios,
    format_report,
    last_bar_reader,
    main,
)

AS_OF = date(2026, 8, 5)


def _write(
    directory: Path,
    agent: str,
    *,
    open_stamp: str = "2026-08-05T14:42:03+00:00",
    close_stamp: str = "2026-08-05T20:29:21+00:00",
    positions: int = 3,
    nav: float = 10_500.0,
    position_rows: list[dict[str, Any]] | None = None,
) -> Path:
    payload = {
        "agent": agent,
        "total_nav": nav,
        "cash": 500.0,
        "positions": (
            position_rows
            if position_rows is not None
            else [{"ticker": f"T{i}"} for i in range(positions)]
        ),
        "last_open_run": open_stamp,
        "last_close_run": close_stamp,
    }
    path = directory / f"{agent}.json"
    path.write_text(json.dumps(payload))
    return path


def _prices_db(path: Path, bars: dict[str, str]) -> Path:
    """A price cache holding one bar per ticker, on the given date.

    Same table and columns as ``PriceDataLoader``'s cache; only the
    columns the verifier reads are filled.
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE prices (ticker TEXT NOT NULL, trade_date TEXT NOT NULL, "
            "open REAL, high REAL, low REAL, close REAL, adj_close REAL, "
            "volume INTEGER, PRIMARY KEY (ticker, trade_date))"
        )
        conn.executemany(
            "INSERT INTO prices (ticker, trade_date, close, adj_close) "
            "VALUES (?, ?, 1.0, 1.0)",
            list(bars.items()),
        )
        conn.commit()
    finally:
        conn.close()
    return path


#: Fresh bars for the three placeholder tickers ``_write`` puts in a book.
FRESH = {"T0": "2026-08-05", "T1": "2026-08-05", "T2": "2026-08-04"}


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------
class TestFreshRun:
    def test_all_agents_fresh_passes(self, tmp_path: Path) -> None:
        _write(tmp_path, "warren_buffett")
        _write(tmp_path, "benjamin_graham")

        report = check_portfolios(tmp_path, mode="open", as_of=AS_OF)

        assert report.ok
        assert report.stale == []
        assert [r.agent for r in report.rows] == [
            "benjamin_graham",
            "warren_buffett",
        ]

    def test_close_mode_reads_the_close_stamp(self, tmp_path: Path) -> None:
        # Fresh close, stale open — a close run must not be judged on the
        # morning's timestamp.
        _write(
            tmp_path,
            "john_neff",
            open_stamp="2026-07-01T14:00:00+00:00",
            close_stamp="2026-08-05T20:29:21+00:00",
        )

        assert check_portfolios(tmp_path, mode="close", as_of=AS_OF).ok
        assert not check_portfolios(tmp_path, mode="open", as_of=AS_OF).ok

    def test_exit_zero(self, tmp_path: Path) -> None:
        _write(tmp_path, "peter_lynch")
        db = _prices_db(tmp_path / "prices.sqlite", FRESH)
        code = main(
            [
                "--mode", "open", "--as-of", "2026-08-05",
                "--portfolios", str(tmp_path), "--prices-db", str(db),
            ]
        )
        assert code == 0


# ---------------------------------------------------------------------------
# Failure modes — each of these used to pass silently
# ---------------------------------------------------------------------------
class TestStaleRun:
    def test_one_stale_agent_fails_the_run(self, tmp_path: Path) -> None:
        _write(tmp_path, "warren_buffett")
        _write(tmp_path, "seth_klarman", open_stamp="2026-08-04T14:42:03+00:00")

        report = check_portfolios(tmp_path, mode="open", as_of=AS_OF)

        assert not report.ok
        assert report.stale == ["seth_klarman"]

    def test_never_run_portfolio_is_stale(self, tmp_path: Path) -> None:
        _write(tmp_path, "philip_fisher", open_stamp="")

        report = check_portfolios(tmp_path, mode="open", as_of=AS_OF)

        assert report.stale == ["philip_fisher"]

    def test_malformed_stamp_is_stale_not_a_crash(self, tmp_path: Path) -> None:
        _write(tmp_path, "howard_marks", open_stamp="not-a-timestamp")

        report = check_portfolios(tmp_path, mode="open", as_of=AS_OF)

        assert report.stale == ["howard_marks"]

    def test_empty_directory_fails(self, tmp_path: Path) -> None:
        report = check_portfolios(tmp_path, mode="open", as_of=AS_OF)

        assert not report.ok
        assert report.rows == []

    def test_unreadable_portfolio_fails(self, tmp_path: Path) -> None:
        (tmp_path / "corrupt.json").write_text("{not json")

        report = check_portfolios(tmp_path, mode="open", as_of=AS_OF)

        assert not report.ok
        assert len(report.unreadable) == 1

    def test_holding_positions_does_not_excuse_staleness(
        self, tmp_path: Path
    ) -> None:
        # The old gate asked only whether any portfolio held positions.
        # This is the exact state it waved through.
        _write(
            tmp_path,
            "walter_schloss",
            positions=30,
            open_stamp="2026-05-08T19:54:33+00:00",
        )

        report = check_portfolios(tmp_path, mode="open", as_of=AS_OF)

        assert not report.ok
        assert report.stale == ["walter_schloss"]

    def test_exit_one(self, tmp_path: Path) -> None:
        _write(tmp_path, "david_dreman", open_stamp="2026-08-04T14:42:03+00:00")
        db = _prices_db(tmp_path / "prices.sqlite", FRESH)
        code = main(
            [
                "--mode", "open", "--as-of", "2026-08-05",
                "--portfolios", str(tmp_path), "--prices-db", str(db),
            ]
        )
        assert code == 1


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
class TestFormatReport:
    def test_stale_agents_are_annotated_as_errors(self, tmp_path: Path) -> None:
        _write(tmp_path, "joel_greenblatt", open_stamp="2026-08-04T14:42:03+00:00")
        report = check_portfolios(tmp_path, mode="open", as_of=AS_OF)

        text = format_report(report, mode="open", as_of=AS_OF)

        assert "::error::" in text
        assert "joel_greenblatt" in text
        assert "STALE" in text

    def test_clean_report_has_no_error_annotation(self, tmp_path: Path) -> None:
        _write(tmp_path, "john_neff")
        report = check_portfolios(tmp_path, mode="open", as_of=AS_OF)

        text = format_report(report, mode="open", as_of=AS_OF)

        assert "::error::" not in text
        assert "all 1 agents completed" in text


class TestModeGuard:
    def test_unknown_mode_raises(self, tmp_path: Path) -> None:
        with pytest.raises(KeyError):
            check_portfolios(tmp_path, mode="midday", as_of=AS_OF)

    def test_both_modes_are_covered(self) -> None:
        assert set(STAMP_FIELD) == {"open", "close"}


# ---------------------------------------------------------------------------
# Unpriced holdings (audit CE-1a / PD-09 / SF-X4)
# ---------------------------------------------------------------------------
def _leg(*, unpriced_since: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticker": "LEG",
        "shares": 17.0,
        "entry_price": 9.66,
        "entry_date": "2026-07-01",
        "current_price": 9.2,
    }
    if unpriced_since is not None:
        row["unpriced_since"] = unpriced_since
    return row


class TestUnpricedHoldings:
    """A run that leaves an unpriced holding unflagged has failed.

    The runner flags a held name once it has gone more than five
    sessions without a bar. This gate checks that independently, from
    the price cache rather than from the flag itself, so a runner that
    stopped flagging is caught on the day rather than 78 days later.
    """

    def test_unpriced_position_fails_verify(self, tmp_path: Path) -> None:
        books = tmp_path / "portfolios"
        books.mkdir()
        _write(books, "seth_klarman", position_rows=[_leg()])
        # Last bar Tue 28 July; Wed 5 August is the sixth session without.
        db = _prices_db(tmp_path / "prices.sqlite", {"LEG": "2026-07-28"})

        report = check_portfolios(
            books, mode="open", as_of=AS_OF, last_bar=last_bar_reader(db)
        )

        assert not report.ok
        assert report.unflagged == ["seth_klarman:LEG (no bar since 2026-07-28)"]
        text = format_report(report, mode="open", as_of=AS_OF)
        assert "::error::" in text and "LEG" in text

    def test_a_flagged_unpriced_position_passes_and_is_reported(
        self, tmp_path: Path
    ) -> None:
        books = tmp_path / "portfolios"
        books.mkdir()
        _write(
            books, "seth_klarman", position_rows=[_leg(unpriced_since="2026-07-29")]
        )
        db = _prices_db(tmp_path / "prices.sqlite", {"LEG": "2026-07-28"})

        report = check_portfolios(
            books, mode="open", as_of=AS_OF, last_bar=last_bar_reader(db)
        )

        assert report.ok
        assert report.unflagged == []
        assert report.flagged == ["seth_klarman:LEG (unpriced since 2026-07-29)"]
        text = format_report(report, mode="open", as_of=AS_OF)
        assert "::warning::" in text and "LEG" in text
        assert "::error::" not in text

    def test_five_sessions_without_a_bar_is_not_yet_unpriced(
        self, tmp_path: Path
    ) -> None:
        books = tmp_path / "portfolios"
        books.mkdir()
        _write(books, "seth_klarman", position_rows=[_leg()])
        db = _prices_db(tmp_path / "prices.sqlite", {"LEG": "2026-07-29"})

        report = check_portfolios(
            books, mode="open", as_of=AS_OF, last_bar=last_bar_reader(db)
        )

        assert report.ok
        assert report.unflagged == []

    def test_a_holding_with_no_bar_at_all_must_be_flagged(
        self, tmp_path: Path
    ) -> None:
        books = tmp_path / "portfolios"
        books.mkdir()
        _write(books, "seth_klarman", position_rows=[_leg()])
        db = _prices_db(tmp_path / "prices.sqlite", {})

        report = check_portfolios(
            books, mode="open", as_of=AS_OF, last_bar=last_bar_reader(db)
        )

        assert report.unflagged == ["seth_klarman:LEG (no bar at all)"]

    def test_without_a_price_source_the_check_is_not_run(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "seth_klarman", position_rows=[_leg()])

        report = check_portfolios(tmp_path, mode="open", as_of=AS_OF)

        assert report.ok
        assert report.unflagged == []

    def test_main_fails_a_run_that_leaves_one_unflagged(
        self, tmp_path: Path
    ) -> None:
        books = tmp_path / "portfolios"
        books.mkdir()
        _write(books, "seth_klarman", position_rows=[_leg()])
        db = _prices_db(tmp_path / "prices.sqlite", {"LEG": "2026-07-28"})

        code = main(
            [
                "--mode", "close", "--as-of", "2026-08-05",
                "--portfolios", str(books), "--prices-db", str(db),
            ]
        )

        assert code == 1

    def test_main_fails_when_the_price_cache_is_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Nothing to check against is not the same as nothing wrong.
        _write(tmp_path, "peter_lynch")

        code = main(
            [
                "--mode", "open", "--as-of", "2026-08-05",
                "--portfolios", str(tmp_path),
                "--prices-db", str(tmp_path / "absent.sqlite"),
            ]
        )

        assert code == 1
        assert "price cache" in capsys.readouterr().out
        # And it must not have created the file it looked for.
        assert not (tmp_path / "absent.sqlite").exists()
