"""Tests for Greenblatt's holding-period rule.

The Little Book is explicit that the formula is a ranking *and* a
discipline for sitting still. None of the discipline was implemented:
the runner sold anything that left today's top thirty, so a name could
be bought on Monday and sold on Thursday for slipping two ranks. The
live book turned over 28% in three trading days.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import ClassVar

import pytest

from agents.greenblatt.exits import (
    DEFAULT_HOLDING_PERIOD_DAYS,
    build_targets,
    is_mature,
    retained,
)
from core.backtest.strategy_runner import HeldPosition

AS_OF = date(2026, 8, 5)


def _held(ticker: str, *, days_ago: int) -> HeldPosition:
    return HeldPosition(
        ticker=ticker,
        shares=10.0,
        entry_price=100.0,
        entry_date=AS_OF - timedelta(days=days_ago),
        current_price=110.0,
    )


def _book(**ages: int) -> dict[str, HeldPosition]:
    return {t: _held(t, days_ago=d) for t, d in ages.items()}


class TestMaturity:
    def test_a_position_inside_the_year_is_immature(self) -> None:
        assert not is_mature(_held("A", days_ago=30), AS_OF, days=365)

    def test_a_position_past_the_year_is_mature(self) -> None:
        assert is_mature(_held("A", days_ago=400), AS_OF, days=365)

    def test_the_anniversary_itself_is_mature(self) -> None:
        assert is_mature(_held("A", days_ago=365), AS_OF, days=365)

    def test_one_day_short_is_not(self) -> None:
        # The tax argument turns on the one-year mark specifically, so
        # selling a day early forfeits it.
        assert not is_mature(_held("A", days_ago=364), AS_OF, days=365)

    def test_the_default_is_a_calendar_year(self) -> None:
        assert DEFAULT_HOLDING_PERIOD_DAYS == 365


class TestRetained:
    def test_only_immature_names_are_protected(self) -> None:
        book = _book(YOUNG=30, OLD=400)

        assert retained(book, AS_OF) == ["YOUNG"]

    def test_ordered_oldest_first(self) -> None:
        book = _book(A=10, B=300, C=100)

        assert retained(book, AS_OF) == ["B", "C", "A"]

    def test_an_empty_book_protects_nothing(self) -> None:
        assert retained({}, AS_OF) == []


class TestBuildTargets:
    RANKED: ClassVar[list[str]] = [f"R{i:02d}" for i in range(1, 41)]

    def test_the_acceptance_case(self) -> None:
        # A 30-day-old position ranked #200 — outside today's list
        # entirely — is not sold. A 400-day-old one is.
        book = _book(YOUNG=30, OLD=400)

        out = build_targets(self.RANKED, book, AS_OF, portfolio_size=30)

        assert "YOUNG" in out
        assert "OLD" not in out

    def test_without_a_book_it_is_the_plain_top_n(self) -> None:
        # A backtest that does not track positions, or a first run.
        out = build_targets(self.RANKED, None, AS_OF, portfolio_size=30)

        assert out == self.RANKED[:30]

    def test_protected_names_do_not_consume_two_slots(self) -> None:
        # A held name that also ranks today appears once.
        book = _book(R01=30)

        out = build_targets(self.RANKED, book, AS_OF, portfolio_size=30)

        assert out.count("R01") == 1
        assert len(out) == 30

    def test_free_slots_are_filled_from_the_top_down(self) -> None:
        book = _book(YOUNG=30)

        out = build_targets(self.RANKED, book, AS_OF, portfolio_size=5)

        assert out[0] == "YOUNG"
        assert out[1:] == self.RANKED[:4]

    def test_more_protected_names_than_slots_keeps_the_oldest(self) -> None:
        # Otherwise the ladder freezes: the newest holdings would be
        # kept and nothing would ever mature out.
        book = _book(A=10, B=300, C=200, D=100)

        out = build_targets(self.RANKED, book, AS_OF, portfolio_size=2)

        assert out == ["B", "C"]

    def test_a_matured_name_keeps_its_slot_if_it_still_ranks(self) -> None:
        # Maturity removes protection; it is not a sell signal.
        book = _book(R01=400)

        out = build_targets(self.RANKED, book, AS_OF, portfolio_size=30)

        assert "R01" in out

    def test_a_matured_name_that_stopped_ranking_is_dropped(self) -> None:
        book = _book(GONE=400)

        out = build_targets(self.RANKED, book, AS_OF, portfolio_size=30)

        assert "GONE" not in out

    def test_the_ladder_replaces_one_position_at_a_time(self) -> None:
        # Entries staggered a month apart mature a month apart, which is
        # the rolling book Greenblatt describes rather than a wholesale
        # annual rebalance.
        # Ages 370, 340, 310, 280, 250 — only the first has passed 365.
        book = _book(**{f"H{i}": 370 - i * 30 for i in range(5)})

        out = build_targets(self.RANKED, book, AS_OF, portfolio_size=5)

        matured = [t for t in book if t not in out]
        assert matured == ["H0"]

    def test_zero_portfolio_size_selects_nothing(self) -> None:
        assert build_targets(self.RANKED, _book(A=10), AS_OF, portfolio_size=0) == []

    def test_a_short_ranking_does_not_pad(self) -> None:
        out = build_targets(["R01", "R02"], None, AS_OF, portfolio_size=30)

        assert out == ["R01", "R02"]

    @pytest.mark.parametrize("days", [90, 180, 730])
    def test_the_period_is_configurable(self, days: int) -> None:
        book = _book(MID=200)

        out = build_targets(
            self.RANKED, book, AS_OF, portfolio_size=30, holding_period_days=days
        )

        assert ("MID" in out) is (days > 200)
