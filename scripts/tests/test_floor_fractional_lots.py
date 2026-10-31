"""What flooring the books to whole shares must and must not do."""

from __future__ import annotations

import pytest

from core.live.portfolio import LivePortfolio, Position
from scripts.floor_fractional_lots import floor_one


def _pos(ticker: str, shares: float, mark: float, entry: float | None = None) -> Position:
    return Position(
        ticker=ticker,
        shares=shares,
        entry_price=entry if entry is not None else mark,
        entry_date="2026-05-06",
        current_price=mark,
    )


def _book(positions: list[Position], cash: float = 0.0) -> LivePortfolio:
    book = LivePortfolio(agent="test", cash=cash)
    book.positions = positions
    book.mark_to_market({p.ticker: p.current_price for p in positions})
    return book


class TestTheValueIsConserved:
    """The whole point: this reclassifies, it does not realise."""

    def test_nav_does_not_move(self) -> None:
        book = _book([_pos("A", 74.375194, 25.95), _pos("B", 0.409917, 370.33)])
        before = book.total_nav

        floor_one(book)
        book.mark_to_market({p.ticker: p.current_price for p in book.positions})

        assert book.total_nav == pytest.approx(before, abs=0.01)

    def test_the_fraction_arrives_in_cash_at_its_own_mark(self) -> None:
        book = _book([_pos("A", 10.25, 40.0)], cash=500.0)

        freed, changes = floor_one(book)

        # 0.25 of a share at $40 is $10.
        assert freed == pytest.approx(10.0)
        assert book.cash == pytest.approx(510.0)
        assert book.positions[0].shares == 10.0
        assert changes == [("A", 10.25, 10.0, pytest.approx(10.0))]

    def test_a_whole_lot_is_not_touched(self) -> None:
        book = _book([_pos("A", 61.0, 31.22)], cash=1.0)

        freed, changes = floor_one(book)

        assert freed == 0.0
        assert changes == []
        assert book.cash == 1.0
        assert book.positions[0].shares == 61.0


class TestALotUnderOneShareCloses:
    def test_it_leaves_the_book_entirely(self) -> None:
        """0.007184 shares of Lam Research is $2 and the dashboard
        already renders it as "0 shares". It is not a position."""
        book = _book([_pos("LRCX", 0.007184, 239.11), _pos("KEEP", 5.0, 10.0)])

        freed, _ = floor_one(book)

        assert [p.ticker for p in book.positions] == ["KEEP"]
        assert freed == pytest.approx(0.007184 * 239.11)
        assert book.cash == pytest.approx(0.007184 * 239.11, abs=0.01)

    def test_closing_it_still_moves_no_nav(self) -> None:
        book = _book([_pos("LRCX", 0.268120, 1417.54)])
        before = book.total_nav

        floor_one(book)
        book.mark_to_market({})

        assert not book.positions
        assert book.total_nav == pytest.approx(before, abs=0.01)


class TestItRefusesWhatItCannotValue:
    def test_a_lot_with_no_mark_is_left_fractional(self) -> None:
        """A missing mark gives the fraction no honest value, and the
        runner refuses to sell at a stale one for the same reason. The
        lot waits for a price rather than being valued at zero.

        Built without marking, because `mark_to_market` falls back to
        the entry price precisely so a transient price failure cannot
        zero a position — which means this state only reaches
        `floor_one` from a portfolio file written with it.
        """
        book = LivePortfolio(agent="test", cash=0.0)
        book.positions = [_pos("DEAD", 3.5, 0.0, entry=20.0)]

        freed, changes = floor_one(book)

        assert freed == 0.0
        assert changes == []
        assert book.positions[0].shares == 3.5


class TestTheRecordedReturnIsNotRestated:
    def test_cumulative_return_is_unchanged(self) -> None:
        """It is computed from NAV against the initial cash, and NAV
        does not move — so a reclassification cannot restate it. The
        assertion is here because it is the invariant that makes this
        script safe to run on books with five months of history."""
        book = _book([_pos("A", 10.5, 40.0, entry=20.0)], cash=9_580.0)
        before = book.cumulative_return_pct

        floor_one(book)
        book.mark_to_market({})

        assert book.cumulative_return_pct == pytest.approx(before, abs=1e-6)

    def test_nothing_is_added_to_cumulative_costs(self) -> None:
        """There is no counterparty and no commission. Charging one
        would take real money out of the book for a trade nobody made."""
        book = _book([_pos("A", 10.5, 40.0, entry=20.0)])
        before = book.cumulative_costs

        floor_one(book)

        assert book.cumulative_costs == before

    def test_the_cost_basis_of_what_remains_is_unchanged(self) -> None:
        book = _book([_pos("A", 10.5, 40.0, entry=20.0)])

        floor_one(book)

        assert book.positions[0].entry_price == 20.0
        assert book.positions[0].entry_date == "2026-05-06"


class TestAnEmptyBookIsNotAnError:
    def test_no_positions_frees_nothing(self) -> None:
        book = _book([], cash=10_000.0)

        freed, changes = floor_one(book)

        assert freed == 0.0
        assert changes == []
        assert book.cash == 10_000.0
