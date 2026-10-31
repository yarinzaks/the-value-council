"""Turning the dataframe cache off, and the trap in asking for it.

The in-memory LRU exists for the backtest, which reads each ticker once
per rebalance and so reuses almost every parse. The fiscal-calendar
build is the opposite caller: one pass over the whole market, each
ticker read exactly once, so a retained dataframe can never pay for
itself. The default size is larger than the number of tickers, so
nothing was ever evicted — measured over the full 8,290-ticker cache,
peak RSS was 2,353 MB against 604 MB with the cache off, for the same
6,529 profiles in the same 77 seconds.

Zero was the natural way to ask for that and the one way to crash: the
eviction loop's condition holds against an empty list, so it popped
from one.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.data.edgar_cache import EdgarCache


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    frame = pd.DataFrame(
        {
            "concept": ["Assets", "Revenues"],
            "value": [1.0, 2.0],
            "period_end": ["2026-06-30", "2026-06-30"],
            "filed": ["2026-07-30", "2026-07-30"],
            "form": ["10-Q", "10-Q"],
        }
    )
    for ticker in ("AAA", "BBB", "CCC"):
        frame.to_parquet(tmp_path / f"{ticker}.parquet")
    return tmp_path


class TestTurningTheCacheOff:
    def test_zero_does_not_raise(self, cache_dir: Path) -> None:
        """It used to pop from an empty list on the very first read."""
        cache = EdgarCache(cache_dir, memory_cache_size=0)

        assert len(cache.load_dataframe("AAA")) == 2

    def test_zero_retains_nothing_across_reads(self, cache_dir: Path) -> None:
        cache = EdgarCache(cache_dir, memory_cache_size=0)
        for ticker in ("AAA", "BBB", "CCC"):
            cache.load_dataframe(ticker)

        assert cache._df_cache == {}
        assert cache._df_cache_order == []

    def test_a_negative_size_is_treated_as_off(self, cache_dir: Path) -> None:
        cache = EdgarCache(cache_dir, memory_cache_size=-1)
        cache.load_dataframe("AAA")

        assert cache._df_cache == {}

    def test_the_data_is_the_same_either_way(self, cache_dir: Path) -> None:
        """Off is a memory decision, never a correctness one."""
        on = EdgarCache(cache_dir, memory_cache_size=16).load_dataframe("BBB")
        off = EdgarCache(cache_dir, memory_cache_size=0).load_dataframe("BBB")

        pd.testing.assert_frame_equal(on, off)


class TestTheCacheStillCaches:
    """The default caller must not lose the optimisation."""

    def test_a_second_read_comes_from_memory(self, cache_dir: Path) -> None:
        cache = EdgarCache(cache_dir, memory_cache_size=16)
        first = cache.load_dataframe("AAA")

        assert cache.load_dataframe("AAA") is first

    def test_it_evicts_once_full(self, cache_dir: Path) -> None:
        cache = EdgarCache(cache_dir, memory_cache_size=2)
        for ticker in ("AAA", "BBB", "CCC"):
            cache.load_dataframe(ticker)

        assert len(cache._df_cache) == 2
        assert "AAA" not in cache._df_cache, "the oldest should have gone"

    def test_clearing_frees_everything(self, cache_dir: Path) -> None:
        cache = EdgarCache(cache_dir, memory_cache_size=16)
        cache.load_dataframe("AAA")
        cache.clear_memory_cache()

        assert cache._df_cache == {}
