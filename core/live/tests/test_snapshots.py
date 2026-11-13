"""Tests for daily snapshot persistence."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from core.live.portfolio import LivePortfolio, TradeRecord
from core.live.snapshots import load_snapshots, make_snapshot, save_snapshot


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


class TestSyncCollisionCopies:
    """A "<day> 2.json" sync copy is not a second day (audit RK-03).

    load_snapshots globbed "*.json", so seth_klarman's history held
    2026-11-06 twice on the Mac and a NAV chart drew the day twice.
    """

    def test_a_collision_copy_does_not_repeat_the_day(
        self, tmp_path: Path
    ) -> None:
        p = LivePortfolio(agent="graham", cash=1_000.0)
        path = save_snapshot(
            make_snapshot(p, as_of=date(2026, 11, 6), trades=[]), root=tmp_path
        )
        (path.parent / "2026-11-06 2.json").write_text(path.read_text())

        got = load_snapshots("graham", root=tmp_path)

        assert [s.date for s in got] == ["2026-11-06"]


class TestFundamentalsStalenessFlag:
    """The open scan records how old the filings it read were (FUND-2).

    The close mark reads no filings, so it measures nothing and must not
    wipe what the morning recorded — the same shape as the trades above.
    """

    def test_the_flag_is_written_and_read_back(self, tmp_path: Path) -> None:
        p = LivePortfolio(agent="graham", cash=1_000.0)
        snap = make_snapshot(
            p,
            as_of=date(2026, 11, 13),
            fundamentals_newest_filed="2026-04-28",
            fundamentals_stale=True,
        )
        save_snapshot(snap, root=tmp_path)

        stored = json.loads((tmp_path / "graham" / "2026-11-13.json").read_text())
        assert stored["fundamentals_stale"] is True
        assert stored["fundamentals_newest_filed"] == "2026-04-28"
        assert load_snapshots("graham", root=tmp_path)[0].fundamentals_stale is True

    def test_the_close_mark_keeps_the_mornings_flag(self, tmp_path: Path) -> None:
        p = LivePortfolio(agent="graham", cash=1_000.0)
        save_snapshot(
            make_snapshot(
                p,
                as_of=date(2026, 11, 13),
                fundamentals_newest_filed="2026-04-28",
                fundamentals_stale=True,
            ),
            root=tmp_path,
        )

        save_snapshot(make_snapshot(p, as_of=date(2026, 11, 13)), root=tmp_path)

        stored = json.loads((tmp_path / "graham" / "2026-11-13.json").read_text())
        assert stored["fundamentals_stale"] is True
        assert stored["fundamentals_newest_filed"] == "2026-04-28"

    def test_a_rerun_scan_that_finds_fresh_filings_clears_the_flag(
        self, tmp_path: Path
    ) -> None:
        p = LivePortfolio(agent="graham", cash=1_000.0)
        for stale, filed in ((True, "2026-04-28"), (False, "2026-11-12")):
            save_snapshot(
                make_snapshot(
                    p,
                    as_of=date(2026, 11, 13),
                    fundamentals_newest_filed=filed,
                    fundamentals_stale=stale,
                ),
                root=tmp_path,
            )

        stored = json.loads((tmp_path / "graham" / "2026-11-13.json").read_text())
        assert stored["fundamentals_stale"] is False
        assert stored["fundamentals_newest_filed"] == "2026-11-12"

    def test_a_snapshot_written_before_the_field_existed_still_loads(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "graham").mkdir()
        old = make_snapshot(LivePortfolio(agent="graham"), as_of=date(2026, 8, 7))
        legacy = old.to_dict()
        legacy.pop("fundamentals_stale")
        legacy.pop("fundamentals_newest_filed")
        (tmp_path / "graham" / "2026-08-07.json").write_text(json.dumps(legacy))

        loaded = load_snapshots("graham", root=tmp_path)[0]

        assert loaded.fundamentals_stale is None
        assert loaded.fundamentals_newest_filed is None


class TestProvenanceMarkers:
    """``closed_session`` and ``restored_from`` (audit RK-06).

    55 committed snapshots sit on days the market was shut and one day,
    2026-08-06, was restored from the commit that misdated it. Both are
    annotated in the file rather than deleted or silently rewritten, and
    ``load_snapshots`` builds the dataclass from every key — so a key it
    does not know makes it drop the whole day with a warning.
    """

    @staticmethod
    def _write(root: Path, extra: dict[str, object]) -> None:
        folder = root / "a"
        folder.mkdir(parents=True)
        (folder / "2026-09-07.json").write_text(json.dumps({
            "agent": "a", "date": "2026-09-07", "nav": 1.0, "cash": 1.0,
            "invested": 0.0, "pnl_usd": 0.0, "pnl_pct": 0.0,
            "position_count": 0, "watchlist_count": 0, "buys": [],
            "sells": [], "trade_count": 0, **extra,
        }))

    def test_a_tagged_snapshot_still_loads(self, tmp_path: Path) -> None:
        self._write(tmp_path, {"closed_session": True})

        (snap,) = load_snapshots("a", root=tmp_path)

        assert snap.closed_session is True

    def test_a_restored_snapshot_still_loads(self, tmp_path: Path) -> None:
        self._write(tmp_path, {"restored_from": "8ac0c8bf"})

        (snap,) = load_snapshots("a", root=tmp_path)

        assert snap.restored_from == "8ac0c8bf"
        assert snap.closed_session is False

    def test_an_ordinary_snapshot_is_written_without_them(
        self, tmp_path: Path
    ) -> None:
        # Every file the runner writes would otherwise grow two keys
        # that say nothing.
        book = LivePortfolio(agent="a", cash=100.0)
        path = save_snapshot(
            make_snapshot(book, as_of=date(2026, 9, 8)), root=tmp_path
        )

        written = json.loads(path.read_text())

        assert "closed_session" not in written
        assert "restored_from" not in written
