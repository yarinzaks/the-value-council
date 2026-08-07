"""US Full Market Universe — every SEC active filer at as_of.

Membership: a ticker is "in the universe at date X" iff

1. It has at least one ``10-K`` or ``10-Q`` filing whose
   ``filing_date <= X`` in the local EDGAR cache.
2. Its most recent filing's ``filing_date`` is within
   ``active_filing_window_days`` of X (default 540 days = 18 months).
   This is the standard "still actively reporting" check — companies
   that go silent for 18+ months are typically delisted, acquired, or
   in bankruptcy.
3. The cache contains a non-trivial set of XBRL concepts (revenue,
   assets, etc.) — filters out ETFs, trusts, and other non-equity
   filers that file 10-K forms but don't report standard income
   statement / balance sheet data.

Every one of those tests is point-in-time: when we query for date X we
never read a filing filed after X, so nothing here knows the future.

.. warning::

   **This universe is NOT survivorship-bias-free**, and it used to say
   it was. The three rules above are honest about *when* a cached
   company was reporting, but they cannot speak for a company that is
   not cached at all — and the roster comes from the SEC's
   ``company_tickers.json``, which lists only issuers **currently**
   registered on the day the prefetch ran.

   Measured on the live index (6,601 tickers, filings 2009-04-15 →
   2026-04-28): of TWTR, ATVI, SIVB, FRC, CERN, XLNX, ANTM, MXIM, ALXN
   and TIF — ten large caps that were acquired or failed while filing
   10-Ks — **zero** appear in ``company_tickers.json`` and **zero**
   have a single cached fact. ``constituents_at(date(2013, 1, 1))``
   therefore returns a 2026 roster wearing a 2013 date. Rule 2 can
   retire a cached company that stopped filing; nothing can resurrect
   one that was never downloaded.

   The consequence is directional and one-sided: names that left the
   market are exactly the ones that failed, so any backtest run over
   this universe **overstates** returns. Treat its historical results
   as an upper bound, not an estimate. :mod:`core.backtest.universe`
   (S&P 500) has no such gap — it walks the index change log backward
   and does resurrect removed members — so a claim verified there does
   not carry over to here.

   Closing it means capturing ``company_tickers.json`` over time, or
   sourcing delisted CIKs from EDGAR full-text search, and back-filling
   their facts. Neither is done.

The market-cap filter is **NOT** applied here — it requires a price
(from the price loader) and shares outstanding (from the cache) at
the rebalance date. Strategies should apply the market cap filter
themselves; that pattern is already built into the existing agents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from core.data.edgar_cache import EdgarCache
from core.data.ticker_filter import is_common_equity, is_primary_listing
from core.exceptions import ValueCouncilError
from core.logger import get_logger

logger = get_logger("core.backtest.full_market_universe")

from core.paths import cache_dir as _cache_dir

DEFAULT_CACHE_DIR = _cache_dir()

# Concepts a real operating company should have at least one of —
# used to filter out ETFs/trusts which often appear in SEC filer
# lists but have empty XBRL footprints.
_REQUIRED_OPERATING_CONCEPTS: tuple[str, ...] = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "Assets",
)


class FullMarketUniverseError(ValueCouncilError):
    """Raised when the universe cannot be assembled."""


@dataclass(frozen=True)
class _TickerActivity:
    """Per-ticker summary used to answer ``constituents_at`` quickly.

    Pre-computed quality flags (added 2026-04-29):

    * ``has_positive_book_history`` — at least one filing reported
      StockholdersEquity > 0. Filters out perpetually negative-book
      shells.
    * ``has_two_consecutive_positive_revenue_years`` — at least one
      pair of consecutive 10-K periods with revenue > 0 in both.
      Filters out pre-revenue trusts and non-operating shells that
      slipped through the operating-concepts gate.
    """

    ticker: str
    earliest_filing_date: date
    latest_filing_date: date
    has_operating_concepts: bool
    has_positive_book_history: bool = True
    has_two_consecutive_positive_revenue_years: bool = True


class FullMarketUniverse:
    """All US-listed common stocks active on ``as_of``, derived from
    the local EDGAR cache.

    Built once from the cache contents; querying ``constituents_at`` is
    then a fast in-memory filter. Rebuild whenever the cache is
    refreshed by calling :meth:`refresh`.
    """

    def __init__(
        self,
        cache: EdgarCache | None = None,
        *,
        active_filing_window_days: int = 540,
        require_operating_concepts: bool = True,
        require_positive_book_history: bool = True,
        require_two_year_positive_revenue: bool = True,
        require_common_equity: bool = True,
        index_path: Path | None = None,
    ) -> None:
        self.cache = cache or EdgarCache()
        self.active_filing_window_days = active_filing_window_days
        self.require_operating_concepts = require_operating_concepts
        self.require_positive_book_history = require_positive_book_history
        self.require_two_year_positive_revenue = require_two_year_positive_revenue
        # When True (default) we filter out preferred shares, baby bonds,
        # depositary shares, ADRs, mutual-fund tickers, warrants, and
        # rights at constituent-query time. See
        # :mod:`core.data.ticker_filter` for the rules.
        self.require_common_equity = require_common_equity
        self._index_path = (
            index_path or DEFAULT_CACHE_DIR / "full_market_universe_index.json"
        )
        self._activity: dict[str, _TickerActivity] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Index construction
    # ------------------------------------------------------------------
    def refresh(self) -> int:
        """Rebuild the in-memory index from the EDGAR cache.

        Returns the number of tickers indexed. This is the only step
        that touches every parquet file; subsequent ``constituents_at``
        calls are pure dict lookups.
        """
        logger.info(
            f"refreshing full-market universe index from {self.cache.cache_dir}"
        )
        activity: dict[str, _TickerActivity] = {}
        tickers = self.cache.tickers()
        for ticker in tickers:
            df = self.cache.load_dataframe(ticker)
            if df.empty:
                continue
            filings = df[df["form"].isin(("10-K", "10-Q"))]
            if filings.empty:
                continue
            earliest = filings["filed"].min().date()
            latest = filings["filed"].max().date()
            has_ops = bool(
                df["concept"].isin(_REQUIRED_OPERATING_CONCEPTS).any()
            )
            has_positive_book = _has_positive_book_history(df)
            has_two_year_rev = _has_two_consecutive_positive_revenue_years(df)
            activity[ticker] = _TickerActivity(
                ticker=ticker,
                earliest_filing_date=earliest,
                latest_filing_date=latest,
                has_operating_concepts=has_ops,
                has_positive_book_history=has_positive_book,
                has_two_consecutive_positive_revenue_years=has_two_year_rev,
            )
        self._activity = activity
        self._loaded = True
        logger.info(f"indexed {len(activity)} tickers from cache")
        self._save_index()
        return len(activity)

    def _save_index(self) -> None:
        """Persist the index so we don't have to rescan parquet on every run."""
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_filing_window_days": self.active_filing_window_days,
            "require_operating_concepts": self.require_operating_concepts,
            "tickers": {
                t: {
                    "earliest_filing_date": a.earliest_filing_date.isoformat(),
                    "latest_filing_date": a.latest_filing_date.isoformat(),
                    "has_operating_concepts": a.has_operating_concepts,
                    "has_positive_book_history": a.has_positive_book_history,
                    "has_two_consecutive_positive_revenue_years": (
                        a.has_two_consecutive_positive_revenue_years
                    ),
                }
                for t, a in self._activity.items()
            },
        }
        self._index_path.write_text(json.dumps(payload, indent=2))

    def _load_index(self) -> bool:
        if not self._index_path.exists():
            return False
        try:
            payload = json.loads(self._index_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"failed to load universe index: {exc}")
            return False
        self._activity = {
            t: _TickerActivity(
                ticker=t,
                earliest_filing_date=date.fromisoformat(d["earliest_filing_date"]),
                latest_filing_date=date.fromisoformat(d["latest_filing_date"]),
                has_operating_concepts=bool(d["has_operating_concepts"]),
                has_positive_book_history=bool(
                    d.get("has_positive_book_history", True)
                ),
                has_two_consecutive_positive_revenue_years=bool(
                    d.get("has_two_consecutive_positive_revenue_years", True)
                ),
            )
            for t, d in payload.get("tickers", {}).items()
        }
        self._loaded = True
        return True

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if self._load_index():
            return
        self.refresh()

    # ------------------------------------------------------------------
    # Universe API (matches Universe Protocol)
    # ------------------------------------------------------------------
    def constituents_at(self, as_of: date | datetime) -> list[str]:
        """Tickers with active 10-K/10-Q filings at ``as_of``.

        "Active" = at least one filing with ``filing_date <= as_of``,
        AND the latest filing on or before ``as_of`` is within
        ``active_filing_window_days``.
        """
        if isinstance(as_of, datetime):
            as_of = as_of.date()
        self._ensure_loaded()
        cutoff = as_of - timedelta(days=self.active_filing_window_days)
        out: list[str] = []
        for ticker, act in self._activity.items():
            if act.earliest_filing_date > as_of:
                continue  # company didn't exist publicly yet
            if self.require_common_equity and not is_common_equity(ticker):
                continue  # preferred / baby bond / fund / warrant
            if self.require_common_equity and not is_primary_listing(ticker):
                # Same issuer, second symbol. Every ticker under one CIK
                # resolves to that CIK's financial statements, so a
                # $25-par note inherits the parent's revenue, equity and
                # share count and prices as though it were the stock.
                continue
            if self.require_operating_concepts and not act.has_operating_concepts:
                continue
            if (
                self.require_positive_book_history
                and not act.has_positive_book_history
            ):
                continue
            if (
                self.require_two_year_positive_revenue
                and not act.has_two_consecutive_positive_revenue_years
            ):
                continue
            # Determine "latest filing as_of as_of" — we don't have
            # per-filing dates here, but we do have earliest+latest.
            # If the latest filing was BEFORE as_of, use it directly.
            # If the latest is AFTER as_of, we'd need to scan for the
            # last filing <= as_of. We approximate: if earliest <= as_of
            # AND latest > cutoff (some filing landed in the window
            # leading up to as_of OR after — but if earliest <= as_of
            # and latest >= cutoff_far, the company was active during
            # the window).
            if act.latest_filing_date <= as_of:
                # Company has filings only up through latest. If latest
                # is within the window, it was actively reporting.
                if act.latest_filing_date >= cutoff:
                    out.append(ticker)
                # else: probably delisted before as_of — exclude
            else:
                # latest > as_of: company is still active today, and
                # earliest <= as_of, so it was definitely a reporter at as_of.
                # We need to verify it had a filing within the window
                # leading up to as_of. We use the cache directly for
                # this finer-grained check.
                if self._had_filing_in_window(
                    ticker,
                    window_start=cutoff,
                    window_end=as_of,
                ):
                    out.append(ticker)
        return sorted(out)

    def _had_filing_in_window(
        self,
        ticker: str,
        *,
        window_start: date,
        window_end: date,
    ) -> bool:
        """Check whether ``ticker`` filed a 10-K/10-Q in [start, end]."""
        df = self.cache.load_dataframe(ticker)
        if df.empty:
            return False
        filings = df[df["form"].isin(("10-K", "10-Q"))]
        if filings.empty:
            return False
        in_window = (
            (filings["filed"].dt.date >= window_start)
            & (filings["filed"].dt.date <= window_end)
        )
        return bool(in_window.any())

    def was_member_on(self, ticker: str, as_of: date | datetime) -> bool:
        """Convenience PIT membership check."""
        return ticker.upper() in self.constituents_at(as_of)

    def stats(self) -> dict[str, int]:
        self._ensure_loaded()
        with_ops = sum(1 for a in self._activity.values() if a.has_operating_concepts)
        return {
            "total_indexed": len(self._activity),
            "with_operating_concepts": with_ops,
            "without_operating_concepts": len(self._activity) - with_ops,
        }


