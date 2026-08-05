"""Unit tests for the price data loader.

We test the cache behavior and SQL schema using a temporary SQLite
file. Live yfinance fetches are tested separately with the
``@pytest.mark.integration`` marker so the offline suite remains fast.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from core.backtest.data_loader import (
    MAX_CARRY_FORWARD_DAYS,
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
