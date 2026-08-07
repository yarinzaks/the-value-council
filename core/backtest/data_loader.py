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
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from core.exceptions import ValueCouncilError
from core.logger import get_logger

logger = get_logger("core.backtest.data_loader")

from core.paths import prices_db as _prices_db

DEFAULT_CACHE_PATH = _prices_db()

# Calendar days re-fetched by ``get_price_on(..., force_refresh=True)``.
# Wide enough to still contain a trading bar when ``as_of`` falls on a
# holiday next to a weekend (the longest such gap is four days), narrow
# enough that the daily close-of-day mark stays cheap.
REFRESH_WINDOW_DAYS = 7

#: How stale a bar may be and still answer for ``as_of``.
#:
#: Carrying the last close forward over a closed market is correct and
#: necessary — that is what a weekend or a holiday is. Carrying it
#: forward without limit is not, and that is what used to happen:
#: ``get_price_on`` checked only that ``as_of`` fell between the outer
#: edges of the cached series, then returned the last bar at or before
#: it, however old.
#:
#: Measured over 400 cached tickers and 831,193 inter-bar gaps: 99.94%
#: are 5 days or under — every routine closure. The remaining 505 are
#: data holes, and 307 of the 395 usable tickers (78%) have at least
#: one, with a median worst gap of 367 days and a maximum of 4,021.
#: AAPL's series runs 2010-01-04 to 2026-07-31 with a 3,657-day hole in
#: the middle, so asking for a June 2015 price returned the close from
#: 2010-12-31 as though it were current.
#:
#: Five days covers every scheduled NYSE closure: the longest routine
#: gap is Friday to Tuesday when a holiday falls on Monday, or Thursday
#: to Monday around Good Friday — four days either way — plus one day
#: of margin. Beyond that a missing bar is a gap in the data, not a
#: closed exchange, and the honest answer is that the price is unknown.
#:
#: This also removes the need for an NYSE holiday calendar. The
#: question was never "was the market open on this date" but "is this
#: quote still good", and a bound answers it without a new dependency
#: or a table of dates to maintain.
MAX_CARRY_FORWARD_DAYS = 5


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