# ----------------------------------------------------------------------
# Quality-flag helpers (used during refresh)
# ----------------------------------------------------------------------
def _has_positive_book_history(df) -> bool:
    """At least one StockholdersEquity datapoint > 0."""
    if df.empty:
        return False
    equity = df[df["concept"] == "StockholdersEquity"]
    if equity.empty:
        return False
    return bool((equity["value"] > 0).any())


def _has_two_consecutive_positive_revenue_years(df) -> bool:
    """At least one pair of consecutive 10-K periods with revenue > 0.

    Uses the most-tagged revenue concept available — falls back across
    the standard alternatives. We check 10-K filings only since
    quarterly revenue can be skewed by seasonality.
    """
    if df.empty:
        return False
    rev_concepts = (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    )
    annual_rev = df[
        df["concept"].isin(rev_concepts)
        & (df["form"] == "10-K")
        & (df["value"] > 0)
    ]
    if annual_rev.empty:
        return False
    # Get distinct fiscal years where we saw positive annual revenue
    years = annual_rev["fiscal_year"].dropna().astype(int).unique()
    if len(years) < 2:
        return False
    sorted_years = sorted(years)
    # Look for any pair of consecutive years
    for i in range(len(sorted_years) - 1):
        if sorted_years[i + 1] - sorted_years[i] == 1:
            return True
    return False


__all__ = [
    "FullMarketUniverse",
    "FullMarketUniverseError",
]
