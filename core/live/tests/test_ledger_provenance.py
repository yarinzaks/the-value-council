"""A session caught up later says so, without pretending it traded later.

The schedule missed the 2026-10-30 session, so it was run on the 31st —
a Saturday. Two things had to stay true at once. The rows belong to
Friday, because that is the session whose prices they were filled at
and a ledger that dated them to Saturday would claim a trade on a day
the market was shut. And a reader finding a Friday book written on a
Saturday should find the answer in the file rather than having to
notice that two timestamps disagree.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from core.live.portfolio import TradeRecord
from core.live.trade_ledger import LedgerEntry, record_day

FRIDAY = date(2026, 10, 30)
SATURDAY = date(2026, 10, 31)


def a_trade() -> TradeRecord:
    return TradeRecord(
        ticker="HOG", side="BUY", shares=10.0, price=30.33,
        gross_value=303.30, cost_paid=0.15, realized_pnl_usd=0.0,
    )


def rows(path: Path) -> list[dict]:
    return json.loads(path.read_text())


class TestASameDayRunIsUnchanged:
    def test_it_is_marked_executed(self, tmp_path: Path) -> None:
        written = record_day("a", FRIDAY, [a_trade()], directory=tmp_path,
                             recorded_on=FRIDAY)

        assert rows(written)[0]["source"] == "executed"

    def test_it_carries_no_recorded_on_at_all(self, tmp_path: Path) -> None:
        """An absent field, not a redundant one repeating the date."""
        written = record_day("a", FRIDAY, [a_trade()], directory=tmp_path,
                             recorded_on=FRIDAY)

        assert "recorded_on" not in rows(written)[0]

    def test_omitting_it_entirely_is_the_same(self, tmp_path: Path) -> None:
        written = record_day("a", FRIDAY, [a_trade()], directory=tmp_path)

        assert rows(written)[0]["source"] == "executed"
        assert "recorded_on" not in rows(written)[0]


class TestACatchUpSaysSo:
    @pytest.fixture
    def written(self, tmp_path: Path) -> list[dict]:
        path = record_day("a", FRIDAY, [a_trade()], directory=tmp_path,
                          recorded_on=SATURDAY)
        assert path is not None
        return rows(path)

    def test_the_row_is_dated_to_the_session_not_the_saturday(
        self, written: list[dict]
    ) -> None:
        """The whole point: no trade is ever dated to a shut market."""
        assert written[0]["date"] == "2026-10-30"

    def test_it_is_marked_caught_up(self, written: list[dict]) -> None:
        assert written[0]["source"] == "caught-up"

    def test_it_names_the_day_it_was_actually_written(
        self, written: list[dict]
    ) -> None:
        assert written[0]["recorded_on"] == "2026-10-31"

    def test_the_figures_are_the_sessions_own(self, written: list[dict]) -> None:
        """Caught up is exact, unlike reconstructed: same code, same
        prices, performed late."""
        assert written[0]["price"] == 30.33
        assert written[0]["shares"] == 10.0

    def test_it_survives_a_round_trip(self, written: list[dict]) -> None:
        restored = LedgerEntry.from_dict(written[0])

        assert restored.source == "caught-up"
        assert restored.recorded_on == "2026-10-31"
        assert restored.date == "2026-10-30"


class TestTheThreeKindsStayDistinct:
    def test_reconstructed_is_still_its_own_thing(self, tmp_path: Path) -> None:
        """Caught up is exact; reconstructed is inferred. Collapsing the
        two would show a guess as a record."""
        entry = LedgerEntry.from_dict(
            {"date": "2026-08-07", "agent": "a", "ticker": "X", "side": "SELL",
             "shares": 1, "price": 1, "source": "reconstructed"}
        )

        assert entry.source == "reconstructed"
        assert entry.recorded_on is None
