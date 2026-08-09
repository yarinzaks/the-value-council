"""Tests for deciding which tickers have a hole worth refilling.

Getting this wrong is expensive in both directions. Too strict and a
sweep leaves AAPL missing eight years, which is how a momentum strategy
ends up designed on a market with no winners in it. Too loose and every
ticker gets refetched, which is hours of vendor calls to rediscover that
nothing was wrong.
"""

from __future__ import annotations

from scripts.backfill_price_gap import MIN_BARS_PER_FULL_YEAR, holed_tickers

FULL = 252


def test_a_complete_series_has_no_hole() -> None:
    bars = {"AAA": {2015: FULL, 2016: FULL, 2017: FULL}}
    assert holed_tickers(bars) == []


def test_a_blank_interior_year_is_a_hole() -> None:
    bars = {"AAA": {2015: FULL, 2016: 0, 2017: FULL}}
    assert holed_tickers(bars) == ["AAA"]


def test_a_missing_interior_year_is_a_hole() -> None:
    # Absent from the mapping entirely, which is what the query returns
    # for a year with no rows at all.
    bars = {"AAA": {2015: FULL, 2017: FULL}}
    assert holed_tickers(bars) == ["AAA"]


def test_a_thin_interior_year_is_a_hole() -> None:
    # Six months of bars still prices every rebalance in the months it
    # kept, so it never looks absent — it just stops being measurable.
    bars = {"AAA": {2015: FULL, 2016: 120, 2017: FULL}}
    assert holed_tickers(bars) == ["AAA"]


def test_a_year_just_over_the_threshold_is_kept() -> None:
    bars = {"AAA": {2015: FULL, 2016: MIN_BARS_PER_FULL_YEAR, 2017: FULL}}
    assert holed_tickers(bars) == []


def test_a_partial_first_year_is_a_listing_not_a_hole() -> None:
    # A company that listed in October has ~60 bars that year, and
    # refetching will not produce more.
    bars = {"AAA": {2015: 60, 2016: FULL, 2017: FULL}}
    assert holed_tickers(bars) == []


def test_a_partial_last_year_is_a_delisting_or_the_present() -> None:
    bars = {"AAA": {2015: FULL, 2016: FULL, 2017: 40}}
    assert holed_tickers(bars) == []


def test_a_single_year_of_history_is_not_a_hole() -> None:
    assert holed_tickers({"AAA": {2016: 40}}) == []


def test_an_empty_mapping_is_not_a_hole() -> None:
    assert holed_tickers({}) == []


def test_several_tickers_are_returned_sorted() -> None:
    bars = {
        "ZZZ": {2015: FULL, 2016: 0, 2017: FULL},
        "AAA": {2015: FULL, 2016: 0, 2017: FULL},
        "MMM": {2015: FULL, 2016: FULL, 2017: FULL},
    }
    assert holed_tickers(bars) == ["AAA", "ZZZ"]


def test_a_multi_year_blackout_is_caught() -> None:
    # The real shape of the damage: AAPL held 2010 and then nothing
    # until 2019.
    bars = {"AAPL": {2010: FULL, **{y: 0 for y in range(2011, 2019)}, 2019: FULL}}
    assert holed_tickers(bars) == ["AAPL"]
