"""Price data loader with SQLite caching.

Wraps yfinance for daily OHLCV + adjusted-close data. All fetches are
cached to ``data/cache/prices.sqlite`` so repeated backtest runs are
fast and reproducible.

Schema (a single table)::

    CREATE TABLE prices (
        ticker      TEXT NOT NULL,
        trade_date  TEXT NOT NULL,   -- ISO YYYY-MM-DD
        open        REAL,
        high        REAL,
        low         REAL,
        close       REAL,
        adj_close   REAL,
        volume      INTEGER,
        PRIMARY KEY (ticker, trade_date)
    );

Adjusted close already incorporates splits and dividends — return
calculations should use it.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

from core.exceptions import ValueCouncilError
from core.logger import get_logger

logger = get_logger("core.backtest.data_loader")

from core.paths import PROJECT_ROOT, prices_db as _prices_db
DEFAULT_CACHE_PATH = _prices_db()


class PriceDataError(ValueCouncilError):
    """Raised when price data cannot be retrieved."""


@dataclass(frozen=True)
class PriceBar:
    """One row of OHLCV data for a ticker on a specific trade date."""

    ticker: str
    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adj_close: float | None
    volume: int | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker      TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    adj_close   REAL,
    volume      INTEGER,
    PRIMARY KEY (ticker, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_prices_ticker ON prices(ticker);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(trade_date);
"""


