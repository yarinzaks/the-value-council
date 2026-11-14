"""Unit tests for the price data loader.

We test the cache behavior and SQL schema using a temporary SQLite
file. Live yfinance fetches are tested separately with the
``@pytest.mark.integration`` marker so the offline suite remains fast.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import ClassVar

import pandas as pd
import pytest

from core.backtest.data_loader import (
    MAX_CARRY_FORWARD_DAYS,
    NO_DATA_TTL_DAYS,
    REFRESH_WINDOW_DAYS,
    PriceDataLoader,
    _to_date,
)


class TestCacheRoundTrip:
    def test_initialize_creates_schema(self, tmp_path: Path) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        assert (tmp_path / "prices.sqlite").exists()
        # Cache should be empty for an unknown ticker
        assert loader.cached_range("AAPL") is None

    def test_write_then_read(self, tmp_path: Path) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        # Construct a small fake DataFrame in yfinance format
        idx = pd.DatetimeIndex(["2020-01-02", "2020-01-03", "2020-01-06"])
        df = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0],
                "High": [101.0, 102.0, 103.0],
                "Low": [99.0, 100.0, 101.0],
                "Close": [100.5, 101.5, 102.5],
                "Adj Close": [100.5, 101.5, 102.5],
                "Volume": [1000, 1100, 1200],
            },
            index=idx,
        )
        n = loader._write_cache("AAPL", df)
        assert n == 3
        cached = loader._read_cached("AAPL", date(2020, 1, 1), date(2020, 1, 31))
        assert len(cached) == 3
        assert "adj_close" in cached.columns

    def test_cached_range_returns_min_max(self, tmp_path: Path) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        idx = pd.DatetimeIndex(["2020-01-02", "2020-01-15", "2020-01-31"])
        df = pd.DataFrame(
            {
                "Open": [100, 101, 102],
                "High": [101, 102, 103],
                "Low": [99, 100, 101],
                "Close": [100.5, 101.5, 102.5],
                "Adj Close": [100.5, 101.5, 102.5],
                "Volume": [1000, 1100, 1200],
            },
            index=idx,
        )
        loader._write_cache("AAPL", df)
        rng = loader.cached_range("AAPL")
        assert rng == (date(2020, 1, 2), date(2020, 1, 31))


class TestDateHelpers:
    def test_to_date_handles_iso_string(self) -> None:
        assert _to_date("2020-01-15") == date(2020, 1, 15)

    def test_to_date_handles_datetime(self) -> None:
        from datetime import datetime

        assert _to_date(datetime(2020, 1, 15, 10, 30)) == date(2020, 1, 15)

    def test_to_date_handles_date(self) -> None:
        d = date(2020, 1, 15)
        assert _to_date(d) is d


class TestGetHistoryValidation:
    def test_start_after_end_raises(self, tmp_path: Path) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        with pytest.raises(ValueError, match="must be <="):
            loader.get_history("AAPL", date(2020, 12, 31), date(2020, 1, 1))


class TestGetPriceOn:
    def test_returns_none_on_empty_cache(self, tmp_path: Path, monkeypatch) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        # Patch the yfinance fetch to return empty
        monkeypatch.setattr(
            loader,
            "_fetch_yfinance",
            lambda ticker, start, end: pd.DataFrame(),
        )
        assert loader.get_price_on("UNKNOWN", date(2020, 6, 15)) is None

    def test_returns_close_at_or_before(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        idx = pd.DatetimeIndex(["2020-01-02", "2020-01-15", "2020-01-31"])
        df = pd.DataFrame(
            {
                "Open": [100, 101, 102],
                "High": [101, 102, 103],
                "Low": [99, 100, 101],
                "Close": [100.5, 101.5, 102.5],
                "Adj Close": [100.5, 101.5, 102.5],
                "Volume": [1000, 1100, 1200],
            },
            index=idx,
        )
        loader._write_cache("AAPL", df)
        # Stub yfinance to ensure the cache is the only data source
        monkeypatch.setattr(
            loader,
            "_fetch_yfinance",
            lambda ticker, start, end: pd.DataFrame(),
        )
        # Querying mid-month should get the Jan 15 close (latest <= Jan 20)
        price = loader.get_price_on("AAPL", date(2020, 1, 20))
        assert price == pytest.approx(101.5)


class TestGetPriceOnForceRefresh:
    """The close-of-day mark must re-read the settled close.

    The morning run caches an intraday quote under today's date. Without
    force_refresh the fast path hands that same number back at 16:30 ET,
    so the entire NAV history becomes intraday prices labelled as closes.
    """

    @staticmethod
    def _bar(day: str, price: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Open": [price],
                "High": [price],
                "Low": [price],
                "Close": [price],
                "Adj Close": [price],
                "Volume": [1000],
            },
            index=pd.DatetimeIndex([day]),
        )

    def test_default_returns_the_cached_intraday_bar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        loader._write_cache("AAPL", self._bar("2026-08-05", 100.0))
        monkeypatch.setattr(
            loader,
            "_fetch_yfinance",
            lambda ticker, start, end: self._bar("2026-08-05", 107.5),
        )

        assert loader.get_price_on("AAPL", date(2026, 8, 5)) == pytest.approx(100.0)

    def test_force_refresh_returns_the_settled_close(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        loader._write_cache("AAPL", self._bar("2026-08-05", 100.0))
        monkeypatch.setattr(
            loader,
            "_fetch_yfinance",
            lambda ticker, start, end: self._bar("2026-08-05", 107.5),
        )

        price = loader.get_price_on("AAPL", date(2026, 8, 5), force_refresh=True)

        assert price == pytest.approx(107.5)

    def test_force_refresh_overwrites_the_cached_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The corrected close must persist, so a later cached read agrees.
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        loader._write_cache("AAPL", self._bar("2026-08-05", 100.0))
        monkeypatch.setattr(
            loader,
            "_fetch_yfinance",
            lambda ticker, start, end: self._bar("2026-08-05", 107.5),
        )
        loader.get_price_on("AAPL", date(2026, 8, 5), force_refresh=True)

        monkeypatch.setattr(
            loader,
            "_fetch_yfinance",
            lambda ticker, start, end: pd.DataFrame(),
        )
        assert loader.get_price_on("AAPL", date(2026, 8, 5)) == pytest.approx(107.5)

    def test_force_refresh_fetches_a_short_window_not_a_year(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        seen: list[tuple[date, date]] = []

        def _spy(ticker: str, start: date, end: date) -> pd.DataFrame:
            seen.append((start, end))
            return self._bar("2026-08-05", 107.5)

        monkeypatch.setattr(loader, "_fetch_yfinance", _spy)
        loader.get_price_on("AAPL", date(2026, 8, 5), force_refresh=True)

        assert seen == [
            (date(2026, 8, 5) - timedelta(days=REFRESH_WINDOW_DAYS), date(2026, 8, 5))
        ]

    def test_force_refresh_with_no_data_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        monkeypatch.setattr(
            loader,
            "_fetch_yfinance",
            lambda ticker, start, end: pd.DataFrame(),
        )

        assert (
            loader.get_price_on("DEAD", date(2026, 8, 5), force_refresh=True) is None
        )

    def test_force_refresh_falls_back_to_the_prior_bar_on_a_holiday(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 2026-01-19 is MLK Day. The refresh window must still contain the
        # Friday bar so the mark does not come back None.
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        monkeypatch.setattr(
            loader,
            "_fetch_yfinance",
            lambda ticker, start, end: self._bar("2026-01-16", 99.25),
        )

        price = loader.get_price_on("AAPL", date(2026, 1, 19), force_refresh=True)

        assert price == pytest.approx(99.25)


class TestDividends:
    """actions=False meant cash dividends were invisible to the whole
    system, so NAV tracked price return only."""

    @staticmethod
    def _frame() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Open": [100.0, 101.0, 99.0],
                "High": [101.0, 102.0, 100.0],
                "Low": [99.0, 100.0, 98.0],
                "Close": [100.0, 101.0, 99.0],
                "Adj Close": [100.0, 101.0, 99.0],
                "Volume": [1000, 1100, 1200],
                "Dividends": [0.0, 0.55, 0.0],
            },
            index=pd.DatetimeIndex(["2026-08-03", "2026-08-04", "2026-08-05"]),
        )

    def _loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> PriceDataLoader:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        monkeypatch.setattr(
            loader, "_fetch_yfinance", lambda ticker, start, end: self._frame()
        )
        loader.get_history("KO", date(2026, 8, 3), date(2026, 8, 5))
        return loader

    def test_a_dividend_is_persisted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = self._loaded(tmp_path, monkeypatch)

        paid = loader.dividends_between("KO", date(2026, 8, 1), date(2026, 8, 5))

        assert paid == [(date(2026, 8, 4), pytest.approx(0.55))]

    def test_zero_rows_are_not_stored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every trading day carries a Dividends column; only the ex-date
        # is non-zero.
        loader = self._loaded(tmp_path, monkeypatch)

        assert loader.dividends_between("KO", date(2026, 8, 1), date(2026, 8, 3)) == []

    def test_the_lower_bound_is_exclusive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # So a caller can pass the last date it settled without paying
        # the same dividend twice.
        loader = self._loaded(tmp_path, monkeypatch)

        assert loader.dividends_between("KO", date(2026, 8, 4), date(2026, 8, 5)) == []

    def test_the_upper_bound_is_inclusive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = self._loaded(tmp_path, monkeypatch)

        paid = loader.dividends_between("KO", date(2026, 8, 3), date(2026, 8, 4))

        assert len(paid) == 1

    def test_refetching_does_not_duplicate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = self._loaded(tmp_path, monkeypatch)
        loader.get_history(
            "KO", date(2026, 8, 3), date(2026, 8, 5), force_refresh=True
        )

        paid = loader.dividends_between("KO", date(2026, 8, 1), date(2026, 8, 5))

        assert len(paid) == 1

    def test_a_frame_without_the_column_is_tolerated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        frame = self._frame().drop(columns=["Dividends"])
        monkeypatch.setattr(
            loader, "_fetch_yfinance", lambda ticker, start, end: frame
        )

        loader.get_history("KO", date(2026, 8, 3), date(2026, 8, 5))

        assert loader.dividends_between("KO", date(2026, 8, 1), date(2026, 8, 5)) == []


class TestSplits:
    """Audit item 22 / CE-5: yfinance's ``Stock Splits`` column was
    fetched with every price and thrown away, so the live book had no
    way to learn that a holding's share count had changed."""

    @staticmethod
    def _frame(splits: list[float]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Open": [100.0, 50.0, 51.0],
                "High": [101.0, 51.0, 52.0],
                "Low": [99.0, 49.0, 50.0],
                "Close": [100.0, 50.0, 51.0],
                "Adj Close": [100.0, 50.0, 51.0],
                "Volume": [1000, 2000, 2100],
                "Dividends": [0.0, 0.0, 0.0],
                "Stock Splits": splits,
            },
            index=pd.DatetimeIndex(["2026-08-03", "2026-08-04", "2026-08-05"]),
        )

    def _loaded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        splits: list[float] | None = None,
    ) -> PriceDataLoader:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        frame = self._frame(splits if splits is not None else [0.0, 2.0, 0.0])
        monkeypatch.setattr(
            loader, "_fetch_yfinance", lambda ticker, start, end: frame
        )
        loader.get_history("NVDA", date(2026, 8, 3), date(2026, 8, 5))
        return loader

    def test_a_split_is_persisted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = self._loaded(tmp_path, monkeypatch)

        found = loader.splits_between("nvda", date(2026, 8, 1), date(2026, 8, 5))

        ((ex_date, ratio),) = found
        assert ex_date == date(2026, 8, 4)
        assert ratio == pytest.approx(2.0)

    def test_zero_and_one_are_not_splits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every bar carries the column; 0 means nothing happened, and a
        # 1.0 ratio would change nothing if it were applied.
        loader = self._loaded(tmp_path, monkeypatch, splits=[0.0, 1.0, 0.0])

        assert loader.splits_between("NVDA", date(2026, 8, 1), date(2026, 8, 5)) == []

    def test_a_reverse_split_is_kept_as_its_ratio(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = self._loaded(tmp_path, monkeypatch, splits=[0.0, 0.0, 0.1])

        found = loader.splits_between("NVDA", date(2026, 8, 1), date(2026, 8, 5))

        ((ex_date, ratio),) = found
        assert ex_date == date(2026, 8, 5)
        assert ratio == pytest.approx(0.1)

    def test_the_lower_bound_is_exclusive_and_the_upper_inclusive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A position bought on the ex-date paid the post-split price, so
        # the caller passes its entry date as the exclusive lower bound.
        loader = self._loaded(tmp_path, monkeypatch)

        assert loader.splits_between("NVDA", date(2026, 8, 4), date(2026, 8, 5)) == []
        assert len(loader.splits_between("NVDA", date(2026, 8, 3), date(2026, 8, 4))) == 1

    def test_refetching_does_not_duplicate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = self._loaded(tmp_path, monkeypatch)
        loader.get_history(
            "NVDA", date(2026, 8, 3), date(2026, 8, 5), force_refresh=True
        )

        assert len(loader.splits_between("NVDA", date(2026, 8, 1), date(2026, 8, 5))) == 1

    def test_a_frame_without_the_column_is_tolerated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        frame = self._frame([0.0, 2.0, 0.0]).drop(columns=["Stock Splits"])
        monkeypatch.setattr(
            loader, "_fetch_yfinance", lambda ticker, start, end: frame
        )

        loader.get_history("NVDA", date(2026, 8, 3), date(2026, 8, 5))

        assert loader.splits_between("NVDA", date(2026, 8, 1), date(2026, 8, 5)) == []


class TestCarryForwardIsBounded:
    """A price may be carried over a closed market, not over a data hole.

    ``get_price_on`` used to check only that ``as_of`` fell between the
    outer edges of the cached series, then return the last bar at or
    before it however old. Measured over 400 cached tickers and 831,193
    inter-bar gaps, 99.94% are five days or under — every routine
    closure — while 307 of the 395 usable tickers carry at least one
    larger hole, median worst 367 days, maximum 4,021. AAPL's real
    series runs 2010-01-04 to 2026-07-31 with a 3,657-day gap, so a
    June 2015 lookup returned the close from 2010-12-31.
    """

    @staticmethod
    def _series(loader: PriceDataLoader, dates: list[str], px: list[float]) -> None:
        df = pd.DataFrame(
            {
                "Open": px,
                "High": px,
                "Low": px,
                "Close": px,
                "Adj Close": px,
                "Volume": [1000] * len(px),
            },
            index=pd.DatetimeIndex(dates),
        )
        loader._write_cache("AAPL", df)

    @staticmethod
    def _no_network(
        loader: PriceDataLoader, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            loader, "_fetch_yfinance", lambda ticker, start, end: pd.DataFrame()
        )

    def test_a_long_weekend_still_carries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Friday close answering for the Tuesday after a Monday holiday
        # — four days. This is the case the carry-forward exists for and
        # it must keep working.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._series(loader, ["2026-05-22"], [200.0])
        self._no_network(loader, monkeypatch)

        assert loader.get_price_on("AAPL", date(2026, 5, 26)) == pytest.approx(
            200.0
        )

    def test_a_year_wide_hole_does_not(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The real AAPL shape: bars on both sides, nothing in between.
        # The date sits inside the outer range, so the old range check
        # passed it and handed back a price a year stale.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._series(loader, ["2024-12-31", "2026-01-02"], [249.0, 270.0])
        self._no_network(loader, monkeypatch)

        assert loader.get_price_on("AAPL", date(2025, 11, 27)) is None

    def test_the_holiday_inside_a_dense_series_is_fine(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Thanksgiving 2025 with the surrounding week actually cached.
        # No NYSE calendar needed: the bar is one day old, so it answers.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._series(
            loader,
            ["2025-11-24", "2025-11-25", "2025-11-26", "2025-11-28"],
            [240.0, 241.0, 242.0, 243.0],
        )
        self._no_network(loader, monkeypatch)

        assert loader.get_price_on("AAPL", date(2025, 11, 27)) == pytest.approx(
            242.0
        )

    def test_it_refetches_before_giving_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A stale cache hit is not a verdict. The gap may be fillable,
        # so the fast path falls through to the fetch instead of
        # returning None outright.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._series(loader, ["2024-12-31", "2026-01-02"], [249.0, 270.0])
        calls: list[tuple[date, date]] = []

        def _fetch(ticker: str, start: date, end: date) -> pd.DataFrame:
            calls.append((start, end))
            idx = pd.DatetimeIndex(["2025-11-26"])
            return pd.DataFrame(
                {
                    "Open": [255.0],
                    "High": [255.0],
                    "Low": [255.0],
                    "Close": [255.0],
                    "Adj Close": [255.0],
                    "Volume": [1000],
                },
                index=idx,
            )

        monkeypatch.setattr(loader, "_fetch_yfinance", _fetch)

        assert loader.get_price_on("AAPL", date(2025, 11, 27)) == pytest.approx(
            255.0
        )
        assert calls, "the stale cache hit should have triggered a refetch"

    def test_the_bound_is_configurable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._series(loader, ["2026-05-01"], [200.0])
        self._no_network(loader, monkeypatch)

        assert loader.get_price_on("AAPL", date(2026, 5, 10)) is None
        assert loader.get_price_on(
            "AAPL", date(2026, 5, 10), max_carry_days=30
        ) == pytest.approx(200.0)

    def test_the_default_bound_covers_every_scheduled_closure(self) -> None:
        # Longest routine NYSE gap is four days — Friday to Tuesday with
        # a Monday holiday, or Thursday to Monday around Good Friday.
        assert MAX_CARRY_FORWARD_DAYS == 5

    def test_a_stale_forced_refresh_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The close-of-day mark must not settle on a week-old bar
        # either; force_refresh goes through the same bound.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._series(loader, ["2026-07-20"], [200.0])
        self._no_network(loader, monkeypatch)

        assert (
            loader.get_price_on("AAPL", date(2026, 7, 31), force_refresh=True)
            is None
        )


class TestNoDataCache:
    """Stop re-asking Yahoo about symbols it does not carry.

    979 of the full-market universe's 6,601 tickers have no series at
    all — SPAC units, warrants, rights. Every screen asked about every
    one of them, every rebalance, in every agent's run.
    """

    @staticmethod
    def _loader(tmp_path: Path) -> PriceDataLoader:
        return PriceDataLoader(cache_path=tmp_path / "prices.sqlite")

    def test_an_unrecorded_ticker_is_not_absent(self, tmp_path: Path) -> None:
        loader = self._loader(tmp_path)

        assert not loader.known_absent(
            "AACBU", date(2024, 12, 31), today=date(2026, 8, 7)
        )

    def test_a_recorded_ticker_is_absent(self, tmp_path: Path) -> None:
        loader = self._loader(tmp_path)
        loader._record_absent("AACBU", date(2024, 12, 31), today=date(2026, 8, 7))

        assert loader.known_absent(
            "AACBU", date(2024, 12, 31), today=date(2026, 8, 7)
        )

    def test_it_is_case_insensitive(self, tmp_path: Path) -> None:
        loader = self._loader(tmp_path)
        loader._record_absent("aacbu", date(2024, 12, 31), today=date(2026, 8, 7))

        assert loader.known_absent(
            "AACBU", date(2024, 12, 31), today=date(2026, 8, 7)
        )

    def test_a_later_window_is_not_suppressed(self, tmp_path: Path) -> None:
        # Nothing through 2024 says nothing about 2026: a company can
        # list after the window that came back empty.
        loader = self._loader(tmp_path)
        loader._record_absent("NEWCO", date(2024, 12, 31), today=date(2026, 8, 7))

        assert not loader.known_absent(
            "NEWCO", date(2026, 8, 6), today=date(2026, 8, 7)
        )

    def test_an_earlier_window_is_suppressed(self, tmp_path: Path) -> None:
        loader = self._loader(tmp_path)
        loader._record_absent("AACBU", date(2024, 12, 31), today=date(2026, 8, 7))

        assert loader.known_absent(
            "AACBU", date(2020, 12, 31), today=date(2026, 8, 7)
        )

    def test_the_record_expires(self, tmp_path: Path) -> None:
        # The safety valve. A symbol recorded during a bad hour at the
        # vendor must not be skipped forever.
        loader = self._loader(tmp_path)
        checked = date(2026, 8, 7)
        loader._record_absent("AACBU", date(2024, 12, 31), today=checked)

        stale = checked + timedelta(days=NO_DATA_TTL_DAYS + 1)
        assert not loader.known_absent("AACBU", date(2024, 12, 31), today=stale)

    def test_it_is_still_trusted_on_the_last_day(self, tmp_path: Path) -> None:
        loader = self._loader(tmp_path)
        checked = date(2026, 8, 7)
        loader._record_absent("AACBU", date(2024, 12, 31), today=checked)

        edge = checked + timedelta(days=NO_DATA_TTL_DAYS)
        assert loader.known_absent("AACBU", date(2024, 12, 31), today=edge)

    def test_re_recording_keeps_the_widest_window(self, tmp_path: Path) -> None:
        # Two agents screen the same dead symbol over different windows.
        # The narrower one must not shrink what the wider one learned.
        loader = self._loader(tmp_path)
        loader._record_absent("AACBU", date(2024, 12, 31), today=date(2026, 8, 7))
        loader._record_absent("AACBU", date(2020, 12, 31), today=date(2026, 8, 7))

        assert loader.known_absent(
            "AACBU", date(2024, 12, 31), today=date(2026, 8, 7)
        )

    def test_re_recording_refreshes_the_clock(self, tmp_path: Path) -> None:
        loader = self._loader(tmp_path)
        loader._record_absent("AACBU", date(2024, 12, 31), today=date(2026, 8, 1))
        loader._record_absent("AACBU", date(2024, 12, 31), today=date(2026, 8, 7))

        # Expired against the first check, live against the second.
        assert loader.known_absent(
            "AACBU", date(2024, 12, 31), today=date(2026, 8, 13)
        )


class TestGetHistorySkipsKnownDeadSymbols:
    """The end-to-end effect: one network call, not one per screen."""

    @staticmethod
    def _stub_loader(tmp_path: Path) -> tuple[PriceDataLoader, list[str]]:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        calls: list[str] = []

        def _empty(ticker: str, start: date, end: date) -> pd.DataFrame:
            calls.append(ticker)
            return pd.DataFrame()

        loader._fetch_yfinance = _empty  # type: ignore[method-assign]
        return loader, calls

    def test_the_second_ask_does_not_reach_the_network(
        self, tmp_path: Path
    ) -> None:
        loader, calls = self._stub_loader(tmp_path)

        first = loader.get_history("AACBU", date(2019, 12, 30), date(2024, 12, 31))
        second = loader.get_history("AACBU", date(2019, 12, 30), date(2024, 12, 31))

        assert first.empty and second.empty
        # Before the no-data table this was ["AACBU", "AACBU"] — and in a
        # real campaign, sixty of them.
        assert calls == ["AACBU"]

    def test_force_refresh_still_reaches_the_network(self, tmp_path: Path) -> None:
        # An explicit re-check must never be answered from a negative
        # record; that is the manual override for a wrong one.
        loader, calls = self._stub_loader(tmp_path)

        loader.get_history("AACBU", date(2019, 12, 30), date(2024, 12, 31))
        loader.get_history(
            "AACBU", date(2019, 12, 30), date(2024, 12, 31), force_refresh=True
        )

        assert calls == ["AACBU", "AACBU"]

    def test_a_ticker_with_bars_is_never_suppressed(self, tmp_path: Path) -> None:
        # The invariant that makes this safe: a symbol that has ever
        # returned a price keeps going to the network for windows the
        # cache does not cover, whatever an empty fetch recorded.
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
        loader._write_cache(
            "REAL",
            pd.DataFrame(
                {
                    "Open": [10.0, 11.0],
                    "High": [11.0, 12.0],
                    "Low": [9.0, 10.0],
                    "Close": [10.5, 11.5],
                    "Adj Close": [10.5, 11.5],
                    "Volume": [1000, 1000],
                },
                index=idx,
            ),
        )
        calls: list[str] = []

        def _empty(ticker: str, start: date, end: date) -> pd.DataFrame:
            calls.append(ticker)
            return pd.DataFrame()

        loader._fetch_yfinance = _empty  # type: ignore[method-assign]

        loader.get_history("REAL", date(2019, 12, 30), date(2024, 12, 31))
        loader.get_history("REAL", date(2019, 12, 30), date(2024, 12, 31))

        assert calls == ["REAL", "REAL"]
        assert not loader.known_absent(
            "REAL", date(2024, 12, 31), today=date(2026, 8, 7)
        )


class TestTrailingReturn:
    """The momentum leg's input.

    12-and-1 is the standard construction: the most recent month is
    dropped because short-horizon returns reverse, so including it mixes
    a signal of the opposite sign into the score.
    """

    @staticmethod
    def _loader_with_series(tmp_path: Path, prices: dict[str, float]) -> PriceDataLoader:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        idx = pd.DatetimeIndex(list(prices.keys()))
        n = len(prices)
        loader._write_cache(
            "X",
            pd.DataFrame(
                {
                    "Open": list(prices.values()),
                    "High": list(prices.values()),
                    "Low": list(prices.values()),
                    "Close": list(prices.values()),
                    "Adj Close": list(prices.values()),
                    "Volume": [1000] * n,
                },
                index=idx,
            ),
        )
        return loader

    def test_it_measures_the_window_not_the_whole_history(
        self, tmp_path: Path
    ) -> None:
        # 100 a year ago, 150 a month ago, 300 today. 12-1 must read
        # +50%, not the +200% the last month would add.
        loader = self._loader_with_series(
            tmp_path,
            {"2023-12-29": 100.0, "2024-11-29": 150.0, "2024-12-31": 300.0},
        )

        r = loader.trailing_return(
            "X", date(2024, 12, 31), lookback_months=12, skip_months=1
        )

        assert r == pytest.approx(50.0, abs=1.0)

    def test_skipping_nothing_includes_the_last_month(
        self, tmp_path: Path
    ) -> None:
        loader = self._loader_with_series(
            tmp_path,
            {"2023-12-29": 100.0, "2024-11-29": 150.0, "2024-12-31": 300.0},
        )

        r = loader.trailing_return(
            "X", date(2024, 12, 31), lookback_months=12, skip_months=0
        )

        assert r == pytest.approx(200.0, abs=1.0)

    def test_a_ticker_with_no_history_is_unknowable(self, tmp_path: Path) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")

        assert (
            loader.trailing_return(
                "NOPE", date(2024, 12, 31), lookback_months=12, skip_months=1
            )
            is None
        )

    def test_a_hole_where_the_window_starts_is_unknowable(
        self, tmp_path: Path
    ) -> None:
        # Only recent bars: the window's start has nothing within the
        # carry-forward bound. Scoring it off whatever survived nearby
        # would put an invented number into a ranking.
        loader = self._loader_with_series(
            tmp_path, {"2024-11-29": 150.0, "2024-12-31": 300.0}
        )

        assert (
            loader.trailing_return(
                "X", date(2024, 12, 31), lookback_months=12, skip_months=1
            )
            is None
        )

    def test_a_flat_series_returns_zero_not_none(self, tmp_path: Path) -> None:
        # Zero momentum is a real answer and must rank as such, distinct
        # from "cannot tell".
        loader = self._loader_with_series(
            tmp_path,
            {"2023-12-29": 100.0, "2024-11-29": 100.0, "2024-12-31": 100.0},
        )

        r = loader.trailing_return(
            "X", date(2024, 12, 31), lookback_months=12, skip_months=1
        )

        assert r == pytest.approx(0.0)

    def test_an_inverted_window_raises(self, tmp_path: Path) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")

        with pytest.raises(ValueError):
            loader.trailing_return(
                "X", date(2024, 12, 31), lookback_months=1, skip_months=12
            )


class TestOneAdjustmentBasisPerTicker:
    """Audit PD-05 / CE-5: the cache must hold each ticker on one basis.

    yfinance restates the whole history whenever a split or a dividend
    is applied, but the cache only ever replaced the window it had just
    fetched. ``get_price_on`` fetched by calendar year, so each year was
    adjusted as of the day it happened to be fetched: APH reads 135.14
    on 2025-12-31 and 69.86 on 2026-01-02 — its 2:1 split applied to
    2026 only — and its dividend table halves at the same boundary.
    """

    SPLIT_DAY = date(2026, 1, 2)
    TODAY = date(2026, 1, 9)

    @staticmethod
    def _frame(
        days: list[pd.Timestamp],
        close: list[float],
        adj: list[float],
        volume: list[int],
        dividends: list[float] | None = None,
    ) -> pd.DataFrame:
        data: dict[str, list[float] | list[int]] = {
            "Open": close,
            "High": [c * 1.01 for c in close],
            "Low": [c * 0.99 for c in close],
            "Close": close,
            "Adj Close": adj,
            "Volume": volume,
        }
        if dividends is not None:
            data["Dividends"] = dividends
        return pd.DataFrame(data, index=pd.DatetimeIndex(days))

    def _seed_old_basis(
        self,
        loader: PriceDataLoader,
        days: list[pd.Timestamp],
        close: float,
        adj: float,
        dividend: tuple[str, float] | None = None,
    ) -> None:
        divs = None
        if dividend is not None:
            divs = [dividend[1] if d == pd.Timestamp(dividend[0]) else 0.0 for d in days]
        df = self._frame(
            days, [close] * len(days), [adj] * len(days), [1000] * len(days), divs
        )
        loader._write_cache("SPL", df)
        loader._write_dividends("SPL", df)

    @staticmethod
    def _days(start: str, end: str) -> list[pd.Timestamp]:
        return list(pd.bdate_range(start, end))

    def _vendor(
        self,
        pre_close: float,
        pre_adj: float,
        post_close: float,
        dividend: tuple[str, float] | None = None,
        cutover: date | None = None,
    ) -> tuple[Callable[[str, date, date], pd.DataFrame], list[tuple[date, date]]]:
        """A stub yfinance serving every bar on today's basis."""
        cut = cutover or self.SPLIT_DAY
        calls: list[tuple[date, date]] = []

        def fetch(ticker: str, start: date, end: date) -> pd.DataFrame:
            calls.append((start, end))
            days = list(pd.bdate_range(start, min(end, self.TODAY)))
            if not days:
                return pd.DataFrame()
            close = [pre_close if d.date() < cut else post_close for d in days]
            adj = [pre_adj if d.date() < cut else post_close for d in days]
            vol = [2000 for _ in days]
            divs = [
                dividend[1] if dividend and d == pd.Timestamp(dividend[0]) else 0.0
                for d in days
            ]
            return self._frame(days, close, adj, vol, divs)

        return fetch, calls

    def _row(self, loader: PriceDataLoader, day: str) -> pd.Series:
        df = loader._read_cached("SPL", date.fromisoformat(day), date.fromisoformat(day))
        assert len(df) == 1, f"no cached bar on {day}"
        return df.iloc[0]

    def test_refetch_with_constant_factor_restates_full_history(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 2025 was cached before a 2:1 split at 140; the vendor now
        # serves it at 70. Asking for a 2026 date must not leave 2025 at
        # 140 next to 2026 at 71.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._seed_old_basis(
            loader,
            self._days("2025-06-02", "2025-06-06") + self._days("2025-12-01", "2025-12-31"),
            close=140.0,
            adj=140.0,
            dividend=("2025-06-03", 0.25),
        )
        fetch, _ = self._vendor(pre_close=70.0, pre_adj=70.0, post_close=71.0)
        monkeypatch.setattr(loader, "_fetch_yfinance", fetch)

        assert loader.get_price_on("SPL", date(2026, 1, 5)) == pytest.approx(71.0)

        # Inside the overlap and far outside any fetched window alike.
        for day in ("2025-12-31", "2025-06-04"):
            bar = self._row(loader, day)
            assert bar["close"] == pytest.approx(70.0)
            assert bar["adj_close"] == pytest.approx(70.0)
            assert bar["open"] == pytest.approx(70.0)
            assert bar["volume"] == 2000
        paid = loader.dividends_between("SPL", date(2025, 6, 1), date(2025, 6, 30))
        assert [d for d, _ in paid] == [date(2025, 6, 3)]
        assert paid[0][1] == pytest.approx(0.125)
        # The year boundary carries no phantom -49%.
        r = loader.trailing_return(
            "SPL", date(2026, 1, 5), lookback_months=1, skip_months=0
        )
        assert r == pytest.approx((71.0 / 70.0 - 1) * 100)

    def test_a_new_year_fetch_reaches_back_into_the_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The fetch must share bars with the cache, or there is nothing
        # to measure the basis against.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._seed_old_basis(
            loader, self._days("2025-12-01", "2025-12-31"), close=140.0, adj=140.0
        )
        fetch, calls = self._vendor(pre_close=140.0, pre_adj=140.0, post_close=141.0)
        monkeypatch.setattr(loader, "_fetch_yfinance", fetch)

        loader.get_price_on("SPL", date(2026, 1, 5))

        assert len(calls) == 1
        start, end = calls[0]
        assert start < date(2025, 12, 31) and end == date(2026, 12, 31)

    def test_a_dividend_readjustment_moves_adj_close_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A new ex-date rescales adj_close for every earlier bar and
        # leaves close, volume and the cash dividends alone.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._seed_old_basis(
            loader,
            self._days("2025-06-02", "2025-06-06") + self._days("2025-12-01", "2025-12-31"),
            close=100.0,
            adj=100.0,
            dividend=("2025-06-03", 0.5),
        )
        fetch, _ = self._vendor(pre_close=100.0, pre_adj=99.0, post_close=100.0)
        monkeypatch.setattr(loader, "_fetch_yfinance", fetch)

        loader.get_price_on("SPL", date(2026, 1, 5))

        bar = self._row(loader, "2025-06-04")
        assert bar["adj_close"] == pytest.approx(99.0)
        assert bar["close"] == pytest.approx(100.0)
        assert bar["volume"] == 1000
        paid = loader.dividends_between("SPL", date(2025, 6, 1), date(2025, 6, 30))
        assert [d for d, _ in paid] == [date(2025, 6, 3)]
        assert paid[0][1] == pytest.approx(0.5)

    def test_an_ex_date_inside_the_window_restates_the_older_bars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The close-of-day refresh is a short window that usually holds
        # the new ex-date. Bars older than the window take the factor
        # seen on the window's oldest bars — every event in the window
        # is after them.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._seed_old_basis(
            loader,
            self._days("2025-06-02", "2025-06-06") + self._days("2025-12-01", "2025-12-31"),
            close=100.0,
            adj=100.0,
        )
        fetch, _ = self._vendor(
            pre_close=100.0, pre_adj=99.0, post_close=100.0, cutover=date(2025, 12, 29)
        )
        monkeypatch.setattr(loader, "_fetch_yfinance", fetch)

        loader.get_history(
            "SPL", date(2025, 12, 15), date(2025, 12, 31), force_refresh=True
        )

        assert self._row(loader, "2025-06-04")["adj_close"] == pytest.approx(99.0)
        assert self._row(loader, "2025-12-01")["adj_close"] == pytest.approx(99.0)
        assert self._row(loader, "2025-12-30")["adj_close"] == pytest.approx(100.0)

    def test_fetching_earlier_history_refetches_the_newer_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 2026 was cached before the split; a backtest now asks for 2025.
        # The newer bars must move to the vendor's current basis too, and
        # they are refetched rather than rescaled: a factor measured at
        # one edge of a window says nothing about bars beyond its other
        # edge when the cache mixes bases.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._seed_old_basis(
            loader, self._days("2026-01-02", "2026-03-31"), close=140.0, adj=140.0
        )
        monkeypatch.setattr(self, "TODAY", date(2026, 4, 15))
        fetch, calls = self._vendor(
            pre_close=70.0, pre_adj=70.0, post_close=70.0, cutover=date(2027, 1, 1)
        )
        monkeypatch.setattr(loader, "_fetch_yfinance", fetch)

        loader.get_history("SPL", date(2025, 12, 1), date(2025, 12, 31))

        assert calls == [(date(2025, 12, 1), date(2026, 3, 31))]
        bar = self._row(loader, "2026-03-31")
        assert bar["close"] == pytest.approx(70.0)
        assert bar["volume"] == 2000

    def _seed_mixed_cache(self, loader: PriceDataLoader) -> None:
        # The real APH shape: 2025 cached before its 2:1 split at 140,
        # 2026 cached after it at 70. The vendor serves all of it at 70.
        self._seed_old_basis(
            loader,
            self._days("2025-06-02", "2025-06-06") + self._days("2025-12-01", "2025-12-31"),
            close=140.0,
            adj=140.0,
        )
        self._seed_old_basis(
            loader, self._days("2026-01-02", "2026-03-31"), close=70.0, adj=70.0
        )

    def test_a_mid_span_refetch_leaves_the_newest_bars_on_their_basis(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Review of PD-05: a forced refetch of 2025 measured ×0.5 on its
        # newest shared bars and applied it to 2026, which was already on
        # the current basis, so get_price_on read half the real price.
        # backfill_price_gap.py and get_price_on's stale-hole path both
        # make exactly this kind of mid-span forced fetch.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._seed_mixed_cache(loader)
        monkeypatch.setattr(self, "TODAY", date(2026, 4, 15))
        fetch, calls = self._vendor(
            pre_close=70.0, pre_adj=70.0, post_close=70.0, cutover=date(2027, 1, 1)
        )
        monkeypatch.setattr(loader, "_fetch_yfinance", fetch)

        loader.get_history(
            "SPL", date(2025, 12, 1), date(2025, 12, 31), force_refresh=True
        )

        assert loader.get_price_on("SPL", date(2026, 3, 31)) == pytest.approx(70.0)
        assert calls[0][1] == date(2026, 3, 31)
        for day in ("2025-06-04", "2025-12-31", "2026-01-02", "2026-03-31"):
            assert self._row(loader, day)["adj_close"] == pytest.approx(70.0), day

    def test_a_partial_repair_is_not_undone_by_the_next_year_fetch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Review of PD-05: after the mid-span refetch, the next 2026
        # year fetch measured ×2 and pushed every older bar back up —
        # the cache flipped between two wrong states on alternate runs.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._seed_mixed_cache(loader)
        monkeypatch.setattr(self, "TODAY", date(2026, 4, 15))
        fetch, _ = self._vendor(
            pre_close=70.0, pre_adj=70.0, post_close=70.0, cutover=date(2027, 1, 1)
        )
        monkeypatch.setattr(loader, "_fetch_yfinance", fetch)

        loader.get_history(
            "SPL", date(2025, 12, 1), date(2025, 12, 31), force_refresh=True
        )
        loader.get_history(
            "SPL", date(2026, 1, 1), date(2026, 12, 31), force_refresh=True
        )

        for day in ("2025-06-04", "2025-12-31", "2026-01-02", "2026-03-31"):
            assert self._row(loader, day)["adj_close"] == pytest.approx(70.0), day

    def test_a_failed_write_rolls_the_restatement_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Review of PD-05: the restatement and the write of the fetched
        # bars ran in separate connections, so a failed write left the
        # older bars rescaled next to unreplaced bars on the old basis.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._seed_old_basis(
            loader,
            self._days("2025-06-02", "2025-06-06") + self._days("2025-12-01", "2025-12-31"),
            close=140.0,
            adj=140.0,
        )
        fetch, _ = self._vendor(pre_close=70.0, pre_adj=70.0, post_close=71.0)
        monkeypatch.setattr(loader, "_fetch_yfinance", fetch)
        real_write = loader._write_cache

        def failing_write(
            ticker: str, df: pd.DataFrame, *args: sqlite3.Connection | None
        ) -> int:
            real_write(ticker, df, *args)
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(loader, "_write_cache", failing_write)

        with pytest.raises(sqlite3.OperationalError):
            loader.get_history("SPL", date(2026, 1, 1), date(2026, 1, 9))

        for day in ("2025-06-04", "2025-12-31"):
            assert self._row(loader, day)["close"] == pytest.approx(140.0), day
        assert loader.cached_range("SPL") == (date(2025, 6, 2), date(2025, 12, 31))

    def test_an_inconsistent_ratio_restates_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No constant factor, no restatement: a vendor that disagrees
        # bar by bar is not a split, and guessing would corrupt history.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        old_days = self._days("2025-06-02", "2025-06-06") + self._days(
            "2025-12-01", "2025-12-31"
        )
        self._seed_old_basis(loader, old_days, close=100.0, adj=100.0)

        def noisy(ticker: str, start: date, end: date) -> pd.DataFrame:
            days = list(pd.bdate_range(start, min(end, self.TODAY)))
            px = [100.0 * (1 + 0.03 * (i % 4)) for i in range(len(days))]
            return self._frame(days, px, px, [1000] * len(days))

        monkeypatch.setattr(loader, "_fetch_yfinance", noisy)
        loader.get_price_on("SPL", date(2026, 1, 5))

        bar = self._row(loader, "2025-06-04")
        assert bar["close"] == pytest.approx(100.0)
        assert bar["adj_close"] == pytest.approx(100.0)

    def test_an_empty_refetch_leaves_the_cache_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._seed_old_basis(
            loader, self._days("2025-12-01", "2025-12-31"), close=140.0, adj=140.0
        )
        monkeypatch.setattr(
            loader, "_fetch_yfinance", lambda ticker, start, end: pd.DataFrame()
        )

        loader.get_price_on("SPL", date(2026, 1, 5))

        assert self._row(loader, "2025-12-31")["close"] == pytest.approx(140.0)

    def test_one_stale_intraday_bar_is_not_a_basis_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The morning run caches an intraday quote under today's date;
        # the evening refresh replaces it. That single disagreeing bar
        # must not be read as a re-adjustment of the whole history.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._seed_old_basis(
            loader,
            self._days("2025-06-02", "2025-06-06") + self._days("2026-01-02", "2026-01-08"),
            close=71.0,
            adj=71.0,
        )
        loader._write_cache(
            "SPL", self._frame([pd.Timestamp("2026-01-09")], [73.0], [73.0], [500])
        )
        fetch, _ = self._vendor(pre_close=71.0, pre_adj=71.0, post_close=71.0)
        monkeypatch.setattr(loader, "_fetch_yfinance", fetch)

        assert loader.get_price_on(
            "SPL", date(2026, 1, 9), force_refresh=True
        ) == pytest.approx(71.0)
        bar = self._row(loader, "2025-06-04")
        assert bar["close"] == pytest.approx(71.0)
        assert bar["volume"] == 1000


