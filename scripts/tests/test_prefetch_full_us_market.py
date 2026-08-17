"""Tests for the cache-refresh eligibility rule.

The rule used to be ``not cache.has_ticker(ticker)`` — pure existence.
Once a ticker had been fetched once it was never fetched again, so the
weekly job only ever added newly-listed companies. Every one of the
8,290 cached files carried the same April 28 mtime when this was found,
against an August 5 run date: the agents had been screening on 99-day-
old fundamentals while a green workflow republished the same tarball
every week.
"""

from __future__ import annotations

import collections
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import ClassVar

from core.data.edgar_cache import EdgarCache
from core.data.fiscal_calendar import FiscalProfile
from scripts.prefetch_full_us_market import (
    DEFAULT_MAX_AGE_DAYS,
    cache_age_days,
    is_due,
    shard_of,
)


def _cached(cache: EdgarCache, ticker: str, *, age_days: float) -> Path:
    """Create a cache file for ``ticker`` aged ``age_days``."""
    path = cache.path_for(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"parquet-ish")
    when = time.time() - age_days * 86_400.0
    os.utime(path, (when, when))
    return path


class TestCacheAge:
    def test_missing_file_has_no_age(self, tmp_path: Path) -> None:
        assert cache_age_days(EdgarCache(cache_dir=tmp_path), "NOPE") is None

    def test_age_reflects_mtime(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        _cached(cache, "AAPL", age_days=30.0)

        age = cache_age_days(cache, "AAPL")

        assert age is not None
        assert 29.9 < age < 30.1


def _profile_due_on(day: date) -> FiscalProfile:
    """A profile whose expected filing date has already arrived."""
    return FiscalProfile(
        ticker="AAPL",
        fiscal_year_end=(12, 31),
        median_lag_days={"10-Q": 35, "10-K": 60},
        last_period_end={"10-Q": day - timedelta(days=120), "10-K": date(2025, 12, 31)},
    )


def _profile_not_due_on(day: date) -> FiscalProfile:
    """A profile whose next filing is still months away."""
    return FiscalProfile(
        ticker="AAPL",
        fiscal_year_end=(12, 31),
        median_lag_days={"10-Q": 35, "10-K": 60},
        last_period_end={
            "10-Q": date(day.year, 6, 30),
            "10-K": date(day.year, 12, 31),
        },
    )


class TestIsDue:
    def test_uncached_ticker_is_due(self, tmp_path: Path) -> None:
        assert is_due(EdgarCache(cache_dir=tmp_path), "AAPL")

    def test_fresh_ticker_is_not_due(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        _cached(cache, "AAPL", age_days=10.0)

        assert not is_due(cache, "AAPL", max_age_days=21)

    def test_stale_ticker_is_due(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        _cached(cache, "AAPL", age_days=120.0)

        assert is_due(cache, "AAPL", max_age_days=21)

    def test_the_calendar_can_add_a_fresh_file_to_the_fetch(
        self, tmp_path: Path
    ) -> None:
        """A filing landed inside the age window, so re-read it now.

        Without this the file waits out --max-age-days and the agents
        screen a company on the quarter before the one it just reported.
        """
        cache = EdgarCache(cache_dir=tmp_path)
        _cached(cache, "AAPL", age_days=2.0)
        calendar = {"AAPL": _profile_due_on(date(2026, 8, 18))}

        assert is_due(
            cache,
            "AAPL",
            max_age_days=21,
            calendar=calendar,
            as_of=date(2026, 8, 18),
        )

    def test_the_calendar_never_removes_a_stale_file_from_the_fetch(
        self, tmp_path: Path
    ) -> None:
        """The invariant. It may make the refresh timelier, not thinner.

        A calendar that could veto would turn every modelling error into
        a company silently frozen out of the corpus — the exact failure
        the age rule was introduced to end.
        """
        cache = EdgarCache(cache_dir=tmp_path)
        _cached(cache, "AAPL", age_days=120.0)
        calendar = {"AAPL": _profile_not_due_on(date(2026, 8, 18))}

        assert is_due(
            cache,
            "AAPL",
            max_age_days=21,
            calendar=calendar,
            as_of=date(2026, 8, 18),
        )

    def test_a_ticker_the_calendar_never_heard_of_keeps_the_age_answer(
        self, tmp_path: Path
    ) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        _cached(cache, "AAPL", age_days=2.0)

        assert not is_due(
            cache, "AAPL", max_age_days=21, calendar={}, as_of=date(2026, 8, 18)
        )

    def test_no_calendar_behaves_exactly_as_before(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        _cached(cache, "AAPL", age_days=2.0)

        assert not is_due(cache, "AAPL", max_age_days=21, calendar=None)

    def test_the_ninety_nine_day_case(self, tmp_path: Path) -> None:
        # The exact condition found in production: cached, and therefore
        # skipped forever by the old existence test.
        cache = EdgarCache(cache_dir=tmp_path)
        _cached(cache, "AAPL", age_days=99.0)

        assert cache.has_ticker("AAPL")  # the old rule said "skip"
        assert is_due(cache, "AAPL")  # the new rule says "fetch"

    def test_default_bound_is_shorter_than_the_shard_rotation(self) -> None:
        # Four shards on a weekly cron revisit each ticker every 28 days.
        # The age bound has to be shorter or a revisit finds nothing due.
        assert DEFAULT_MAX_AGE_DAYS < 28


class TestSharding:
    TICKERS: ClassVar[tuple[str, ...]] = tuple(f"T{i:04d}" for i in range(4_000))

    def test_shard_is_stable_across_calls(self) -> None:
        assert shard_of("AAPL", 4) == shard_of("AAPL", 4)

    def test_every_ticker_lands_in_range(self) -> None:
        assert all(0 <= shard_of(t, 4) < 4 for t in self.TICKERS)

    def test_shards_partition_the_universe(self) -> None:
        # Each ticker belongs to exactly one shard, so the union over
        # shards is the whole universe with no duplicates.
        seen: list[str] = []
        for s in range(4):
            seen.extend(t for t in self.TICKERS if shard_of(t, 4) == s)
        assert sorted(seen) == sorted(self.TICKERS)

    def test_shards_are_roughly_balanced(self) -> None:
        counts = collections.Counter(shard_of(t, 4) for t in self.TICKERS)
        expected = len(self.TICKERS) / 4
        assert all(0.85 * expected < n < 1.15 * expected for n in counts.values())

    def test_not_sliced_alphabetically(self) -> None:
        # An alphabetical split would put every A-name in one run and
        # skew any partial refresh toward one end of the market.
        a_names = ["AAPL", "AAL", "AAP", "ABBV", "ABT", "ACN", "ADBE", "ADI"]
        assert len({shard_of(t, 4) for t in a_names}) > 1
