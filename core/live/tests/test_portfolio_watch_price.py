"""The quotation a watch was recorded at survives a save and a load.

The dashboard's "distance from trigger" column is a subtraction: the
price the screen saw, minus the price it wants. It had neither for as
long as the entry carried only the target, and both branches of the
column returned a dash.

The field is optional on the way in because every watchlist written
before it existed lacks it, and every agent whose trigger is not a price
leaves it empty.
"""

from __future__ import annotations

import pytest

from core.live.portfolio import LivePortfolio, WatchEntry


def an_entry(**kw: object) -> WatchEntry:
    base: dict[str, object] = {
        "ticker": "MGEE",
        "identified_date": "2026-10-30",
        "current_rank": 1,
        "entry_trigger": "Defensive thresholds met",
        "entry_price_target": 74.20,
        "last_price": 91.00,
        "why_en": "blocked on P/E",
        "why_he": "נחסם על P/E",
    }
    base.update(kw)
    return WatchEntry(**base)  # type: ignore[arg-type]


def round_trip(entry: WatchEntry) -> WatchEntry:
    book = LivePortfolio(agent="benjamin_graham", initial_cash=10_000.0)
    book.set_watchlist([entry])
    return LivePortfolio.from_dict(book.to_dict()).watchlist[0]


def test_the_price_the_screen_saw_survives_the_file():
    assert round_trip(an_entry()).last_price == pytest.approx(91.00)


def test_the_target_still_survives_beside_it():
    restored = round_trip(an_entry())

    assert restored.entry_price_target == pytest.approx(74.20)
    assert restored.last_price > restored.entry_price_target


def test_an_entry_written_before_the_field_existed_still_loads():
    book = LivePortfolio(agent="benjamin_graham", initial_cash=10_000.0)
    book.set_watchlist([an_entry()])
    raw = book.to_dict()
    del raw["watchlist"][0]["last_price"]

    assert LivePortfolio.from_dict(raw).watchlist[0].last_price is None


def test_an_agent_whose_trigger_is_not_a_price_leaves_both_empty():
    restored = round_trip(an_entry(entry_price_target=None, last_price=None))

    assert restored.entry_price_target is None
    assert restored.last_price is None


def test_the_file_carries_the_field_by_name():
    book = LivePortfolio(agent="benjamin_graham", initial_cash=10_000.0)
    book.set_watchlist([an_entry()])

    assert "last_price" in book.to_dict()["watchlist"][0]
