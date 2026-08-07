"""Local fundamentals cache backed by Parquet files.

One Parquet file per ticker at
``data/fundamentals_cache/{ticker}.parquet``. Each row is one
:class:`XbrlFact` (namespace, concept, unit, value, period_start,
period_end, filed, form, fiscal_year, fiscal_period, accession_number).

Why Parquet:

* **Compact** — fundamentals tables are highly compressible.
* **Columnar** — pulling one concept across many years is fast.
* **Self-describing schema** — no separate DDL to maintain.
* **Cross-tool friendly** — pandas, DuckDB, R, Spark all read it.

API surface:

* :meth:`save_facts` — write a list of XbrlFact for a ticker.
* :meth:`load_facts` — read a ticker's full history.
* :meth:`has_ticker` / :meth:`tickers` / :meth:`stats` — introspection.
* :meth:`latest_value_at` — the PIT lookup primitive.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from core.exceptions import ValueCouncilError
from core.logger import get_logger

from .edgar_facts import XbrlFact, _parse_date_required

logger = get_logger("core.data.edgar_cache")

from core.paths import fundamentals_cache_dir as _fundamentals_cache_dir

DEFAULT_CACHE_DIR = _fundamentals_cache_dir()

# Parquet schema — matches XbrlFact fields. Date columns stored as
# strings for portability (Parquet's date32/64 handling varies by
# tool); we re-parse on load.
_SCHEMA = pa.schema(
    [
        ("concept", pa.string()),
        ("namespace", pa.string()),
        ("unit", pa.string()),
        ("value", pa.float64()),
        ("period_start", pa.string()),  # nullable ISO date
        ("period_end", pa.string()),  # ISO date
        ("filed", pa.string()),  # ISO date
        ("form", pa.string()),
        ("fiscal_year", pa.int32()),
        ("fiscal_period", pa.string()),
        ("accession_number", pa.string()),
    ]
)


class EdgarCacheError(ValueCouncilError):
    """Raised on cache I/O failure."""


@dataclass(frozen=True)
class CacheStats:
    """High-level cache health report."""

    ticker_count: int
    total_facts: int
    total_size_bytes: int

    def total_size_mb(self) -> float:
        return self.total_size_bytes / (1024 * 1024)


class EdgarCache:
    """Per-ticker Parquet cache of XBRL facts."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        memory_cache_size: int = 16384,
    ) -> None:
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # In-memory LRU of parsed DataFrames keyed by ticker. The
        # full-market backtest does ~6,000 tickers × 16 rebalances =
        # ~96,000 reads; without this cache each read parses the
        # parquet file (~10ms × 96,000 = 16 minutes of pure I/O).
        # With the cache, the second pass through the universe at
        # any rebalance is in-memory.
        self._df_cache: dict[str, pd.DataFrame] = {}
        self._df_cache_order: list[str] = []
        self._df_cache_size = memory_cache_size

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------
    def path_for(self, ticker: str) -> Path:
        return self.cache_dir / f"{ticker.upper()}.parquet"

    def has_ticker(self, ticker: str) -> bool:
        return self.path_for(ticker).exists()

    def tickers(self) -> list[str]:
        # Filter out macOS AppleDouble companion files (``._FOO.parquet``).
        # If the cache tarball was created on macOS without
        # ``COPYFILE_DISABLE=1``, every real ``FOO.parquet`` gets a
        # sibling ``._FOO.parquet`` of resource-fork bytes — pyarrow
        # crashes when it tries to parse one.
        return sorted(
            p.stem.upper()
            for p in self.cache_dir.glob("*.parquet")
            if not p.name.startswith("._")
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save_facts(self, ticker: str, facts: list[XbrlFact]) -> None:
        """Write a ticker's full fact history to Parquet."""
        if not facts:
            logger.debug(f"no facts to save for {ticker}; skipping")
            return
        table = self._facts_to_table(facts)
        path = self.path_for(ticker)
        try:
            pq.write_table(table, path, compression="snappy")
        except Exception as exc:
            raise EdgarCacheError(f"write_table failed for {ticker}: {exc}") from exc
        logger.debug(f"cached {len(facts)} facts for {ticker} → {path.name}")

    def load_facts(self, ticker: str) -> list[XbrlFact]:
        """Read a ticker's full fact history from Parquet."""
        path = self.path_for(ticker)
        if not path.exists():
            return []
        try:
            table = pq.read_table(path)
        except Exception as exc:
            raise EdgarCacheError(f"read_table failed for {ticker}: {exc}") from exc
        return self._table_to_facts(table)

    def load_dataframe(self, ticker: str) -> pd.DataFrame:
        """Read a ticker's facts as a pandas DataFrame.

        Memoized — repeated calls for the same ticker hit an in-memory
        LRU. Date columns are parsed into ``datetime64[ns]``.
        """
        ticker_u = ticker.upper()
        cached = self._df_cache.get(ticker_u)
        if cached is not None:
            # Move to most-recently-used end of LRU
            try:
                self._df_cache_order.remove(ticker_u)
            except ValueError:
                pass
            self._df_cache_order.append(ticker_u)
            return cached
        path = self.path_for(ticker_u)
        if not path.exists():
            return pd.DataFrame()
        try:
            df = pq.read_table(path).to_pandas()
        except Exception as exc:
            raise EdgarCacheError(f"to_pandas failed for {ticker}: {exc}") from exc
        for col in ("period_start", "period_end", "filed"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        # Evict if cache full
        while len(self._df_cache_order) >= self._df_cache_size:
            evicted = self._df_cache_order.pop(0)
            self._df_cache.pop(evicted, None)
        self._df_cache[ticker_u] = df
        self._df_cache_order.append(ticker_u)
        return df

    def clear_memory_cache(self) -> None:
        """Free all in-memory DataFrames — useful in long-running tools."""
        self._df_cache.clear()
        self._df_cache_order.clear()

    # ------------------------------------------------------------------
    # PIT primitive
    # ------------------------------------------------------------------
    def latest_value_at(
        self,
        ticker: str,
        concept: str,
        as_of: date | datetime,
        *,
        namespace: str = "us-gaap",
        forms: tuple[str, ...] | None = ("10-K", "10-Q"),
        prefer_annual: bool = False,
        duration_days: tuple[int, int] | None = None,
        units: tuple[str, ...] | None = None,
    ) -> XbrlFact | None:
        """Return the most recent reported value for ``concept`` known
        on ``as_of``.

        "Most recent" means the highest ``period_end`` among facts
        whose ``filed <= as_of``. Ties broken by ``filed`` desc.

        Args:
            ticker: Ticker symbol.
            concept: XBRL concept name (e.g., ``"OperatingIncomeLoss"``).
            as_of: PIT date. Only facts filed on or before this date
                are considered.
            namespace: ``"us-gaap"`` or ``"dei"`` (for shares
                outstanding).
            forms: Restrict to specific filing forms; ``None`` =
                any form.
            prefer_annual: When True, prefer 10-K facts even if a
                later 10-Q exists. Useful for snapshot metrics like
                annual revenue or shares outstanding at year-end.
            duration_days: Inclusive ``(min, max)`` window on the fact's
                reporting period, in days. Required for flow concepts —
                revenue, earnings, cash flow — because a 10-Q's
                year-to-date figure carries a *later* ``period_end``
                than the last 10-K's annual figure and would otherwise
                win the sort, handing a three- or nine-month number to
                a caller that asked for a year. Instant facts
                (balance-sheet items, which carry no ``period_start``)
                never satisfy a window, so pass ``None`` for those.
            units: Restrict to these XBRL units. A foreign private issuer
                files in its own currency, and those figures are otherwise
                divided straight into a USD share price — an Enbridge-class
                filer reporting CAD lands ~25% cheaper on every multiple
                than it is. There is no point-in-time FX series here, so
                the only honest answer is to reject rather than translate.
        """
        df = self.load_dataframe(ticker)
        if df.empty:
            return None
        as_of_d = (
            as_of.date() if isinstance(as_of, datetime) else as_of
        )
        mask = (df["concept"] == concept) & (df["namespace"] == namespace)
        if forms is not None:
            mask &= df["form"].isin(forms)
        if units is not None:
            mask &= df["unit"].isin(units)
        mask &= df["filed"].dt.date <= as_of_d
        sub = df[mask]
        if sub.empty:
            return None
        if duration_days is not None:
            lo, hi = duration_days
            # NaT period_start yields NaN days, and between() drops it —
            # which is what we want for instant facts.
            span = (sub["period_end"] - sub["period_start"]).dt.days
            sub = sub[span.between(lo, hi)]
            if sub.empty:
                return None
        if prefer_annual:
            annual = sub[sub["form"] == "10-K"]
            if not annual.empty:
                sub = annual
        sub = sub.sort_values(["period_end", "filed"], ascending=[False, False])
        row = sub.iloc[0]
        return _row_to_fact(row)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def stats(self) -> CacheStats:
        files = [
            p for p in self.cache_dir.glob("*.parquet")
            if not p.name.startswith("._")
        ]
        total_facts = 0
        total_size = 0
        for f in files:
            total_size += f.stat().st_size
            try:
                meta = pq.read_metadata(f)
                total_facts += meta.num_rows
            except Exception as exc:
                logger.debug(f"could not read metadata for {f}: {exc}")
        return CacheStats(
            ticker_count=len(files),
            total_facts=total_facts,
            total_size_bytes=total_size,
        )

    # ------------------------------------------------------------------
    # Internal serialization helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _facts_to_table(facts: list[XbrlFact]) -> pa.Table:
        rows: dict[str, list] = {name: [] for name in _SCHEMA.names}
        for f in facts:
            rows["concept"].append(f.concept)
            rows["namespace"].append(f.namespace)
            rows["unit"].append(f.unit)
            rows["value"].append(f.value)
            rows["period_start"].append(
                f.period_start.isoformat() if f.period_start else None
            )
            rows["period_end"].append(f.period_end.isoformat())
            rows["filed"].append(f.filed.isoformat())
            rows["form"].append(f.form)
            rows["fiscal_year"].append(f.fiscal_year)
            rows["fiscal_period"].append(f.fiscal_period)
            rows["accession_number"].append(f.accession_number)
        return pa.table(rows, schema=_SCHEMA)

    @staticmethod
    def _table_to_facts(table: pa.Table) -> list[XbrlFact]:
        df = table.to_pandas()
        return [_row_to_fact(row) for _, row in df.iterrows()]


def _row_to_fact(row: pd.Series) -> XbrlFact:
    period_start: date | None
    ps = row.get("period_start")
    if pd.isna(ps) or ps is None or ps == "":
        period_start = None
    else:
        period_start = (
            ps.date() if isinstance(ps, pd.Timestamp) else _parse_date_required(ps)
        )
    pe = row["period_end"]
    period_end = (
        pe.date() if isinstance(pe, pd.Timestamp) else _parse_date_required(pe)
    )
    fd = row["filed"]
    filed = fd.date() if isinstance(fd, pd.Timestamp) else _parse_date_required(fd)
    fy = row.get("fiscal_year")
    return XbrlFact(
        concept=str(row["concept"]),
        namespace=str(row["namespace"]),
        unit=str(row["unit"]),
        value=float(row["value"]),
        period_start=period_start,
        period_end=period_end,
        filed=filed,
        form=str(row["form"]),
        fiscal_year=None if pd.isna(fy) else int(fy),
        fiscal_period=str(row["fiscal_period"]) if not pd.isna(row["fiscal_period"]) else None,
        accession_number=str(row["accession_number"]),
    )


__all__ = ["CacheStats", "EdgarCache", "EdgarCacheError"]
