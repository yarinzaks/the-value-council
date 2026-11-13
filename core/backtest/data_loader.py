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
        fetched_at  TEXT,            -- UTC ISO-8601 of the write; NULL before it was kept
        PRIMARY KEY (ticker, trade_date)
    );

Adjusted close already incorporates splits and dividends — return
calculations should use it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType

import numpy as np
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

#: How long a "yfinance has nothing for this symbol" answer is trusted.
#:
#: 979 of the full-market universe's 6,601 tickers (14.8%) have no price
#: series at all — mostly SPAC units, warrants and rights (AACBU, AACIW,
#: ACHR-WT) that Yahoo does not serve under those symbols. Nothing
#: recorded that fact, so every screen re-asked about every one of them,
#: at roughly 0.7s per doomed request.
#:
#: The saving is real but far smaller than that 979 suggests, and the
#: 2026-08-07 campaign measured it: only 13 symbols were ever recorded
#: across all ten agents. Doctrine screens on fundamentals before it
#: asks for a price, so the great majority of dead symbols are filtered
#: out before anyone requests a quote. Call it minutes over a campaign,
#: not hours — worth having, not worth claiming more for.
#:
#: The entry is only written when :meth:`PriceDataLoader._fetch_yfinance`
#: returns an empty frame *without raising* — network failures and rate
#: limits come back as :class:`PriceDataError`, so a transient outage
#: cannot be mistaken for an absent symbol. The expiry is the second
#: guard: a symbol wrongly recorded, or one that genuinely starts
#: trading later, is retried within the week instead of being skipped
#: forever.
NO_DATA_TTL_DAYS = 7

#: One adjustment basis per ticker (audit PD-05 / CE-5).
#:
#: yfinance returns every bar restated as of the day it is asked: a
#: split or a new ex-date rescales the whole history. The cache only
#: replaces the window it fetched, so without a correction each window
#: stays on the basis of its own fetch day. Fetching by calendar year
#: made that systematic — APH read 135.14 on 2025-12-31 and 69.86 on
#: 2026-01-02, a 2:1 split applied to 2026 only, and 86 of 5,186 cached
#: tickers jumped more than 60% across that one year boundary.
#:
#: The fix widens every fetch through the cache's newest bar, so nothing
#: newer than a fetch is ever left behind, measures the ratio between
#: fetched and cached bars on the oldest dates they share, and applies
#: it to every older cached bar. These constants govern that measurement.
#:
#: What it does not do is repair a cache that already mixes bases inside
#: its history (APH: 2025 at 134.67, 2026 at 69.50). The daily fetches
#: share bars only with the recent, already-consistent end of such a
#: cache, measure a factor of 1 and leave the older break where it is;
#: only a refetch that reaches back across the break — in practice the
#: ticker's full cached span with ``force_refresh=True`` — heals it.
#:
#: Calendar days of cached history a fetch is widened to include when it
#: would otherwise share too few bars with the cache to measure a basis.
BASIS_ANCHOR_DAYS = 14
#: Shared bars needed before the cache is trusted to measure a basis.
MIN_BASIS_OVERLAP = 3
#: Relative tolerance for two ratios to count as the same factor, and
#: the smallest factor worth applying. Vendor rounding of a two-decimal
#: close sits well under it; a split is orders of magnitude over it; a
#: quarterly dividend under ~0.1% of the price is below it and ignored.
BASIS_RTOL = 1e-3

#: Key under which :meth:`PriceDataLoader._fetch_yfinance` attaches the
#: ticker's full dividend history to the price frame's ``attrs``.
#:
#: Audit CE-3c. Dividends used to come only from the ``Dividends`` column
#: of a price fetch, so an ex-date reached the table only if some fetch
#: window happened to contain it, and bars already cached are never
#: refetched. That left permanent holes — ACN had no row between
#: 2025-07-10 and 2026-04-09, AGO none between 2025-05-16 and 2026-03-06
#: — and a hole inside a holding window is a payment the live book never
#: receives. The full history is one extra request, read at most once a
#: calendar day per ticker (the ``dividend_history_sync`` table), and it
#: is on the vendor's current basis, the same basis
#: :meth:`PriceDataLoader._restate_basis` puts the cached prices on.
DIVIDEND_HISTORY_ATTR = "dividend_history"


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
    fetched_at  TEXT,
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

