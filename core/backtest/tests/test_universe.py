"""Unit tests for the historical universe module.

The most important test here is the **survivorship-bias** test: when
we ask for the S&P 500 constituents on 2008-09-15 (the day of the
Lehman bankruptcy), Lehman Brothers (LEH) MUST be in the result.

We use a hand-built fixture rather than scraping Wikipedia in tests so
the suite is fast and offline-safe.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.backtest.universe import (
    Change,
    HistoricalUniverse,
    UniverseError,
)


@pytest.fixture
def fixture_universe() -> HistoricalUniverse:
    """A small fixture mimicking the S&P 500 change log.

    Timeline:
        2008-09-15: Lehman (LEH) was a member, gets removed (bankruptcy).
                    Replaced by Fastenal (FAST).
        2008-09-26: WaMu (WM) gets removed. Replaced by IRM.
        2020-12-21: Tesla (TSLA) added; Apartment Investment (AIV) removed.

    Current constituents (2025): AAPL, FAST, IRM, MSFT, NVDA, TSLA.
    """
    current = ["AAPL", "FAST", "IRM", "MSFT", "NVDA", "TSLA"]
    changes = [
        Change(
            effective_date=date(2008, 9, 15),
            added_ticker="FAST",
            removed_ticker="LEH",
        ),
        Change(
            effective_date=date(2008, 9, 26),
            added_ticker="IRM",
            removed_ticker="WM",
        ),
        Change(
            effective_date=date(2020, 12, 21),
            added_ticker="TSLA",
            removed_ticker="AIV",
        ),
    ]
    return HistoricalUniverse(current, changes)


class TestSurvivorshipBias:
    def test_lehman_was_member_before_bankruptcy(
        self, fixture_universe: HistoricalUniverse
    ) -> None:
        # On 2008-09-14 (day before LEH was removed), LEH IS in the universe.
        members = fixture_universe.constituents_at(date(2008, 9, 14))
        assert "LEH" in members, (
            "Lehman Brothers must appear in the historical universe before bankruptcy"
        )
        assert "FAST" not in members, (
            "Fastenal had not yet been added on 2008-09-14"
        )

    def test_lehman_not_member_after_bankruptcy(
        self, fixture_universe: HistoricalUniverse
    ) -> None:
        members = fixture_universe.constituents_at(date(2008, 9, 30))
        assert "LEH" not in members
        assert "FAST" in members

    def test_wamu_was_member_before_collapse(
        self, fixture_universe: HistoricalUniverse
    ) -> None:
        members = fixture_universe.constituents_at(date(2008, 9, 25))
        assert "WM" in members

    def test_wamu_removed_after(self, fixture_universe: HistoricalUniverse) -> None:
        members = fixture_universe.constituents_at(date(2008, 9, 27))
        assert "WM" not in members
        assert "IRM" in members

    def test_tesla_not_in_universe_pre_2020(
        self, fixture_universe: HistoricalUniverse
    ) -> None:
        # Tesla was added 2020-12-21; before that it should NOT be in the universe.
        assert "TSLA" not in fixture_universe.constituents_at(date(2020, 12, 20))
        assert "TSLA" in fixture_universe.constituents_at(date(2020, 12, 22))


class TestConstituentsAt:
    def test_current_returns_current_list(self, fixture_universe: HistoricalUniverse) -> None:
        today = date.today()
        # Use a date well after all changes
        members = fixture_universe.constituents_at(today)
        assert "TSLA" in members
        assert "FAST" in members
        assert "LEH" not in members

    def test_was_member_on_helper(self, fixture_universe: HistoricalUniverse) -> None:
        assert fixture_universe.was_member_on("LEH", date(2008, 9, 1))
        assert not fixture_universe.was_member_on("LEH", date(2009, 1, 1))

    def test_caches_repeated_lookups(self, fixture_universe: HistoricalUniverse) -> None:
        # First call populates the cache
        members1 = fixture_universe.constituents_at(date(2008, 9, 14))
        # Second call should return identical content
        members2 = fixture_universe.constituents_at(date(2008, 9, 14))
        assert members1 == members2

    def test_members_on_dates_batch(self, fixture_universe: HistoricalUniverse) -> None:
        results = fixture_universe.members_on_dates(
            [date(2008, 9, 14), date(2008, 10, 1), date(2021, 1, 1)]
        )
        assert "LEH" in results[date(2008, 9, 14)]
        assert "WM" in results[date(2008, 9, 14)]  # WM removed on 9/26
        assert "WM" not in results[date(2008, 10, 1)]
        assert "TSLA" in results[date(2021, 1, 1)]


class TestPersistence:
    def test_save_and_load_round_trip(
        self, fixture_universe: HistoricalUniverse, tmp_path: Path
    ) -> None:
        fixture_universe.save(tmp_path)
        loaded = HistoricalUniverse.load(tmp_path)
        assert loaded.current == fixture_universe.current
        assert len(loaded.changes) == len(fixture_universe.changes)
        # Verify a key membership query still works post-load
        assert "LEH" in loaded.constituents_at(date(2008, 9, 1))

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(UniverseError):
            HistoricalUniverse.load(tmp_path)


class TestChangeDataclass:
    def test_round_trip_dict(self) -> None:
        c = Change(
            effective_date=date(2020, 1, 15),
            added_ticker="NEW",
            removed_ticker="OLD",
        )
        d = c.to_dict()
        c2 = Change.from_dict(d)
        assert c2 == c

    def test_handles_one_sided_change(self) -> None:
        # Some changes only add or only remove (rare but possible)
        c = Change(
            effective_date=date(2020, 1, 1),
            added_ticker="ADD",
            removed_ticker=None,
        )
        c2 = Change.from_dict(c.to_dict())
        assert c2.removed_ticker is None
