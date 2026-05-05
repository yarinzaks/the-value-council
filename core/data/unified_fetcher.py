"""Unified fetcher — single entry point combining every data source.

Strategy:
    * Sources are constructed lazily. If a source fails to build (e.g.,
      missing API key), it is logged and skipped — the rest of the
      fetcher remains functional.
    * Per-method priority lists define the fallback order (e.g., quotes
      try yfinance first, then FMP, then Finnhub).
    * Results are cached in-memory with a 1-hour TTL keyed by
      ``(method, ticker)``. The cache is process-local — restarts wipe it.
    * :meth:`enrich` returns a :class:`StockSnapshot` combining quote,
      fundamentals, news, filings, and insider transactions.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

from core.exceptions import DataSourceError
from core.logger import get_logger

from .alpha_vantage_source import AlphaVantageSource
from .base import DataSource
from .edgar_source import EdgarSource
from .finnhub_source import FinnhubSource
from .fmp_source import FMPSource
from .marketaux_source import MarketauxSource
from .models import (
    FilingExcerpt,
    Fundamentals,
    InsiderTransaction,
    NewsItem,
    Quote,
    StockSnapshot,
)
from .tase_source import TaseSource
from .yfinance_source import YFinanceSource

T = TypeVar("T")

CACHE_TTL_SECONDS: float = 3600.0  # 1 hour


class UnifiedFetcher:
    """Combines all data sources behind a single, normalized interface."""

    def __init__(self) -> None:
        self.logger = get_logger("core.data.unified_fetcher")

        self._sources: dict[str, DataSource] = {}
        self._cache: dict[tuple[str, str], tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self._cache_hits = 0
        self._cache_misses = 0

        # Try to construct each source independently. A failure (usually
        # missing config) takes only that source out of rotation.
        for cls in (
            YFinanceSource,
            FMPSource,
            FinnhubSource,
            AlphaVantageSource,
            MarketauxSource,
            EdgarSource,
            TaseSource,
        ):
            try:
                instance = cls()
                self._sources[instance.name] = instance
                self.logger.info(f"loaded source: {instance.name}")
            except Exception as exc:  # noqa: BLE001 — any setup failure is non-fatal
                self.logger.warning(f"skipping source {cls.__name__}: {exc}")

    # --- Source access -------------------------------------------------------
    @property
    def available_sources(self) -> list[str]:
        """Names of sources successfully constructed."""
        return list(self._sources)

    def source(self, name: str) -> DataSource:
        """Return a specific source by name, or raise."""
        if name not in self._sources:
            raise DataSourceError("unified", f"source {name!r} not available")
        return self._sources[name]

    # --- Cache helpers -------------------------------------------------------
    def _cache_get(self, method: str, ticker: str) -> Any | None:
        with self._lock:
            entry = self._cache.get((method, ticker.upper()))
            if entry is None:
                self._cache_misses += 1
                return None
            value, expires_at = entry
            if time.monotonic() >= expires_at:
                del self._cache[(method, ticker.upper())]
                self._cache_misses += 1
                return None
            self._cache_hits += 1
            return value

    def _cache_put(self, method: str, ticker: str, value: Any) -> None:
        with self._lock:
            self._cache[(method, ticker.upper())] = (
                value,
                time.monotonic() + CACHE_TTL_SECONDS,
            )

    def cache_stats(self) -> dict[str, int]:
        """Return total cache hits and misses since process start."""
        with self._lock:
            return {
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "size": len(self._cache),
            }

    # --- Fallback runner -----------------------------------------------------
    def _try_in_order(
        self,
        method_name: str,
        ticker: str,
        order: list[str],
        call: Callable[[DataSource], T],
    ) -> T:
        last_error: Exception | None = None
        for name in order:
            source = self._sources.get(name)
            if source is None:
                continue
            try:
                self.logger.debug(f"{method_name}({ticker}) trying {name}")
                return call(source)
            except DataSourceError as exc:
                self.logger.info(f"{name} failed for {ticker}: {exc}")
                last_error = exc
            except Exception as exc:  # noqa: BLE001 — defensive
                self.logger.warning(f"{name} unexpected error for {ticker}: {exc}")
                last_error = exc

        raise DataSourceError(
            "unified",
            f"all sources failed for {method_name}({ticker}): {last_error}",
        )

    # --- Public methods ------------------------------------------------------
    def get_quote(self, ticker: str) -> Quote:
        """Return a quote for ``ticker``, trying sources in priority order."""
        cached = self._cache_get("quote", ticker)
        if cached is not None:
            return cached

        is_israeli = ticker.upper().endswith(".TA")
        order = ["tase", "yfinance"] if is_israeli else [
            "yfinance",
            "fmp",
            "finnhub",
            "alpha_vantage",
        ]
        result = self._try_in_order("get_quote", ticker, order, lambda s: s.get_quote(ticker))
        self._cache_put("quote", ticker, result)
        return result

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        """Return fundamentals for ``ticker``, trying sources in priority order."""
        cached = self._cache_get("fundamentals", ticker)
        if cached is not None:
            return cached

        order = ["fmp", "yfinance", "finnhub", "alpha_vantage"]
        result = self._try_in_order(
            "get_fundamentals", ticker, order, lambda s: s.get_fundamentals(ticker)
        )
        self._cache_put("fundamentals", ticker, result)
        return result

    def get_news(self, ticker: str, limit: int = 25) -> list[NewsItem]:
        """Aggregate news from all news-capable sources, deduped by URL."""
        cached = self._cache_get("news", ticker)
        if cached is not None:
            return cached

        seen: set[str] = set()
        merged: list[NewsItem] = []
        for name in ("marketaux", "alpha_vantage", "finnhub"):
            source = self._sources.get(name)
            if source is None:
                continue
            try:
                if name == "marketaux":
                    items = source.get_news(ticker, limit=limit)  # type: ignore[attr-defined]
                elif name == "alpha_vantage":
                    items = source.get_news_sentiment(ticker, limit=limit)  # type: ignore[attr-defined]
                elif name == "finnhub":
                    today = time.strftime("%Y-%m-%d")
                    week_ago = time.strftime(
                        "%Y-%m-%d", time.gmtime(time.time() - 7 * 86400)
                    )
                    items = source.get_company_news(ticker, week_ago, today, limit)  # type: ignore[attr-defined]
                else:
                    continue
            except DataSourceError as exc:
                self.logger.info(f"{name} news failed for {ticker}: {exc}")
                continue

            for item in items:
                if item.url and item.url not in seen:
                    seen.add(item.url)
                    merged.append(item)

        merged.sort(key=lambda n: n.published_at, reverse=True)
        merged = merged[:limit]
        self._cache_put("news", ticker, merged)
        return merged

    def get_filings(
        self, ticker: str, form_type: str = "10-K", limit: int = 5
    ) -> list[FilingExcerpt]:
        """Return SEC filings for ``ticker`` (US only)."""
        edgar = self._sources.get("edgar")
        if edgar is None:
            return []
        try:
            return edgar.get_filings(ticker, form_type=form_type, limit=limit)  # type: ignore[attr-defined]
        except DataSourceError as exc:
            self.logger.info(f"edgar filings failed for {ticker}: {exc}")
            return []

    def get_insider_transactions(
        self, ticker: str, limit: int = 20
    ) -> list[InsiderTransaction]:
        """Return Form 4 insider transactions for ``ticker`` (US only)."""
        edgar = self._sources.get("edgar")
        if edgar is None:
            return []
        try:
            return edgar.get_form4_insider_transactions(ticker, limit=limit)  # type: ignore[attr-defined]
        except DataSourceError as exc:
            self.logger.info(f"edgar form4 failed for {ticker}: {exc}")
            return []

    def enrich(self, ticker: str) -> StockSnapshot:
        """Return a fully-populated :class:`StockSnapshot` for ``ticker``.

        Combines results from all relevant sources. Missing pieces are
        ``None`` or empty lists — :attr:`StockSnapshot.sources` records
        which sources contributed.
        """
        contributors: list[str] = []

        try:
            quote = self.get_quote(ticker)
            contributors.append("quote")
        except DataSourceError as exc:
            self.logger.warning(f"enrich({ticker}): no quote — {exc}")
            quote = None

        try:
            fundamentals = self.get_fundamentals(ticker)
            contributors.append("fundamentals")
        except DataSourceError as exc:
            self.logger.warning(f"enrich({ticker}): no fundamentals — {exc}")
            fundamentals = None

        news = self.get_news(ticker)
        if news:
            contributors.append("news")

        filings: list[FilingExcerpt] = []
        insiders: list[InsiderTransaction] = []
        if not ticker.upper().endswith(".TA"):
            filings = self.get_filings(ticker, form_type="10-K", limit=1)
            if filings:
                contributors.append("filings")
            insiders = self.get_insider_transactions(ticker, limit=10)
            if insiders:
                contributors.append("insiders")

        return StockSnapshot(
            ticker=ticker.upper(),
            quote=quote,
            fundamentals=fundamentals,
            news=news,
            insider_transactions=insiders,
            filings=filings,
            sources=contributors,
        )


__all__ = ["UnifiedFetcher", "CACHE_TTL_SECONDS"]