-- Stock splits by ex-date, as yfinance's ``Stock Splits`` column gives
-- them: new shares per old share (2.0 for 2-for-1, 1.5 for 3-for-2,
-- 0.1 for 1-for-10). The prices above are split-adjusted, so on an
-- ex-date a held line's quote moves by the ratio while the book's share
-- count does not; the live runner reads this table to restate the
-- holding (audit item 22 / CE-5). The column used to be discarded.
CREATE TABLE IF NOT EXISTS splits (
    ticker      TEXT NOT NULL,
    ex_date     TEXT NOT NULL,   -- ISO YYYY-MM-DD
    ratio       REAL NOT NULL,   -- new shares per old share
    PRIMARY KEY (ticker, ex_date)
);
CREATE INDEX IF NOT EXISTS idx_splits_ticker ON splits(ticker);

-- The last calendar day each ticker's full dividend history was read,
-- so the extra request is made at most once a day per ticker.
CREATE TABLE IF NOT EXISTS dividend_history_sync (
    ticker      TEXT PRIMARY KEY,
    synced_on   TEXT NOT NULL    -- ISO YYYY-MM-DD
);

-- Symbols yfinance has no series for, so a screen stops re-asking.
-- ``through`` is the end of the window that came back empty: a hit only
-- counts when the request does not reach past it, so recording that a
-- symbol had nothing up to 2024 never suppresses a 2026 lookup.
CREATE TABLE IF NOT EXISTS no_price_data (
    ticker      TEXT PRIMARY KEY,
    through     TEXT NOT NULL,   -- ISO YYYY-MM-DD, end of the empty window
    checked_at  TEXT NOT NULL    -- ISO YYYY-MM-DD, for the TTL
);
"""

#: Columns added to an existing table after it was first created, as
#: (table, column, declaration). ``CREATE TABLE IF NOT EXISTS`` leaves a
#: cache written by an older version untouched, so each of these is
#: added on open when missing. Nullable and without a default, so the
#: ALTER is a schema-only change that does not rewrite existing rows.
#:
#: ``prices.fetched_at`` is when the row was written from the vendor, in
#: UTC (audit PD-08). Without it a bar cached from an intraday quote
#: could not be told from a settled close. Rows written before the
#: column existed read NULL: unknown, not a time.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("prices", "fetched_at", "TEXT"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Bring an existing cache's tables up to :data:`_ADDED_COLUMNS`.

    Safe to run on every open: a column already present is left alone.
    Two processes opening the same old cache at once can both see it
    missing; the loser's ALTER fails with "duplicate column name", which
    means the column now exists and is not an error.
    """
    for table, column, decl in _ADDED_COLUMNS:
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column in present:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc):
                raise


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
            _add_missing_columns(conn)

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

    def known_absent(self, ticker: str, end: date, *, today: date) -> bool:
        """Has yfinance already said it has no series for ``ticker``?

        Only answers True for a request that does not reach past the
        window that came back empty, and only while the record is inside
        :data:`NO_DATA_TTL_DAYS`. Both bounds matter: the first keeps a
        2024 miss from suppressing a 2026 lookup, the second lets a
        symbol that starts trading — or one recorded during a bad hour
        at the vendor — come back on its own.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT through, checked_at FROM no_price_data WHERE ticker = ?",
                (ticker.upper(),),
            ).fetchone()
        if row is None:
            return False
        through, checked_at = date.fromisoformat(row[0]), date.fromisoformat(row[1])
        if (today - checked_at).days > NO_DATA_TTL_DAYS:
            return False
        return end <= through

    def _record_absent(self, ticker: str, end: date, *, today: date) -> None:
        """Remember that ``ticker`` has no series through ``end``.

        Callers must only reach this after an empty fetch for a ticker
        with no cached bars at all — a symbol that has ever returned a
        price is never suppressed, whatever a later window does.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO no_price_data (ticker, through, checked_at)
                VALUES (?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    through = MAX(excluded.through, no_price_data.through),
                    checked_at = excluded.checked_at
                """,
                (ticker.upper(), end.isoformat(), today.isoformat()),
            )

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

    @staticmethod
    def _today() -> date:
        """Today's date; a method so tests can pin it."""
        return date.today()

    @staticmethod
    def _now() -> datetime:
        """The current UTC time, for ``prices.fetched_at``; pinnable in tests."""
        return datetime.now(UTC)

    def _dividend_history_synced(self, ticker: str) -> bool:
        """Has ``ticker``'s full dividend history been read today?"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT synced_on FROM dividend_history_sync WHERE ticker = ?",
                (ticker.upper(),),
            ).fetchone()
        return row is not None and row[0] == self._today().isoformat()

    def _write_dividends(self, ticker: str, df: pd.DataFrame) -> int:
        """Persist a fetch's dividends: its window and its full history.

        The ``Dividends`` column covers only the fetched window. When
        :meth:`_fetch_yfinance` also attached the ticker's full history
        (``df.attrs[DIVIDEND_HISTORY_ATTR]``), every ex-date in it is
        written too, and replaces a window row for the same date — both
        are the vendor's figure on its current basis. Zero and missing
        amounts are skipped in both.
        """
        if df.empty:
            return 0
        ticker = ticker.upper()
        by_date: dict[str, float] = {}
        sources: list[pd.Series] = []
        if "Dividends" in df.columns:
            sources.append(df["Dividends"])
        history = df.attrs.get(DIVIDEND_HISTORY_ATTR)
        if isinstance(history, pd.Series):
            sources.append(history)
        for series in sources:
            for idx, value in series.items():
                amount = _f(value)
                if amount is None or amount <= 0:
                    continue
                # A tz-aware stamp (midnight New York) keeps its local
                # calendar date; .date() does not convert to UTC.
                d = idx.date() if isinstance(idx, pd.Timestamp) else idx
                by_date[d.isoformat()] = amount
        with self._connect() as conn:
            if by_date:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO dividends (ticker, ex_date, amount)
                    VALUES (?, ?, ?)
                    """,
                    [(ticker, d, a) for d, a in by_date.items()],
                )
            if isinstance(history, pd.Series):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO dividend_history_sync (ticker, synced_on)
                    VALUES (?, ?)
                    """,
                    (ticker, self._today().isoformat()),
                )
        return len(by_date)

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

    def _write_splits(self, ticker: str, df: pd.DataFrame) -> int:
        """Persist the fetch window's stock splits.

        Every bar carries a ``Stock Splits`` value and all but the
        ex-date's are 0. A ratio of exactly 1 changes nothing and is
        skipped with the zeros; a missing column (an older yfinance, a
        test frame) writes nothing. A refetch replaces the row for the
        same ex-date rather than adding one.

        Only the fetched window is read. That is enough for a held name,
        whose bars the runner fetches every session: the ex-date's bar is
        fetched on the ex-date by the morning run and again by the close
        mark's refresh window.
        """
        if df.empty or "Stock Splits" not in df.columns:
            return 0
        ticker = ticker.upper()
        rows: list[tuple[str, str, float]] = []
        for idx, value in df["Stock Splits"].items():
            ratio = _f(value)
            if ratio is None or ratio <= 0 or ratio == 1.0:
                continue
            # A tz-aware stamp keeps its local calendar date, as for
            # dividends.
            d = idx.date() if isinstance(idx, pd.Timestamp) else idx
            rows.append((ticker, d.isoformat(), ratio))
        if rows:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO splits (ticker, ex_date, ratio)
                    VALUES (?, ?, ?)
                    """,
                    rows,
                )
        return len(rows)

    def splits_between(
        self,
        ticker: str,
        start: date | datetime | str,
        end: date | datetime | str,
    ) -> list[tuple[date, float]]:
        """Stock splits with ex-date in ``(start, end]``, oldest first.

        The lower bound is exclusive for the same reason as
        :meth:`dividends_between`, and one more: a position bought on
        the ex-date paid the post-split price, so passing its entry date
        must not split it.
        """
        start_d, end_d = _to_date(start), _to_date(end)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ex_date, ratio FROM splits
                WHERE ticker = ? AND ex_date > ? AND ex_date <= ?
                ORDER BY ex_date
                """,
                (ticker.upper(), start_d.isoformat(), end_d.isoformat()),
            ).fetchall()
        return [(_to_date(r[0]), float(r[1])) for r in rows]

    def _write_cache(
        self,
        ticker: str,
        df: pd.DataFrame,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Insert or replace ``df``'s bars.

        ``conn`` lets :meth:`get_history` write in the same transaction
        as :meth:`_restate_basis`; without it the write commits on its own.
        """
        if df.empty:
            return 0
        ticker = ticker.upper()
        fetched_at = self._now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
                    fetched_at,
                )
            )
        scope: AbstractContextManager[sqlite3.Connection] = (
            self._connect() if conn is None else nullcontext(conn)
        )
        with scope as c:
            c.executemany(
                """
                INSERT OR REPLACE INTO prices
                    (ticker, trade_date, open, high, low, close, adj_close, volume,
                     fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    # ------------------------------------------------------------------
    # Adjustment basis
    # ------------------------------------------------------------------
    def _cached_count(self, ticker: str, start: date, end: date) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM prices WHERE ticker = ? "
                "AND trade_date BETWEEN ? AND ?",
                (ticker.upper(), start.isoformat(), end.isoformat()),
            ).fetchone()
        return int(row[0])

    def _basis_window(
        self,
        ticker: str,
        start: date,
        end: date,
        cached_range: tuple[date, date] | None,
    ) -> tuple[date, date]:
        """The window to fetch so it shares enough bars with the cache.

        The window always runs through the cache's newest bar. Bars newer
        than a fetch could only be put on its basis by a factor measured
        at the fetch's far edge, and that is wrong whenever the cache
        already mixes bases: a forced refetch of APH's 2025 measured ×0.5
        against bars cached before the split and halved 2026, which had
        been cached after it. Refetching them costs a longer download in
        the same single request; the daily paths already end at or after
        the newest bar, so for them nothing changes.

        A fetch that shares no bars with the cache cannot tell whether
        the vendor's basis has moved since the cache was written — the
        old calendar-year fetch was exactly that. So the start is kept
        when the window already overlaps by :data:`MIN_BASIS_OVERLAP`
        bars, moved :data:`BASIS_ANCHOR_DAYS` before the newest bar when
        not, and moved to the cache's oldest bar as a last resort (a
        cache of fewer bars than that, or a window inside a hole that
        reaches to the end of the cache), which refetches it outright.
        """
        if cached_range is None:
            return start, end
        cmin, cmax = cached_range
        end = max(end, cmax)
        anchor = timedelta(days=BASIS_ANCHOR_DAYS)
        for s in (start, min(start, cmax - anchor)):
            if self._cached_count(ticker, s, end) >= MIN_BASIS_OVERLAP:
                return s, end
        return min(start, cmin), end

    @staticmethod
    def _consensus(ratios: list[float]) -> float | None:
        """The factor at least two of ``ratios`` agree on, else None.

        Called with the three shared bars at one edge of a fetch. One
        dissenter is tolerated because one is expected: the morning run
        caches an intraday quote that the evening refresh replaces, so
        the newest cached bar routinely differs from the vendor's
        settled close without any basis having changed.
        """
        if len(ratios) < 2:
            return None
        mid = float(np.median(ratios))
        if mid <= 0:
            return None
        agree = [r for r in ratios if abs(r / mid - 1.0) <= BASIS_RTOL]
        if len(agree) < 2:
            return None
        return float(np.mean(agree))

    def _restate_basis(
        self, ticker: str, fetched: pd.DataFrame, conn: sqlite3.Connection
    ) -> None:
        """Put the cached bars older than the fetch on its basis.

        Ratios of fetched to cached values on the dates both hold give
        the factor; ``close`` and ``adj_close`` are measured separately
        because a split moves both while a new ex-date moves only the
        adjusted series. Bars older than the fetch take the factor seen
        on its oldest shared bars — every adjustment event inside the
        window is later than them. Bars newer than the fetch are never
        rescaled: :meth:`_basis_window` runs every fetch through the
        newest cached bar, so the only ones left are bars the vendor did
        not return, and a factor from the far edge of the window would
        be wrong for them on a cache that mixes bases. A close factor also
        rescales open/high/low, divides volume (so close × volume, the
        dollars traded, is unchanged) and rescales cash dividends, which
        the vendor reports split-adjusted. With no agreed factor nothing
        is restated: a vendor disagreeing bar by bar is not a split, and
        guessing would corrupt history that is at least self-consistent.

        Runs in the caller's ``conn`` so the restatement commits or rolls
        back together with the write of the fetched bars.
        """
        if (
            fetched.empty
            or "Close" not in fetched.columns
            or not isinstance(fetched.index, pd.DatetimeIndex)
        ):
            return
        ticker = ticker.upper()
        index = fetched.index
        if index.tz is not None:
            index = index.tz_localize(None)
        first = index.min().date()
        last = index.max().date()
        cached = self._read_cached(ticker, first, last)
        if cached.empty:
            return

        new = pd.DataFrame(
            {
                "close": pd.to_numeric(fetched["Close"], errors="coerce").to_numpy(),
                "adj_close": pd.to_numeric(
                    fetched.get("Adj Close", fetched["Close"]), errors="coerce"
                ).to_numpy(),
            },
            index=index.normalize(),
        )
        new = new[~new.index.duplicated(keep="last")]
        shared = cached[["close", "adj_close"]].join(
            new, how="inner", rsuffix="_new"
        )

        def edge_factor(column: str) -> float:
            pair = shared[[column, f"{column}_new"]].dropna()
            pair = pair[(pair[column] > 0) & (pair[f"{column}_new"] > 0)]
            pair = pair.head(MIN_BASIS_OVERLAP)
            ratios = (pair[f"{column}_new"] / pair[column]).tolist()
            factor = self._consensus(ratios)
            if factor is None:
                if ratios:
                    logger.warning(
                        f"{ticker}: no agreed {column} factor between the "
                        f"cache and the fetch ({[round(r, 6) for r in ratios]}) "
                        f"— the bars before {first} are left as they are"
                    )
                return 1.0
            return factor if abs(factor - 1.0) > BASIS_RTOL else 1.0

        newer = conn.execute(
            "SELECT COUNT(*) FROM prices WHERE ticker = ? AND trade_date > ?",
            (ticker, last.isoformat()),
        ).fetchone()[0]
        if newer:
            logger.warning(
                f"{ticker}: the vendor returned nothing after {last}; {newer} "
                f"newer cached bars are kept as they are"
            )
        older = conn.execute(
            "SELECT COUNT(*) FROM prices WHERE ticker = ? AND trade_date < ?",
            (ticker, first.isoformat()),
        ).fetchone()[0]
        if not older:
            return
        f_close = edge_factor("close")
        f_adj = edge_factor("adj_close")
        if f_close == 1.0 and f_adj == 1.0:
            return
        logger.info(
            f"{ticker}: restating {older} cached bars before {first} onto the "
            f"vendor's current basis (close ×{f_close:.6g}, adj_close ×{f_adj:.6g})"
        )
        conn.execute(
            """
            UPDATE prices SET
                open = open * ?, high = high * ?, low = low * ?,
                close = close * ?, adj_close = adj_close * ?,
                volume = CAST(ROUND(volume / ?) AS INTEGER)
            WHERE ticker = ? AND trade_date < ?
            """,
            (
                f_close, f_close, f_close, f_close, f_adj, f_close,
                ticker, first.isoformat(),
            ),
        )
        if f_close != 1.0:
            conn.execute(
                "UPDATE dividends SET amount = amount * ? "
                "WHERE ticker = ? AND ex_date < ?",
                (f_close, ticker, first.isoformat()),
            )

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
        fetched from yfinance and added to the cache. A fetch is widened
        to share bars with the cache and to run through its newest bar,
        and every older cached bar is rescaled onto the fetch's basis
        when the oldest shared bars show a consistent factor (a split or
        a dividend re-adjustment), so a fetch never introduces a second
        adjustment basis — see :data:`BASIS_ANCHOR_DAYS` and
        :meth:`_restate_basis`. A cache that already mixes bases is
        healed only by a fetch that reaches back across the break.

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

        cached_range = self.cached_range(ticker)
        today = date.today()
        if not force_refresh:
            if cached_range is not None:
                cmin, cmax = cached_range
                # If the requested range is fully covered, return cache.
                if cmin <= start_d and cmax >= end_d:
                    cached = self._read_cached(ticker, start_d, end_d)
                    logger.debug(
                        f"cache hit for {ticker} {start_d}..{end_d} ({len(cached)} rows)"
                    )
                    return cached
            elif self.known_absent(ticker, end_d, today=today):
                logger.debug(
                    f"{ticker}: recorded as having no series through {end_d} — "
                    f"not re-asking yfinance"
                )
                return self._read_cached(ticker, start_d, end_d)

        # Fetch from yfinance — we always fetch the full requested range
        # (yfinance is fast enough and merging partial fetches is fragile),
        # widened where needed so it shares bars with the cache and the
        # rest of the cached history can be put on the same basis.
        fetch_start, fetch_end = self._basis_window(
            ticker, start_d, end_d, cached_range
        )
        logger.info(f"fetching {ticker} {fetch_start}..{fetch_end} from yfinance")
        df = self._fetch_yfinance(ticker, fetch_start, fetch_end)
        if not df.empty:
            # Before the write: the comparison needs the old cached rows.
            # One transaction, so a failed write cannot leave the older
            # bars rescaled next to bars still on the old basis.
            with self._connect() as conn:
                self._restate_basis(ticker, df, conn)
                n = self._write_cache(ticker, df, conn)
            d = self._write_dividends(ticker, df)
            sp = self._write_splits(ticker, df)
            logger.debug(
                f"cached {n} rows for {ticker} ({d} dividends, {sp} splits)"
            )
        elif cached_range is None:
            # Empty *without* an exception — _fetch_yfinance turns network
            # and rate-limit failures into PriceDataError, so this is the
            # vendor saying the symbol has no series, not a bad moment.
            self._record_absent(ticker, end_d, today=today)
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

    def median_dollar_volume(
        self,
        tickers: Iterable[str],
        as_of: date | datetime,
        *,
        sessions: int = 63,
    ) -> dict[str, float]:
        """Median daily dollar volume over the trailing ``sessions`` bars.

        Median rather than mean: one earnings-day spike should not make
        an otherwise untradeable name look tradeable.

        ``close * volume`` rather than a share count, because the product
        is invariant to split adjustment. This database stores
        split-adjusted prices in both price columns — NVDA reads $120.99
        on 2024-06-06 where the tape said about $1,210 — so a historical
        price is quoted in today's share terms and the matching volume
        is divided by the same factor. Multiplying them back together
        recovers the dollars that actually changed hands.

        Cache-only and one query for the whole list, for the same reason
        as :meth:`idiosyncratic_volatility`.
        """
        as_of_d = as_of.date() if isinstance(as_of, datetime) else as_of
        start = as_of_d - timedelta(days=int(sessions * 1.6) + 10)

        wanted = sorted({t.upper() for t in tickers})
        if not wanted:
            return {}
        placeholders = ",".join("?" for _ in wanted)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ticker, trade_date, close * volume AS dollars
                FROM prices
                WHERE trade_date >= ? AND trade_date <= ?
                  AND close > 0 AND volume >= 0
                  AND ticker IN ({placeholders})
                """,
                (start.isoformat(), as_of_d.isoformat(), *wanted),
            ).fetchall()

        if not rows:
            return {}
        frame = pd.DataFrame(rows, columns=["ticker", "trade_date", "dollars"])
        recent = (
            frame.sort_values("trade_date")
            .groupby("ticker")
            .tail(sessions)
        )
        medians = recent.groupby("ticker")["dollars"].median()
        return {str(t): float(v) for t, v in medians.items() if pd.notna(v)}

    def idiosyncratic_volatility(
        self,
        tickers: Iterable[str],
        as_of: date | datetime,
        *,
        market: str = "SPY",
        sessions: int = 126,
        min_sessions: int = 63,
    ) -> dict[str, float]:
        """Annualised volatility of each ticker's return not explained by
        the market, measured over the trailing ``sessions`` bars.

        One query, not one per name. A screen that ranks the whole
        universe on this at every rebalance cannot afford a round trip
        per candidate — that is what makes a full-market backtest take
        an hour — so the window is read for every ticker at once and the
        regression is done in one vectorised pass.

        Idiosyncratic rather than total volatility, and idiosyncratic
        rather than beta, because that is where the low-risk evidence
        actually sits: volatility-sorted portfolios carry the effect and
        beta-sorted ones are the weakest form of it.

        Cache-only, like :meth:`trailing_return` and
        :meth:`price_extremes`. Names with fewer than ``min_sessions``
        usable bars are absent from the result rather than present with
        a number computed from too little data — a screen cannot tell an
        undersampled estimate from a real one, and a name with six bars
        of history would otherwise look like the calmest stock in the
        market.
        """
        as_of_d = as_of.date() if isinstance(as_of, datetime) else as_of
        # Calendar days generous enough to hold ``sessions`` trading
        # days plus holidays.
        start = as_of_d - timedelta(days=int(sessions * 1.6) + 10)

        wanted = {t.upper() for t in tickers}
        wanted.add(market.upper())
        placeholders = ",".join("?" for _ in wanted)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ticker, trade_date, adj_close
                FROM prices
                WHERE trade_date >= ? AND trade_date <= ?
                  AND adj_close > 0
                  AND ticker IN ({placeholders})
                """,
                (start.isoformat(), as_of_d.isoformat(), *sorted(wanted)),
            ).fetchall()

        if not rows:
            return {}

        frame = pd.DataFrame(rows, columns=["ticker", "trade_date", "adj_close"])
        wide = frame.pivot_table(
            index="trade_date", columns="ticker", values="adj_close", aggfunc="last"
        ).sort_index()
        wide = wide.tail(sessions + 1)

        market_key = market.upper()
        if market_key not in wide.columns:
            logger.warning(f"{market_key} absent from cache at {as_of_d}")
            return {}

        returns = wide.pct_change(fill_method=None).iloc[1:]
        m = returns[market_key]
        usable = returns.notna().sum()

        m_var = float(m.var())
        if not m_var > 0:
            return {}
        beta = returns.apply(lambda col: col.cov(m)).div(m_var)
        alpha = returns.mean() - beta * float(m.mean())
        resid = returns.sub(m.to_numpy()[:, None] * beta.to_numpy(), axis=1).sub(alpha)
        vol = resid.std() * np.sqrt(252.0)

        out: dict[str, float] = {}
        for ticker, value in vol.items():
            if ticker == market_key:
                continue
            if int(usable.get(ticker, 0)) < min_sessions:
                continue
            if pd.isna(value) or value <= 0:
                continue
            out[str(ticker)] = float(value)
        return out

    def trailing_return(
        self,
        ticker: str,
        as_of: date | datetime,
        *,
        lookback_months: int,
        skip_months: int = 0,
    ) -> float | None:
        """Total return over a trailing window, or None when unknowable.

        The window runs from ``lookback_months`` back to ``skip_months``
        back, both measured from ``as_of``. The default 12-and-1 is the
        standard construction: the most recent month is dropped because
        short-horizon returns reverse, and including it mixes a reversal
        effect into a momentum signal with the opposite sign.

        Cache-only and deliberately so, for the same reason as
        :meth:`price_extremes` — a momentum score is evaluated across
        the whole universe at every rebalance, and one network call per
        candidate is not affordable.

        Returns None rather than a number whenever the answer cannot be
        trusted: no bars at either end, or an endpoint whose nearest bar
        is more than :data:`MAX_CARRY_FORWARD_DAYS` away. A name with a
        hole in its history where the window starts would otherwise be
        scored off whatever price happened to survive nearby, and a
        ranking cannot tell an invented number from a real one.
        """
        if lookback_months <= skip_months:
            raise ValueError(
                f"lookback_months ({lookback_months}) must exceed "
                f"skip_months ({skip_months})"
            )
        as_of_d = as_of.date() if isinstance(as_of, datetime) else as_of
        start = as_of_d - timedelta(days=int(30.44 * lookback_months))
        end = as_of_d - timedelta(days=int(30.44 * skip_months))

        df = self._read_cached(ticker.upper(), start - timedelta(days=10), end)
        if df.empty:
            return None
        first = self._fresh_close(df, start, max_carry_days=MAX_CARRY_FORWARD_DAYS)
        last = self._fresh_close(df, end, max_carry_days=MAX_CARRY_FORWARD_DAYS)
        if first is None or last is None or first <= 0:
            return None
        return (last / first - 1.0) * 100.0

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
           ``get_history`` stretches that year back into the cached
           history when it would not otherwise overlap it, runs it
           through the newest cached bar, and restates the older bars
           onto the fetch's basis, so a split between two fetches no
           longer leaves the years on different bases (audit PD-05).

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
                # actions=True adds the Dividends and Stock Splits
                # columns. It used to be False, so cash dividends were
                # invisible to the whole system and NAV tracked price
                # return only; splits are kept since audit item 22.
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

        # The window's Dividends column misses every ex-date outside it;
        # attach the full history so _write_dividends can fill the
        # table regardless of which windows were ever fetched (CE-3c).
        if not self._dividend_history_synced(ticker):
            history = self._fetch_dividend_history(yf, ticker)
            if history is not None:
                df.attrs[DIVIDEND_HISTORY_ATTR] = history

        return df

    @staticmethod
    def _fetch_dividend_history(yf: ModuleType, ticker: str) -> pd.Series | None:
        """The ticker's full cash-dividend history, or None if unavailable.

        A failure here never fails the price fetch: the window's own
        ``Dividends`` column is still written, and because nothing is
        recorded as synced the history is asked for again on the next
        fetch.
        """
        try:
            series = yf.Ticker(ticker).dividends
        except Exception as exc:  # yfinance raises network, HTTP and parse errors alike
            logger.warning(
                f"{ticker}: full dividend history unavailable ({exc}) — "
                f"only the dividends inside the fetched window are recorded"
            )
            return None
        if not isinstance(series, pd.Series):
            logger.warning(
                f"{ticker}: dividend history came back as "
                f"{type(series).__name__}, not a series — ignored"
            )
            return None
        return series


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
