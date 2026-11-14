"""Tests for daily snapshot persistence."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

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

    def test_a_close_mark_keeps_the_closed_session_tag(
        self, tmp_path: Path
    ) -> None:
        # The day was tagged; a later trade-less close-of-day save
        # rebuilds the snapshot from the book, which knows nothing of
        # the tag, and ``to_dict`` drops a False marker.
        self._write(tmp_path, {"closed_session": True})
        book = LivePortfolio(agent="a", cash=100.0)

        path = save_snapshot(
            make_snapshot(book, as_of=date(2026, 9, 7)), root=tmp_path
        )

        assert json.loads(path.read_text())["closed_session"] is True
        (snap,) = load_snapshots("a", root=tmp_path)
        assert snap.closed_session is True

    def test_a_close_mark_keeps_the_restoration_provenance(
        self, tmp_path: Path
    ) -> None:
        self._write(tmp_path, {"restored_from": "8ac0c8bf"})
        book = LivePortfolio(agent="a", cash=100.0)

        path = save_snapshot(
            make_snapshot(book, as_of=date(2026, 9, 7)), root=tmp_path
        )

        assert json.loads(path.read_text())["restored_from"] == "8ac0c8bf"
        (snap,) = load_snapshots("a", root=tmp_path)
        assert snap.restored_from == "8ac0c8bf"

    def test_a_save_that_carries_trades_and_freshness_keeps_both(
        self, tmp_path: Path
    ) -> None:
        # A re-run scan replaces trades and freshness outright, so it
        # had no reason to read the prior file at all; the provenance
        # marker is about the day, not the run, and must survive it too.
        # The closed-session tag does not: a save that booked trades
        # contradicts it (see TestTheClosedSessionMarkerCanBeCorrected).
        self._write(
            tmp_path, {"closed_session": True, "restored_from": "8ac0c8bf"}
        )
        book = LivePortfolio(agent="a", cash=100.0)
        trade = TradeRecord(
            ticker="AAA", side="BUY", shares=1.0, price=10.0,
            gross_value=10.0, cost_paid=0.01, realized_pnl_usd=0.0,
        )

        path = save_snapshot(
            make_snapshot(
                book,
                as_of=date(2026, 9, 7),
                trades=[trade],
                fundamentals_newest_filed="2026-08-01",
                fundamentals_stale=False,
            ),
            root=tmp_path,
        )

        written = json.loads(path.read_text())
        assert written["trade_count"] == 1
        assert "closed_session" not in written
        assert written["restored_from"] == "8ac0c8bf"


@pytest.fixture
def warnings_seen() -> Iterator[list[str]]:
    """Every WARNING-or-worse message logged while the test runs.

    The project logs through loguru, which pytest's caplog never sees.
    """
    from loguru import logger as loguru_logger

    seen: list[str] = []
    sink_id = loguru_logger.add(
        lambda m: seen.append(m.record["message"]), level="WARNING"
    )
    try:
        yield seen
    finally:
        loguru_logger.remove(sink_id)


class TestTheClosedSessionMarkerCanBeCorrected:
    """``closed_session`` is tri-state, so a wrong tag is not forever.

    It used to be a one-way ratchet: a True in the day's file survived
    every later save, so a day tagged by mistake could only be fixed by
    hand-editing JSON. Now ``None`` means "this save did not assess it"
    and inherits, while an explicit ``False`` — what the runner passes,
    having just asked the market calendar — clears it.
    """

    _write = staticmethod(TestProvenanceMarkers._write)

    @staticmethod
    def _book() -> LivePortfolio:
        return LivePortfolio(agent="a", cash=100.0)

    def test_an_explicit_false_clears_a_wrong_tag(self, tmp_path: Path) -> None:
        self._write(tmp_path, {"closed_session": True})

        path = save_snapshot(
            make_snapshot(self._book(), as_of=date(2026, 9, 7), closed_session=False),
            root=tmp_path,
        )

        # Cleared, and written the way an ordinary day always was.
        assert "closed_session" not in json.loads(path.read_text())
        (snap,) = load_snapshots("a", root=tmp_path)
        assert snap.closed_session is False

    def test_a_save_that_did_not_assess_it_inherits(self, tmp_path: Path) -> None:
        self._write(tmp_path, {"closed_session": True})
        snap = make_snapshot(self._book(), as_of=date(2026, 9, 7))
        assert snap.closed_session is None

        path = save_snapshot(snap, root=tmp_path)

        assert json.loads(path.read_text())["closed_session"] is True

    def test_a_day_with_trades_does_not_inherit_the_tag(
        self, tmp_path: Path, warnings_seen: list[str]
    ) -> None:
        # Trades booked today and "the market was shut today" cannot both
        # be true; the save that carries the trades wins, loudly.
        self._write(tmp_path, {"closed_session": True})
        trade = TradeRecord(
            ticker="AAA", side="BUY", shares=1.0, price=10.0,
            gross_value=10.0, cost_paid=0.01, realized_pnl_usd=0.0,
        )

        path = save_snapshot(
            make_snapshot(self._book(), as_of=date(2026, 9, 7), trades=[trade]),
            root=tmp_path,
        )

        written = json.loads(path.read_text())
        assert written["trade_count"] == 1
        assert "closed_session" not in written
        assert any("closed_session" in m and "a@2026-09-07" in m for m in warnings_seen)

    def test_a_tagged_day_keeps_its_tag_and_its_old_trades(
        self, tmp_path: Path
    ) -> None:
        # The holidays tagged by ``scripts.tag_closed_sessions`` include
        # the ones that booked trades. A trade-less save carries those
        # trades over; it adds no contradiction, so the tag stays.
        self._write(
            tmp_path, {"closed_session": True, "buys": ["AAA"], "trade_count": 1}
        )

        path = save_snapshot(
            make_snapshot(self._book(), as_of=date(2026, 9, 7)), root=tmp_path
        )

        written = json.loads(path.read_text())
        assert written["trade_count"] == 1
        assert written["closed_session"] is True

    def test_a_non_string_restored_from_is_not_inherited(
        self, tmp_path: Path
    ) -> None:
        self._write(tmp_path, {"restored_from": 12345})

        path = save_snapshot(
            make_snapshot(self._book(), as_of=date(2026, 9, 7)), root=tmp_path
        )

        assert "restored_from" not in json.loads(path.read_text())

    def test_a_prior_file_that_is_not_an_object_is_overwritten(
        self, tmp_path: Path, warnings_seen: list[str]
    ) -> None:
        # ``prior.get`` on a list raised AttributeError, so the day's
        # record was never written at all.
        folder = tmp_path / "a"
        folder.mkdir()
        (folder / "2026-09-07.json").write_text("[1, 2, 3]")

        path = save_snapshot(
            make_snapshot(self._book(), as_of=date(2026, 9, 7)), root=tmp_path
        )

        assert json.loads(path.read_text())["agent"] == "a"
        assert any("a@2026-09-07" in m and "not a JSON object" in m for m in warnings_seen)