-- Cash dividends by ex-date. Separate from prices because the live
-- portfolio has to receive them as cash: marking a position at the
-- close means the ex-date price drop lands in NAV while the payment
-- never does, so the recorded return is price return, not total
-- return. That penalty is proportional to yield, which makes it
-- doctrine-correlated — Neff, Dreman and Graham lose roughly their
-- portfolio yield a year against Buffett and Fisher, for no reason
-- either investor would recognise.
CREATE TABLE IF NOT EXISTS dividends (
    ticker      TEXT NOT NULL,
    ex_date     TEXT NOT NULL,   -- ISO YYYY-MM-DD
    amount      REAL NOT NULL,   -- cash per share, in the quote currency
    PRIMARY KEY (ticker, ex_date)
);
CREATE INDEX IF NOT EXISTS idx_dividends_ticker ON dividends(ticker);
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

    def _write_dividends(self, ticker: str, df: pd.DataFrame) -> int:
        """Persist any non-zero Dividends column rows from a fetch."""
        if df.empty or "Dividends" not in df.columns:
            return 0
        ticker = ticker.upper()
        rows = []
        for idx, value in df["Dividends"].items():
            amount = _f(value)
            if amount is None or amount <= 0:
                continue
            d = idx.date() if isinstance(idx, pd.Timestamp) else idx
            rows.append((ticker, d.isoformat(), amount))
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO dividends (ticker, ex_date, amount)
                VALUES (?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def dividends_between(
        self,
        ticker: str,
        start: date | datetime | str,
        end: date | datetime | str,
    ) -> list[tuple[date, float]]:
        """Cash dividends with ex-date in ``(start, end]``, oldest first.

        The lower bound is exclusive so a caller can pass the last date
        it already settled without paying the same dividend twice.
        """
        start_d, end_d = _to_date(start), _to_date(end)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ex_date, amount FROM dividends
                WHERE ticker = ? AND ex_date > ? AND ex_date <= ?
                ORDER BY ex_date
                """,
                (ticker.upper(), start_d.isoformat(), end_d.isoformat()),
            ).fetchall()
        return [(_to_date(r[0]), float(r[1])) for r in rows]

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
            d = self._write_dividends(ticker, df)
            logger.debug(f"cached {n} rows for {ticker} ({d} dividends)")
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

    def price_extremes(
        self,
        ticker: str,
        as_of: date | datetime,
        *,
        years: float,
    ) -> tuple[float | None, float | None]:
        """Lowest and highest adjusted close over the trailing window.

        Returns ``(low, high)``, either of which is None when the window
        holds no bars. Reads the cache only — this is a screening input
        evaluated across the whole universe, and one network call per
        candidate per rebalance is not affordable. A ticker with no
        cached history yields ``(None, None)``, which callers must read
        as "cannot tell" rather than "not distressed".

        Added for Schloss's entry condition, which needs a 52-week low
        and a five-year high; :func:`agents.schloss.filters.is_distressed_price`
        consumes both.
        """
        as_of_d = as_of.date() if isinstance(as_of, datetime) else as_of
        start = as_of_d - timedelta(days=int(365.25 * years))
        df = self._read_cached(ticker.upper(), start, as_of_d)
        if df.empty:
            return None, None
        closes = df["adj_close"].dropna()
        if closes.empty:
            return None, None
        return float(closes.min()), float(closes.max())

    @staticmethod
    def _fresh_close(
        df: pd.DataFrame,
        as_of: date,
        *,
        max_carry_days: int,
    ) -> float | None:
        """Last adjusted close at or before ``as_of``, if recent enough.

        Returns None when the newest available bar is more than
        ``max_carry_days`` old. That is the difference between carrying
        a price over a closed market and inventing one across a hole in
        the data — see :data:`MAX_CARRY_FORWARD_DAYS`.
        """
        if df.empty:
            return None
        eligible = df[df.index.date <= as_of]
        if eligible.empty:
            return None
        bar_date = eligible.index[-1].date()
        age = (as_of - bar_date).days
        if age > max_carry_days:
            return None
        return float(eligible["adj_close"].iloc[-1])

    def get_price_on(
        self,
        ticker: str,
        as_of: date | datetime,
        *,
        force_refresh: bool = False,
        max_carry_days: int = MAX_CARRY_FORWARD_DAYS,
    ) -> float | None:
        """Return the adjusted close on or just before ``as_of``.

        Optimized for daily NAV walks during backtests:

        1. If the cache already has any data covering ``as_of``, read
           directly from SQLite without going through ``get_history``'s
           strict range-coverage check (which fails on holiday/weekend
           boundaries).
        2. On true cache miss, fetch the entire calendar year so
           subsequent daily lookups in the same year are free.

        Args:
            force_refresh: Re-fetch ``as_of``'s bar even when the cache
                already covers it. Required by the close-of-day mark:
                the morning run caches an intraday quote under today's
                date, and the fast path would otherwise hand that same
                intraday number back at 16:30 ET, making the whole NAV
                history a series of intraday prices labelled as closes.
                Only a short window is re-fetched — a full-year refresh
                would be wasteful and slow for a once-a-day correction.

        Returns None if no data is available.
        """
        as_of_d = as_of.date() if isinstance(as_of, datetime) else as_of

        if force_refresh:
            # INSERT OR REPLACE in _write_cache means the settled close
            # overwrites the intraday row for the same trade_date.
            window_start = as_of_d - timedelta(days=REFRESH_WINDOW_DAYS)
            df = self.get_history(
                ticker, start=window_start, end=as_of_d, force_refresh=True
            )
            return self._fresh_close(df, as_of_d, max_carry_days=max_carry_days)

        # Fast path: if the cache already has data covering as_of, use it.
        # The range check alone is not enough — as_of can sit inside a
        # hole in the series and still fall between its outer edges — so
        # a stale hit falls through to the fetch below rather than
        # returning. The gap may well be fillable.
        cached_range = self.cached_range(ticker)
        if cached_range is not None:
            cmin, cmax = cached_range
            if cmin <= as_of_d <= cmax:
                cached = self._fresh_close(
                    self._read_cached(ticker, cmin, as_of_d),
                    as_of_d,
                    max_carry_days=max_carry_days,
                )
                if cached is not None:
                    return cached
                stale_hit = True
                logger.debug(
                    f"{ticker}@{as_of_d}: cached range covers the date but the "
                    f"nearest bar is over {max_carry_days}d old — refetching"
                )
            else:
                stale_hit = False
        else:
            stale_hit = False

        # Slow path: fetch the whole year so subsequent daily lookups
        # in the same year all hit the cache.
        #
        # ``force_refresh`` on a stale hit is required, not belt-and-
        # braces: get_history applies the same outer-range test, so a
        # year that lies inside a hole reads as "cached, 0 rows" and
        # never reaches the network. Without it the fall-through is a
        # no-op and the gap is permanent. One year-wide fetch repairs
        # the whole year, so the cost is per ticker-year, not per date.
        year_start = as_of_d.replace(month=1, day=1)
        year_end = as_of_d.replace(month=12, day=31)
        df = self.get_history(
            ticker, start=year_start, end=year_end, force_refresh=stale_hit
        )
        return self._fresh_close(df, as_of_d, max_carry_days=max_carry_days)

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
                # actions=True adds a Dividends column. It used to be
                # False, so cash dividends were invisible to the whole
                # system and NAV tracked price return only.
                actions=True,
                threads=False,
            )
        except Exception as exc:
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
