"""The breaker's input must be readable, and say so when it is not.

The whole point is E1: an agent that cannot read its own equity curve is
exactly the one that should not be opening positions, so an unreadable
history answers None rather than assuming no drawdown.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.council.nav_history import current_nav, drawdown_from_peak, peak_nav


def seed(tmp_path: Path, *, nav: float, history: list[float]) -> tuple[Path, Path]:
    portfolios = tmp_path / "portfolios"
    portfolios.mkdir()
    (portfolios / "agent.json").write_text(json.dumps({"total_nav": nav}))

    snapshots = tmp_path / "snapshots"
    (snapshots / "agent").mkdir(parents=True)
    for i, value in enumerate(history):
        (snapshots / "agent" / f"2026-01-{i + 1:02d}.json").write_text(
            json.dumps({"nav": value})
        )
    return portfolios, snapshots


class TestCurrentNav:
    def test_it_reads_the_portfolio(self, tmp_path: Path) -> None:
        portfolios, _ = seed(tmp_path, nav=9_500.0, history=[])
        assert current_nav("agent", directory=portfolios) == 9_500.0

    def test_a_missing_portfolio_is_none(self, tmp_path: Path) -> None:
        assert current_nav("nobody", directory=tmp_path) is None


class TestPeakNav:
    def test_it_takes_the_highest_snapshot(self, tmp_path: Path) -> None:
        _, snapshots = seed(tmp_path, nav=0.0, history=[100.0, 130.0, 110.0])
        assert peak_nav("agent", snapshots=snapshots) == 130.0

    def test_todays_nav_can_be_the_new_peak(self, tmp_path: Path) -> None:
        """A book that just made a high must not report a drawdown."""
        _, snapshots = seed(tmp_path, nav=0.0, history=[100.0, 130.0])
        assert peak_nav("agent", snapshots=snapshots, include=150.0) == 150.0

    def test_a_corrupt_snapshot_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        _, snapshots = seed(tmp_path, nav=0.0, history=[100.0, 130.0])
        (snapshots / "agent" / "2026-01-09.json").write_text("{not json")
        assert peak_nav("agent", snapshots=snapshots) == 130.0

    def test_no_history_is_none(self, tmp_path: Path) -> None:
        assert peak_nav("agent", snapshots=tmp_path) is None


class TestDrawdown:
    def test_a_book_at_its_peak_has_none(self, tmp_path: Path) -> None:
        portfolios, snapshots = seed(tmp_path, nav=130.0, history=[100.0, 130.0])
        assert drawdown_from_peak(
            "agent", portfolios=portfolios, snapshots=snapshots
        ) == 0.0

    def test_a_loss_is_negative(self, tmp_path: Path) -> None:
        portfolios, snapshots = seed(tmp_path, nav=75.0, history=[100.0])
        value = drawdown_from_peak(
            "agent", portfolios=portfolios, snapshots=snapshots
        )
        assert value is not None
        assert round(value, 6) == -0.25

    def test_an_unreadable_portfolio_is_none(self, tmp_path: Path) -> None:
        """None blocks entries; assuming no drawdown would not."""
        _, snapshots = seed(tmp_path, nav=0.0, history=[100.0])
        assert (
            drawdown_from_peak(
                "agent", portfolios=tmp_path / "nowhere", snapshots=snapshots
            )
            is None
        )

    def test_a_new_high_today_reports_no_drawdown(self, tmp_path: Path) -> None:
        portfolios, snapshots = seed(tmp_path, nav=200.0, history=[100.0, 130.0])
        assert drawdown_from_peak(
            "agent", portfolios=portfolios, snapshots=snapshots
        ) == 0.0

    def test_it_feeds_the_breaker(self, tmp_path: Path) -> None:
        from agents.council.exits import entries_blocked

        portfolios, snapshots = seed(tmp_path, nav=70.0, history=[100.0])
        value = drawdown_from_peak(
            "agent", portfolios=portfolios, snapshots=snapshots
        )
        blocked, reason = entries_blocked(value)
        assert blocked
        assert "circuit breaker" in reason
