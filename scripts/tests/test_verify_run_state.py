"""Tests for the post-run freshness gate.

The gate exists because the previous check could not fail. These tests
pin down the one behaviour that matters: a portfolio that did not get
written by today's run must produce a non-zero exit.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.verify_run_state import (
    STAMP_FIELD,
    check_portfolios,
    format_report,
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
) -> Path:
    payload = {
        "agent": agent,
        "total_nav": nav,
        "cash": 500.0,
        "positions": [{"ticker": f"T{i}"} for i in range(positions)],
        "last_open_run": open_stamp,
        "last_close_run": close_stamp,
    }
    path = directory / f"{agent}.json"
    path.write_text(json.dumps(payload))
    return path


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
        code = main(
            ["--mode", "open", "--as-of", "2026-08-05", "--portfolios", str(tmp_path)]
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
        code = main(
            ["--mode", "open", "--as-of", "2026-08-05", "--portfolios", str(tmp_path)]
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
