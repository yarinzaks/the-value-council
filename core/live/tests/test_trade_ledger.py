"""The ledger, and the breakdown that makes a return legible.

Graham stood at +24.55% with four of five open positions losing money.
Both facts were true and together they read as a contradiction, because
nothing on disk could say that closed trades had made $3,239 while the
open book was down $704. realized_pnl_usd was computed at every sale and
discarded; the snapshot kept the ticker and nothing else.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from core.live.portfolio import LivePortfolio, Position, TradeRecord
from core.live.trade_ledger import (
    LedgerEntry,
    decompose_return,
    read_ledger,
    realized_by_ticker,
    record_day,
)

AS_OF = date(2026, 8, 7)


def _trade(ticker: str, side: str, *, shares: float = 10.0, price: float = 20.0,
           realized: float = 0.0) -> TradeRecord:
    return TradeRecord(
        ticker=ticker, side=side, shares=shares, price=price,
        gross_value=shares * price, cost_paid=shares * price * 0.001,
        realized_pnl_usd=realized,
    )


class TestRecordingADay:
    def test_a_sale_keeps_the_number_that_used_to_be_thrown_away(
        self, tmp_path: Path
    ) -> None:
        record_day("g", AS_OF, [_trade("TASK", "SELL", realized=329.83)],
                   directory=tmp_path)
        got = read_ledger("g", directory=tmp_path)
        assert [e.realized_pnl_usd for e in got] == [329.83]

    def test_a_day_with_no_trades_writes_nothing(self, tmp_path: Path) -> None:
        """So the directory listing is the list of days it actually traded."""
        assert record_day("g", AS_OF, [], directory=tmp_path) is None
        assert read_ledger("g", directory=tmp_path) == []

    def test_a_re_run_of_the_day_corrects_rather_than_appends(
        self, tmp_path: Path
    ) -> None:
        """The morning scan is the run that trades; a second is a fix."""
        record_day("g", AS_OF, [_trade("A", "SELL", realized=10.0)], directory=tmp_path)
        record_day("g", AS_OF, [_trade("A", "SELL", realized=25.0)], directory=tmp_path)
        got = read_ledger("g", directory=tmp_path)
        assert len(got) == 1
        assert got[0].realized_pnl_usd == 25.0

    def test_days_come_back_oldest_first(self, tmp_path: Path) -> None:
        record_day("g", date(2026, 8, 10), [_trade("B", "SELL", realized=2.0)],
                   directory=tmp_path)
        record_day("g", date(2026, 8, 7), [_trade("A", "SELL", realized=1.0)],
                   directory=tmp_path)
        assert [e.ticker for e in read_ledger("g", directory=tmp_path)] == ["A", "B"]

    def test_agents_do_not_mix(self, tmp_path: Path) -> None:
        record_day("g", AS_OF, [_trade("A", "SELL", realized=1.0)], directory=tmp_path)
        record_day("s", AS_OF, [_trade("B", "SELL", realized=9.0)], directory=tmp_path)
        assert [e.ticker for e in read_ledger("g", directory=tmp_path)] == ["A"]

    def test_an_unreadable_day_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        """One bad day costs its attribution, not the whole report."""
        record_day("g", AS_OF, [_trade("A", "SELL", realized=1.0)], directory=tmp_path)
        (tmp_path / "g" / "2026-08-08.json").write_text("{ not json")
        assert len(read_ledger("g", directory=tmp_path)) == 1

    def test_the_file_is_readable_json(self, tmp_path: Path) -> None:
        record_day("g", AS_OF, [_trade("TASK", "SELL", realized=329.83)],
                   directory=tmp_path)
        rows = json.loads((tmp_path / "g" / "2026-08-07.json").read_text())
        assert rows[0]["ticker"] == "TASK"
        assert rows[0]["realized_pnl_usd"] == 329.83

    def test_a_round_trip_preserves_the_entry(self, tmp_path: Path) -> None:
        record_day("g", AS_OF, [_trade("A", "BUY", shares=3.5, price=12.25)],
                   directory=tmp_path)
        e = read_ledger("g", directory=tmp_path)[0]
        assert (e.shares, e.price, e.side) == (3.5, 12.25, "BUY")


class TestRealizedByTicker:
    def test_buys_contribute_nothing(self) -> None:
        entries = [
            LedgerEntry("2026-08-07", "g", "A", "BUY", 1, 1, 1, 0, 0.0),
            LedgerEntry("2026-08-07", "g", "B", "SELL", 1, 1, 1, 0, 50.0),
        ]
        assert realized_by_ticker(entries) == {"B": 50.0}

    def test_several_sales_of_one_name_add_up(self) -> None:
        entries = [
            LedgerEntry("2026-08-07", "g", "A", "SELL", 1, 1, 1, 0, 30.0),
            LedgerEntry("2026-08-10", "g", "A", "SELL", 1, 1, 1, 0, -10.0),
        ]
        assert realized_by_ticker(entries) == {"A": 20.0}

    def test_the_best_comes_first(self) -> None:
        entries = [
            LedgerEntry("2026-08-07", "g", "SMALL", "SELL", 1, 1, 1, 0, 5.0),
            LedgerEntry("2026-08-07", "g", "BIG", "SELL", 1, 1, 1, 0, 500.0),
        ]
        assert list(realized_by_ticker(entries)) == ["BIG", "SMALL"]


def _graham_shaped_book() -> LivePortfolio:
    """A book in Graham's position: open losses, a positive return."""
    p = LivePortfolio(agent="g", cash=0.11)
    p.positions.append(
        # Bought for $15,000, now worth $12,500 — an open loss, on a
        # book whose NAV is still above the $10,000 it started with.
        Position(ticker="HOG", shares=500.0, entry_price=30.0,
                 entry_date="2026-05-06", current_price=25.0,
                 why_en="", why_he="")
    )
    p.initial_cash = 10_000.0
    p.cumulative_dividends = 52.19
    p.cumulative_costs = 132.41
    return p


