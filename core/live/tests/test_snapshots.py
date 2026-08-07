"""Tests for daily snapshot persistence."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from core.live.portfolio import LivePortfolio, TradeRecord
from core.live.snapshots import make_snapshot, save_snapshot


class TestTheCloseRunDoesNotEraseTheMorningsTrades:
    """Two runs write the same file each day.

    The morning scan executes the rotations and passes them in; the
    close-of-day mark re-values the book and passes ``trades=[]``
    because it does not trade. save_snapshot overwrote unconditionally,
    so the close run erased the morning's record every day. On the live
    data that was 68 of 68 snapshots holding zero trades for portfolios
    that had plainly been traded — every transaction the system ever
    made was gone within hours, and the dashboard had no history to
    show.
    """

    @staticmethod
    def _trade(ticker: str, side: str) -> TradeRecord:
        return TradeRecord(
            ticker=ticker,
            side=side,
            shares=10.0,
            price=100.0,
            gross_value=1_000.0,
            cost_paid=1.0,
            realized_pnl_usd=0.0,
        )

    def test_a_tradeless_save_keeps_what_the_day_recorded(
        self, tmp_path: Path
    ) -> None:
        p = LivePortfolio(agent="graham", cash=1_000.0)
        morning = make_snapshot(
            p, as_of=date(2026, 8, 7), trades=[self._trade("ASGN", "BUY")]
        )
        save_snapshot(morning, root=tmp_path)

        close = make_snapshot(p, as_of=date(2026, 8, 7), trades=[])
        save_snapshot(close, root=tmp_path)

        stored = json.loads((tmp_path / "graham" / "2026-08-07.json").read_text())
        assert stored["trade_count"] == 1
        assert stored["buys"] == ["ASGN"]

    def test_the_close_run_still_updates_the_valuation(
        self, tmp_path: Path
    ) -> None:
        # Preserving trades must not freeze the numbers the close run
        # exists to refresh.
        p = LivePortfolio(agent="graham", cash=1_000.0)
        save_snapshot(
            make_snapshot(
                p, as_of=date(2026, 8, 7), trades=[self._trade("ASGN", "BUY")]
            ),
            root=tmp_path,
        )

        p.cash = 2_500.0
        save_snapshot(
            make_snapshot(p, as_of=date(2026, 8, 7), trades=[]), root=tmp_path
        )

        stored = json.loads((tmp_path / "graham" / "2026-08-07.json").read_text())
        assert stored["cash"] == 2_500.0
        assert stored["trade_count"] == 1

    def test_a_rerun_with_trades_replaces_rather_than_appends(
        self, tmp_path: Path
    ) -> None:
        # A corrected morning scan should overwrite its own record, not
        # accumulate a duplicate.
        p = LivePortfolio(agent="graham", cash=1_000.0)
        save_snapshot(
            make_snapshot(
                p, as_of=date(2026, 8, 7), trades=[self._trade("ASGN", "BUY")]
            ),
            root=tmp_path,
        )
        save_snapshot(
            make_snapshot(
                p, as_of=date(2026, 8, 7), trades=[self._trade("CRMD", "BUY")]
            ),
            root=tmp_path,
        )

        stored = json.loads((tmp_path / "graham" / "2026-08-07.json").read_text())
        assert stored["buys"] == ["CRMD"]
        assert stored["trade_count"] == 1

    def test_a_genuinely_quiet_day_records_nothing(
        self, tmp_path: Path
    ) -> None:
        # Guard against over-correction: no trades ever means no trades.
        p = LivePortfolio(agent="graham", cash=1_000.0)
        save_snapshot(
            make_snapshot(p, as_of=date(2026, 8, 7), trades=[]), root=tmp_path
        )
        save_snapshot(
            make_snapshot(p, as_of=date(2026, 8, 7), trades=[]), root=tmp_path
        )

        stored = json.loads((tmp_path / "graham" / "2026-08-07.json").read_text())
        assert stored["trade_count"] == 0
