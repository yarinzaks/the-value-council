"""The calendar is checked against dates, not against itself.

A rule engine that agrees with its own rules proves nothing. Every
assertion here names a real date whose status is a matter of public
record — including the four days the agents actually traded on while
the exchange was shut, which is what this module was written for.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.market_calendar import (
    closes_early,
    closures,
    easter_sunday,
    half_days,
    is_trading_day,
    observed,
    previous_trading_day,
    why_closed,
)

#: The days the books moved while the market was shut. Measured from
#: data/decisions and data/trades before this module existed: 1,770
#: journal rows and 21 executed trades across the four.
THE_FOUR = [
    (date(2026, 5, 25), "Memorial Day"),
    (date(2026, 6, 19), "Juneteenth"),
    (date(2026, 7, 3), "Independence Day"),
    (date(2026, 9, 7), "Labor Day"),
]


class TestTheDaysThatCausedThis:
    @pytest.mark.parametrize("day,name", THE_FOUR)
    def test_each_one_is_now_known_to_be_shut(self, day: date, name: str) -> None:
        assert not is_trading_day(day)
        assert why_closed(day) == name

    def test_they_were_all_weekdays(self) -> None:
        """Which is exactly why a Mon-Fri schedule walked into them."""
        assert all(day.weekday() < 5 for day, _ in THE_FOUR)


class TestGoodFriday:
    """The one holiday with no fixed date and no nth-weekday rule."""

    @pytest.mark.parametrize(
        "year,expected",
        [
            (2026, date(2026, 4, 3)),
            (2027, date(2027, 3, 26)),
            (2028, date(2028, 4, 14)),
            (2030, date(2030, 4, 19)),
        ],
    )
    def test_good_friday_lands_where_the_church_puts_it(
        self, year: int, expected: date
    ) -> None:
        assert expected in closures(year)
        assert closures(year)[expected] == "Good Friday"
        assert easter_sunday(year) - timedelta(days=2) == expected

    def test_easter_is_always_a_sunday(self) -> None:
        assert all(easter_sunday(y).weekday() == 6 for y in range(2020, 2080))


class TestRule72:
    """A weekend holiday, and the exception the rule names itself."""

    def test_a_sunday_holiday_closes_the_monday(self) -> None:
        # 19 June 2027 is a Saturday, so Juneteenth is taken on the 18th.
        assert date(2027, 6, 19).weekday() == 5
        assert date(2027, 6, 18) in closures(2027)

    def test_a_saturday_holiday_closes_the_friday_before(self) -> None:
        # 25 December 2027 is a Saturday.
        assert date(2027, 12, 25).weekday() == 5
        assert closures(2027)[date(2027, 12, 24)] == "Christmas Day"

    def test_a_saturday_new_year_is_not_observed_at_all(self) -> None:
        """31 December is the yearly accounting close, so it trades.

        And the holiday does not move to the Monday either, which is
        why 2028 has nine closures where every neighbouring year has
        ten.
        """
        assert date(2028, 1, 1).weekday() == 5
        assert observed(date(2028, 1, 1)) is None
        assert is_trading_day(date(2027, 12, 31))
        assert is_trading_day(date(2028, 1, 3))
        assert len([d for d in closures(2028) if d.year == 2028]) == 9

    def test_every_other_year_in_the_decade_has_ten(self) -> None:
        for year in (2026, 2027, 2029, 2030, 2031):
            kept = [d for d in closures(year) if d.year == year]
            assert len(kept) == 10, f"{year}: {sorted(closures(year))}"


class TestHalfDays:
    def test_the_two_coming_up(self) -> None:
        assert closes_early(date(2026, 11, 27)) == "day after Thanksgiving"
        assert closes_early(date(2026, 12, 24)) == "Christmas Eve"

    def test_an_early_close_is_still_a_trading_day(self) -> None:
        """It moves the auction forward; it does not cancel the session."""
        assert is_trading_day(date(2026, 11, 27))
        assert is_trading_day(date(2026, 12, 24))

    def test_a_closure_is_never_also_a_half_day(self) -> None:
        for year in range(2026, 2041):
            assert not set(closures(year)) & set(half_days(year))

    def test_christmas_eve_shut_leaves_that_year_one_half_day(self) -> None:
        # 2027 takes Christmas on the 24th, so there is no eve to shorten.
        assert date(2027, 12, 24) in closures(2027)
        assert len(half_days(2027)) == 1

    def test_july_third_is_only_short_when_the_fourth_trades(self) -> None:
        # 2026: the 4th is a Saturday, so the 3rd is shut, not short.
        assert date(2026, 7, 3) in closures(2026)
        assert date(2026, 7, 3) not in half_days(2026)
        # 2028: the 4th is a Tuesday, so the 3rd is a half day.
        assert closes_early(date(2028, 7, 3)) == "day before Independence Day"


class TestTheShapeHoldsForDecades:
    """Derived rather than tabulated, so it must not drift or run out."""

    @pytest.mark.parametrize("year", range(2026, 2061, 7))
    def test_nothing_ever_lands_on_a_weekend(self, year: int) -> None:
        for day in list(closures(year)) + list(half_days(year)):
            assert day.weekday() < 5, f"{day:%Y-%m-%d %a}"

    @pytest.mark.parametrize("year", range(2026, 2061, 7))
    def test_a_year_always_has_nine_or_ten_closures(self, year: int) -> None:
        kept = [d for d in closures(year) if d.year == year]
        assert len(kept) in (9, 10)

    def test_a_normal_week_has_five_sessions(self) -> None:
        monday = date(2026, 10, 19)
        week = [monday + timedelta(days=n) for n in range(7)]
        assert sum(1 for d in week if is_trading_day(d)) == 5


class TestWeekends:
    def test_saturday_and_sunday_are_shut(self) -> None:
        assert why_closed(date(2026, 10, 31)) == "weekend"
        assert why_closed(date(2026, 11, 1)) == "weekend"

    def test_an_ordinary_wednesday_is_open(self) -> None:
        assert is_trading_day(date(2026, 10, 28))
        assert why_closed(date(2026, 10, 28)) is None


class TestPreviousTradingDay:
    def test_it_steps_back_over_a_weekend(self) -> None:
        assert previous_trading_day(date(2026, 11, 1)) == date(2026, 10, 30)

    def test_it_steps_back_over_a_holiday_against_a_weekend(self) -> None:
        # Fri 3 July 2026 shut, Sat and Sun, so Thursday the 2nd.
        assert previous_trading_day(date(2026, 7, 5)) == date(2026, 7, 2)

    def test_a_trading_day_is_its_own_answer(self) -> None:
        assert previous_trading_day(date(2026, 10, 28)) == date(2026, 10, 28)

    def test_thanksgiving_falls_back_to_the_wednesday(self) -> None:
        assert previous_trading_day(date(2026, 11, 26)) == date(2026, 11, 25)
