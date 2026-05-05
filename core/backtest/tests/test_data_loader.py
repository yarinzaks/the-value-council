"""Unit tests for the price data loader.

We test the cache behavior and SQL schema using a temporary SQLite
file. Live yfinance fetches are tested separately with the
``@pytest.mark.integration`` marker so the offline suite remains fast.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.backtest.data_loader import PriceDataLoader, _to_date


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