class TestFullDividendHistory:
    """Audit CE-3c: the dividend table must not depend on fetch windows.

    Dividends were only ever written from the ``Dividends`` column of a
    price fetch, so an ex-date was recorded only if some fetch window
    happened to contain it. Bars cached without their dividends leave a
    permanent hole: ACN had none between 2025-07-10 and 2026-04-09, AGO
    none between 2025-05-16 and 2026-03-06, and nothing refetches bars
    the cache already covers. A hole inside a holding window is a
    payment the live book never receives.

    These tests stub the ``yfinance`` module itself rather than
    ``_fetch_yfinance``, because the fix lives in that method.
    """

    EX_DATES = ("2025-11-14", "2026-02-13", "2026-05-15", "2026-08-14", "2026-11-06")
    AMOUNT = 0.40
    ENTRY = date(2026, 2, 2)
    AS_OF = date(2026, 11, 10)

    class _Ticker:
        """Stands in for ``yfinance.Ticker``; counts history requests."""

        calls: ClassVar[list[str]] = []
        dividends_value: ClassVar[object] = None
        error: ClassVar[Exception | None] = None

        def __init__(self, ticker: str) -> None:
            self._ticker = ticker

        @property
        def dividends(self) -> object:
            cls = type(self)
            cls.calls.append(self._ticker)
            error = cls.error
            if error is not None:
                raise error
            return cls.dividends_value

    @classmethod
    def _schedule(cls) -> pd.Series:
        # yfinance returns ex-dates at midnight New York time.
        index = pd.DatetimeIndex(list(cls.EX_DATES)).tz_localize("America/New_York")
        return pd.Series([cls.AMOUNT] * len(cls.EX_DATES), index=index, name="Dividends")

    @classmethod
    def _download(cls, ticker: str, start: str, end: str, **_: object) -> pd.DataFrame:
        # ``end`` is exclusive, as in yfinance.
        days = pd.bdate_range(start, pd.Timestamp(end) - pd.Timedelta(days=1))
        ex = {pd.Timestamp(d) for d in cls.EX_DATES}
        return pd.DataFrame(
            {
                "Open": 50.0,
                "High": 50.5,
                "Low": 49.5,
                "Close": 50.0,
                "Adj Close": 50.0,
                "Volume": 1000,
                "Dividends": [cls.AMOUNT if d in ex else 0.0 for d in days],
            },
            index=days,
        )

    @pytest.fixture
    def vendor(self, monkeypatch: pytest.MonkeyPatch) -> type[_Ticker]:
        import yfinance

        ticker_cls = self._Ticker
        monkeypatch.setattr(ticker_cls, "calls", [])
        monkeypatch.setattr(ticker_cls, "dividends_value", self._schedule())
        monkeypatch.setattr(ticker_cls, "error", None)
        monkeypatch.setattr(yfinance, "download", self._download)
        monkeypatch.setattr(yfinance, "Ticker", ticker_cls)
        return ticker_cls

    @staticmethod
    def _cache_without_dividends(loader: PriceDataLoader) -> None:
        # Bars already cached with no dividend rows — the ACN/AGO state.
        days = pd.bdate_range("2026-01-02", "2026-10-30")
        loader._write_cache(
            "QTR",
            pd.DataFrame(
                {
                    "Open": 50.0, "High": 50.5, "Low": 49.5, "Close": 50.0,
                    "Adj Close": 50.0, "Volume": 1000,
                },
                index=days,
            ),
        )

    def test_quarterly_payer_has_no_gap_over_110_days_in_holding_window(
        self, tmp_path: Path, vendor: type[_Ticker]
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        self._cache_without_dividends(loader)

        # The live close-of-day mark: a short refresh window.
        loader.get_price_on("QTR", self.AS_OF, force_refresh=True)

        paid = loader.dividends_between("QTR", self.ENTRY, self.AS_OF)
        edges = [self.ENTRY] + [d for d, _ in paid] + [self.AS_OF]
        gaps = [(b - a).days for a, b in pairwise(edges)]
        assert max(gaps) <= 110, (paid, gaps)
        assert [d.isoformat() for d, _ in paid] == list(self.EX_DATES[1:])
        assert all(a == pytest.approx(self.AMOUNT) for _, a in paid)

    def test_ex_dates_keep_their_new_york_calendar_date(
        self, tmp_path: Path, vendor: type[_Ticker]
    ) -> None:
        # Converting the midnight-New-York stamps through UTC would still
        # land on the same day; converting them to naive UTC-minus-anything
        # would move every ex-date back one day and break idempotency
        # against the window column's rows.
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")

        loader.get_history("QTR", date(2026, 11, 2), date(2026, 11, 10))

        with sqlite3.connect(tmp_path / "p.sqlite") as conn:
            rows = conn.execute(
                "SELECT ex_date FROM dividends WHERE ticker = 'QTR' ORDER BY ex_date"
            ).fetchall()
        assert [r[0] for r in rows] == list(self.EX_DATES)

    def test_history_endpoint_failure_keeps_the_price_fetch_and_window_dividend(
        self, tmp_path: Path, vendor: type[_Ticker], caplog: pytest.LogCaptureFixture
    ) -> None:
        vendor.error = RuntimeError("429 Too Many Requests")
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")

        df = loader.get_history("QTR", date(2026, 11, 2), date(2026, 11, 10))

        assert not df.empty
        paid = loader.dividends_between("QTR", date(2025, 1, 1), self.AS_OF)
        assert [d for d, _ in paid] == [date(2026, 11, 6)]
        assert paid[0][1] == pytest.approx(self.AMOUNT)
        # Not recorded as synced, so the next fetch asks again.
        loader.get_history("QTR", date(2026, 11, 2), date(2026, 11, 10), force_refresh=True)
        assert len(vendor.calls) == 2

    def test_a_non_series_answer_is_ignored(
        self, tmp_path: Path, vendor: type[_Ticker]
    ) -> None:
        vendor.dividends_value = None
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")

        loader.get_history("QTR", date(2026, 11, 2), date(2026, 11, 10))

        paid = loader.dividends_between("QTR", date(2025, 1, 1), self.AS_OF)
        assert [d for d, _ in paid] == [date(2026, 11, 6)]
        assert paid[0][1] == pytest.approx(self.AMOUNT)

    def test_history_is_read_at_most_once_a_day_per_ticker(
        self, tmp_path: Path, vendor: type[_Ticker], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")
        today = [date(2026, 11, 10)]
        monkeypatch.setattr(loader, "_today", lambda: today[0])

        for _ in range(3):
            loader.get_history(
                "QTR", date(2026, 11, 2), date(2026, 11, 10), force_refresh=True
            )
        assert len(vendor.calls) == 1

        today[0] = date(2026, 11, 11)
        loader.get_history("QTR", date(2026, 11, 2), date(2026, 11, 10), force_refresh=True)
        assert len(vendor.calls) == 2

    def test_zero_and_missing_amounts_in_the_history_are_skipped(
        self, tmp_path: Path, vendor: type[_Ticker]
    ) -> None:
        schedule = self._schedule()
        schedule.iloc[0] = 0.0
        schedule.iloc[1] = float("nan")
        vendor.dividends_value = schedule
        loader = PriceDataLoader(cache_path=tmp_path / "p.sqlite")

        loader.get_history("QTR", date(2026, 11, 2), date(2026, 11, 10))

        paid = loader.dividends_between("QTR", date(2025, 1, 1), self.AS_OF)
        assert [d.isoformat() for d, _ in paid] == list(self.EX_DATES[2:])


class TestFetchedAt:
    """Every cached bar says when it was written (audit PD-08).

    Without it a cached close cannot be told from one fetched intraday
    and never refreshed: the row carried a trade date and nothing else.
    Caches written before the column existed are migrated in place on
    open — the column is added, the bars already there keep their
    values and read ``NULL``, which means "written before we recorded
    it", not a time.
    """

    STAMP = datetime(2026, 11, 13, 21, 35, 7, tzinfo=UTC)

    @staticmethod
    def _bar(day: str, price: float) -> pd.DataFrame:
        return pd.DataFrame(
            {"Open": [price], "High": [price], "Low": [price], "Close": [price],
             "Adj Close": [price], "Volume": [1000]},
            index=pd.DatetimeIndex([day]),
        )

    @staticmethod
    def _columns(path: Path) -> list[str]:
        with sqlite3.connect(path) as conn:
            return [r[1] for r in conn.execute("PRAGMA table_info(prices)")]

    @staticmethod
    def _rows(path: Path) -> list[tuple[object, ...]]:
        with sqlite3.connect(path) as conn:
            return conn.execute(
                "SELECT ticker, trade_date, close, fetched_at FROM prices "
                "ORDER BY ticker, trade_date"
            ).fetchall()

    def _old_cache(self, path: Path) -> None:
        """The schema as it stood before the column, with one bar in it."""
        with sqlite3.connect(path) as conn:
            conn.executescript(
                """
                CREATE TABLE prices (
                    ticker TEXT NOT NULL, trade_date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    adj_close REAL, volume INTEGER,
                    PRIMARY KEY (ticker, trade_date)
                );
                INSERT INTO prices VALUES
                    ('AAPL', '2026-11-12', 1, 1, 1, 230.5, 230.5, 10);
                """
            )

    def test_a_new_cache_has_the_column(self, tmp_path: Path) -> None:
        path = tmp_path / "p.sqlite"
        PriceDataLoader(cache_path=path)
        assert "fetched_at" in self._columns(path)

    def test_a_write_is_stamped_in_utc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "p.sqlite"
        loader = PriceDataLoader(cache_path=path)
        monkeypatch.setattr(loader, "_now", lambda: self.STAMP)
        loader._write_cache("AAPL", self._bar("2026-11-13", 231.0))
        assert self._rows(path) == [
            ("AAPL", "2026-11-13", 231.0, "2026-11-13T21:35:07Z"),
        ]

    def test_a_rewrite_moves_the_stamp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The settled close replacing an intraday bar carries its own time."""
        path = tmp_path / "p.sqlite"
        loader = PriceDataLoader(cache_path=path)
        clock = [self.STAMP - timedelta(hours=6)]
        monkeypatch.setattr(loader, "_now", lambda: clock[0])
        loader._write_cache("AAPL", self._bar("2026-11-13", 229.0))
        clock[0] = self.STAMP
        loader._write_cache("AAPL", self._bar("2026-11-13", 231.0))
        assert self._rows(path) == [
            ("AAPL", "2026-11-13", 231.0, "2026-11-13T21:35:07Z"),
        ]

    def test_an_existing_cache_is_migrated_without_losing_a_bar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "p.sqlite"
        self._old_cache(path)
        assert "fetched_at" not in self._columns(path)

        loader = PriceDataLoader(cache_path=path)
        monkeypatch.setattr(loader, "_now", lambda: self.STAMP)
        loader._write_cache("AAPL", self._bar("2026-11-13", 231.0))

        assert self._columns(path)[-1] == "fetched_at"
        assert self._rows(path) == [
            ("AAPL", "2026-11-12", 230.5, None),
            ("AAPL", "2026-11-13", 231.0, "2026-11-13T21:35:07Z"),
        ]
        cached = loader._read_cached("AAPL", date(2026, 11, 1), date(2026, 11, 30))
        assert list(cached["close"]) == [230.5, 231.0]

    def test_reopening_a_migrated_cache_is_a_no_op(self, tmp_path: Path) -> None:
        path = tmp_path / "p.sqlite"
        self._old_cache(path)
        PriceDataLoader(cache_path=path)
        PriceDataLoader(cache_path=path)
        assert self._columns(path).count("fetched_at") == 1
        assert self._rows(path) == [("AAPL", "2026-11-12", 230.5, None)]
