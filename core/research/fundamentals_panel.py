"""Point-in-time fundamentals for every (date, ticker), built once.

Where the numbers come from
~~~~~~~~~~~~~~~~~~~~~~~~~~~

:class:`~core.data.fundamentals_fetcher.FundamentalsFetcher`, reading
the local XBRL parquet cache. Not
:class:`~core.backtest.point_in_time.PointInTimeLoader`, and the
difference is not a matter of taste: the ``filings`` table that loader
depends on is populated lazily by whatever has previously asked, so it
is a query log rather than an index. It holds 130 filings for AAPL and
**five** for MSFT. Asking it for MSFT's fundamentals across 2011-2018
returns nothing at all, while the parquet for the same ticker holds
31,574 facts going back to 2009.

The fetcher resolves concept chains, separates balance-sheet stocks
from income-statement flows, requires a full-year duration on flows and
bounds how stale a fact may be. That logic is subtle and already
tested; re-deriving it here to save a little time would be trading a
known-good implementation for an unaudited one, in the exact place
where a mistake becomes look-ahead bias.

Verified point-in-time: MSFT at 2011-01-31 resolves to the filing of
2011-01-27 with total assets of $86.1bn, and AAPL at the same date to
the 10-K of 2010-10-27 with $75.2bn. Both filings precede the asking
date, which is the property that matters.

Share counts
~~~~~~~~~~~~

Restated through :mod:`core.research.splits` before they leave this
module, because a raw count multiplied by an adjusted price is wrong by
the cumulative split factor — see that module for what that does to a
size screen.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date

import pandas as pd

from core.data.edgar_cache import EdgarCache
from core.data.fundamentals_fetcher import (
    FundamentalsFetcher,
    FundamentalsFetcherConfig,
)
from core.logger import get_logger
from core.paths import DATA_ROOT
from core.research.splits import adjusted_shares

logger = get_logger("core.research.fundamentals_panel")

#: Fields pulled per (ticker, date). A deliberate subset of what the
#: fetcher can return — every one of these feeds a factor below, and
#: each extra field costs a concept-chain walk per lookup.
FIELDS: tuple[str, ...] = (
    "operating_income",
    "total_assets",
    "total_equity",
    "total_liabilities",
    "revenue",
    "net_income",
    "cash_and_equivalents",
    "total_debt",
    "shares_outstanding",
    "operating_cash_flow",
    "capex",
)

#: XBRL concepts carrying a share count, best first. The ``dei``
#: cover-page fact is preferred: it is dated at the filing rather than
#: at the period end, so it is the freshest count the filing contains.
SHARE_CONCEPTS: tuple[str, ...] = (
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding",
)

#: Fundamentals are sampled quarterly and forward-filled to the monthly
#: rebalance grid. A company files four times a year, so sampling
#: monthly would repeat the same filing three times over and triple the
#: build for nothing.
FUNDAMENTAL_FREQ = "QE"


@dataclass(frozen=True)
class FundamentalsSpec:
    """Which tickers to measure, and over what stretch."""

    tickers: tuple[str, ...]
    start: date
    end: date
    workers: int = max(1, (os.cpu_count() or 4) - 2)

    def __post_init__(self) -> None:
        if not self.tickers:
            raise ValueError("no tickers given")
        if self.start >= self.end:
            raise ValueError(f"start {self.start} is not before end {self.end}")

    def sample_dates(self) -> list[date]:
        rng = pd.date_range(self.start, self.end, freq=FUNDAMENTAL_FREQ)
        return [d.date() for d in rng]


def _share_history(cache: EdgarCache, ticker: str) -> pd.Series:
    """Split-restated share count indexed by filing date, or empty."""
    try:
        facts = cache.load_dataframe(ticker)
    # A missing or corrupt parquet is a fact about this ticker, not a
    # reason to end a build that spans four thousand of them.
    except Exception:
        return pd.Series(dtype="float64")
    if facts is None or len(facts) == 0:
        return pd.Series(dtype="float64")

    rows = facts[facts["concept"].isin(SHARE_CONCEPTS)]
    if len(rows) == 0:
        return pd.Series(dtype="float64")

    # One count per filing date, and it has to be *that filing's* count.
    # A 10-K restates the same concept for every year in its comparative
    # columns — MSFT's 2015 filing tags CommonStockSharesOutstanding at
    # 8.38bn (2012), 8.33bn (2013), 8.24bn (2014) and 8.03bn (2015) —
    # so taking the largest silently reaches three years into the past
    # and reads a share count that was already stale when filed.
    #
    # The dei cover-page fact has no such problem: it is dated at the
    # filing itself and there is exactly one. Prefer it, and fall back
    # to the newest period_end only where it is absent.
    preference = {name: i for i, name in enumerate(SHARE_CONCEPTS)}
    ordered = rows.assign(_rank=rows["concept"].map(preference)).sort_values(
        ["filed", "_rank", "period_end"], ascending=[True, False, True]
    )
    by_filing = ordered.groupby("filed")["value"].last()
    return adjusted_shares(by_filing)


#: Tickers handed to a worker in one task.
#:
#: Not a tuning knob — a fix. Submitting one ticker per task rebuilds
#: :class:`EdgarCache` and :class:`FundamentalsFetcher` every time, and
#: across 3,577 tickers that construction cost came to dominate the
#: sweep: the measured rate implied three hours against the seventy-odd
#: minutes the actual lookups account for. A worker now builds them
#: once and reuses them for a whole chunk.
CHUNK_SIZE = 40


def _measure_chunk(args: tuple[tuple[str, ...], list[date]]) -> pd.DataFrame:
    """Every field for a chunk of tickers across every sample date."""
    tickers, dates = args
    cache = EdgarCache()
    fetcher = FundamentalsFetcher(
        cache=cache,
        client=None,
        config=FundamentalsFetcherConfig(populate_cache_on_miss=False),
    )

    records: list[dict[str, object]] = []
    for ticker in tickers:
        shares = _share_history(cache, ticker)
        for when in dates:
            try:
                values, meta = fetcher.get_all_fields(ticker, when)
            # Same reasoning as _share_history: one unreadable ticker is
            # a gap in the panel, not a failed build.
            except Exception:
                continue
            if meta is None:
                continue

            row: dict[str, object] = {"date": when, "ticker": ticker}
            for field in FIELDS:
                row[field] = values.get(field)
            row["filing_date"] = meta.filing_date
            row["form_type"] = meta.form_type

            known = shares[shares.index <= when] if len(shares) else shares
            row["shares_split_adjusted"] = (
                float(known.iloc[-1]) if len(known) else None
            )
            records.append(row)

    return pd.DataFrame.from_records(records)


def build_fundamentals_panel(spec: FundamentalsSpec) -> pd.DataFrame:
    """One row per ``(sample_date, ticker)`` with point-in-time fields."""
    dates = spec.sample_dates()
    logger.info(
        f"measuring {len(spec.tickers):,} tickers over {len(dates)} quarter-ends "
        f"({spec.start} → {spec.end}) on {spec.workers} workers"
    )

    chunks = [
        tuple(spec.tickers[i : i + CHUNK_SIZE])
        for i in range(0, len(spec.tickers), CHUNK_SIZE)
    ]
    payloads = [(chunk, dates) for chunk in chunks]
    frames: list[pd.DataFrame] = []
    done = 0
    with ProcessPoolExecutor(max_workers=spec.workers) as pool:
        futures = {pool.submit(_measure_chunk, p): p[0] for p in payloads}
        for future in as_completed(futures):
            done += len(futures[future])
            try:
                frame = future.result()
            except Exception as exc:
                logger.debug(f"{futures[future][:3]}...: {exc}")
                continue
            if len(frame):
                frames.append(frame)
            logger.info(f"  {done}/{len(spec.tickers)} tickers")

    if not frames:
        raise RuntimeError("no fundamentals resolved for any ticker")

    panel = pd.concat(frames, ignore_index=True)
    panel = panel.set_index(["date", "ticker"]).sort_index()
    logger.info(
        f"fundamentals panel: {len(panel):,} rows, "
        f"{panel.index.get_level_values('ticker').nunique():,} tickers"
    )
    return panel


def panel_path(name: str) -> os.PathLike[str]:
    """Where a built panel is cached between sessions."""
    out = DATA_ROOT / "research"
    out.mkdir(parents=True, exist_ok=True)
    return out / f"{name}.parquet"


__all__ = [
    "FIELDS",
    "FundamentalsSpec",
    "build_fundamentals_panel",
    "panel_path",
]