class PriceDataLoader:
    """Cached price loader backed by yfinance + SQLite.

    Thread-safety: uses one connection per call (SQLite handles
    concurrent readers; we serialize writes through ``check_same_thread=False``
    plus short-lived connections).
    """

    def __init__(self, cache_path: Path | None = None) -> None:
        self.cache_path = cache_path or DEFAULT_CACHE_PATH
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.cache_path, check_same_thread=False)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Cache I/O
    # ------------------------------------------------------------------
    def cached_range(self, ticker: str) -> tuple[date, date] | None:
        """Return (min_date, max_date) of cached data for ticker, if any."""
        ticker = ticker.upper()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MIN(trade_date), MAX(trade_date) FROM prices WHERE ticker = ?",
                (ticker,),
            ).fetchone()
        if not row or row[0] is None:
            return None
        return date.fromisoformat(row[0]), date.fromisoformat(row[1])

    def _read_cached(
        self, ticker: str, start: date, end: date
    ) -> pd.DataFrame:
        ticker = ticker.upper()
        with self._connect() as conn:
            df = pd.read_sql_query(
                """
                SELECT trade_date, open, high, low, close, adj_close, volume
                FROM prices
                WHERE ticker = ? AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date
                """,
                conn,
                params=(ticker, start.isoformat(), end.isoformat()),
            )
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date")
        df.index.name = "date"
        return df

    def _write_cache(self, ticker: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        ticker = ticker.upper()
        rows = []
        for idx, row in df.iterrows():
            d = idx.date() if isinstance(idx, pd.Timestamp) else idx
            rows.append(
                (
                    ticker,
                    d.isoformat(),
                    _f(row.get("Open")),
                    _f(row.get("High")),
                    _f(row.get("Low")),
                    _f(row.get("Close")),
                    _f(row.get("Adj Close", row.get("Close"))),
                    _i(row.get("Volume")),
                )
            )
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO prices
                    (ticker, trade_date, open, high, low, close, adj_close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_history(
        self,
        ticker: str,
        start: date | datetime | str,
        end: date | datetime | str,
        *,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Return daily OHLCV + adj_close for ``ticker`` between dates.

        Cached results are returned when available; missing ranges are
        fetched from yfinance and added to the cache.

        Args:
            ticker: Symbol (e.g., "AAPL"). Case-insensitive.
            start: Inclusive start date.
            end: Inclusive end date.
            force_refresh: If True, bypass cache and re-fetch from yfinance.

        Returns:
            DataFrame indexed by date with columns: open, high, low,
            close, adj_close, volume. Empty if no data is available.
        """
        ticker = ticker.upper()
        start_d = _to_date(start)
        end_d = _to_date(end)
        if start_d > end_d:
            raise ValueError(f"start ({start_d}) must be <= end ({end_d})")

        if not force_refresh:
            cached = self._read_cached(ticker, start_d, end_d)
            cached_range = self.cached_range(ticker)
            if cached_range is not None:
                cmin, cmax = cached_range
                # If the requested range is fully covered, return cache.
                if cmin <= start_d and cmax >= end_d:
                    logger.debug(
                        f"cache hit for {ticker} {start_d}..{end_d} ({len(cached)} rows)"
                    )
                    return cached

        # Fetch from yfinance — we always fetch the full requested range
        # (yfinance is fast enough and merging partial fetches is fragile).
        logger.info(f"fetching {ticker} {start_d}..{end_d} from yfinance")
        df = self._fetch_yfinance(ticker, start_d, end_d)
        if not df.empty:
            n = self._write_cache(ticker, df)
            logger.debug(f"cached {n} rows for {ticker}")
        return self._read_cached(ticker, start_d, end_d)

    def get_adj_close(
        self,
        ticker: str,
        start: date | datetime | str,
        end: date | datetime | str,
    ) -> pd.Series:
        """Convenience: return only the adjusted-close series."""
        df = self.get_history(ticker, start, end)
        if df.empty:
            return pd.Series(dtype=float, name=ticker)
        s = df["adj_close"].rename(ticker)
        return s

    def get_adj_close_panel(
        self,
        tickers: Iterable[str],
        start: date | datetime | str,
        end: date | datetime | str,
    ) -> pd.DataFrame:
        """Return a wide DataFrame: dates × tickers of adjusted close."""
        series = {}
        for t in tickers:
            try:
                series[t.upper()] = self.get_adj_close(t, start, end)
            except PriceDataError as exc:
                logger.warning(f"skipping {t}: {exc}")
                continue
        if not series:
            return pd.DataFrame()
        return pd.concat(series.values(), axis=1).sort_index()

    def get_price_on(self, ticker: str, as_of: date | datetime) -> float | None:
        """Return the adjusted close on or just before ``as_of``.

        Optimized for daily NAV walks during backtests:

        1. If the cache already has any data covering ``as_of``, read
           directly from SQLite without going through ``get_history``'s
           strict range-coverage check (which fails on holiday/weekend
           boundaries).
        2. On true cache miss, fetch the entire calendar year so
           subsequent daily lookups in the same year are free.

        Returns None if no data is available.
        """
        as_of_d = as_of.date() if isinstance(as_of, datetime) else as_of

        # Fast path: if the cache already has data covering as_of, use it.
        cached_range = self.cached_range(ticker)
        if cached_range is not None:
            cmin, cmax = cached_range
            if cmin <= as_of_d <= cmax:
                df = self._read_cached(ticker, cmin, as_of_d)
                if not df.empty:
                    df = df[df.index.date <= as_of_d]
                    if not df.empty:
                        return float(df["adj_close"].iloc[-1])

        # Slow path: fetch the whole year so subsequent daily lookups
        # in the same year all hit the cache.
        year_start = as_of_d.replace(month=1, day=1)
        year_end = as_of_d.replace(month=12, day=31)
        df = self.get_history(ticker, start=year_start, end=year_end)
        if df.empty:
            return None
        df = df[df.index.date <= as_of_d]
        if df.empty:
            return None
        return float(df["adj_close"].iloc[-1])

    # ------------------------------------------------------------------
    # yfinance interface
    # ------------------------------------------------------------------
    def _fetch_yfinance(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Wrap yfinance.download with sane defaults and error normalization."""
        try:
            import yfinance as yf
        except ImportError as exc:
            raise PriceDataError(f"yfinance not installed: {exc}") from exc

        # yfinance treats `end` as exclusive — bump by one day.
        from datetime import timedelta

        end_excl = end + timedelta(days=1)
        try:
            df = yf.download(
                ticker,
                start=start.isoformat(),
                end=end_excl.isoformat(),
                progress=False,
                auto_adjust=False,
                actions=False,
                threads=False,
            )
        except Exception as exc:  # noqa: BLE001 — yfinance throws broad types
            raise PriceDataError(f"yfinance failed for {ticker}: {exc}") from exc

        if df is None or df.empty:
            return pd.DataFrame()

        # yfinance returns multi-index columns when fed a single ticker
        # with auto_adjust=False — flatten it.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)

        return df


def _f(value: object) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _i(value: object) -> int | None:
    f = _f(value)
    if f is None:
        return None
    return int(f)


def _to_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


__all__ = ["PriceBar", "PriceDataError", "PriceDataLoader"]