class TestDecomposeReturn:
    def test_the_parts_add_back_up_to_nav(self) -> None:
        """The property that makes the breakdown checkable by eye."""
        b = decompose_return(_graham_shaped_book())
        total = (
            b.initial_cash + b.realized + b.unrealized + b.dividends - b.costs
        )
        assert total == pytest.approx(b.nav)

    def test_open_losses_and_a_positive_return_coexist(self) -> None:
        """The exact shape that read as a contradiction on the dashboard."""
        b = decompose_return(_graham_shaped_book())
        assert b.unrealized < 0  # the open book is down
        assert b.realized > 0  # and closed trades paid for it

    def test_realized_is_solved_for_not_summed_from_the_ledger(self) -> None:
        """So a ledger missing history cannot make the total wrong.

        Summing the ledger would understate the return by exactly the
        amount of history it lacks, and the parts would stop adding up
        to a NAV the reader can see for themselves.
        """
        book = _graham_shaped_book()
        with_ledger = decompose_return(book, [
            LedgerEntry("2026-08-07", "g", "TASK", "SELL", 1, 1, 1, 0, 100.0)
        ])
        without = decompose_return(book)
        assert with_ledger.realized == pytest.approx(without.realized)

    def test_what_the_ledger_cannot_name_is_reported_not_hidden(self) -> None:
        book = _graham_shaped_book()
        b = decompose_return(book, [
            LedgerEntry("2026-08-07", "g", "TASK", "SELL", 1, 1, 1, 0, 100.0)
        ])
        assert b.attributed == {"TASK": 100.0}
        assert b.unattributed == pytest.approx(b.realized - 100.0)

    def test_with_no_ledger_everything_is_unattributed(self) -> None:
        """A book older than the ledger says so rather than showing zero."""
        b = decompose_return(_graham_shaped_book())
        assert b.attributed == {}
        assert b.unattributed == pytest.approx(b.realized)

    def test_a_fresh_book_decomposes_to_nothing(self) -> None:
        b = decompose_return(LivePortfolio(agent="new", cash=10_000.0))
        assert b.realized == pytest.approx(0.0)
        assert b.unrealized == pytest.approx(0.0)
        assert b.return_pct == pytest.approx(0.0)

    def test_the_percentage_matches_the_book(self) -> None:
        book = _graham_shaped_book()
        assert decompose_return(book).return_pct == pytest.approx(
            book.cumulative_return_pct
        )

    def test_it_serialises(self) -> None:
        d = decompose_return(_graham_shaped_book()).to_dict()
        assert set(d) >= {
            "initial_cash", "realized", "unrealized", "dividends",
            "costs", "nav", "return_pct", "attributed", "unattributed",
        }
