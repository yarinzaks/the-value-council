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
