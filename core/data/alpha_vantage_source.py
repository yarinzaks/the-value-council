"""Alpha Vantage — quotes, fundamentals, news with sentiment.

API docs: https://www.alphavantage.co/documentation/

Free tier is heavily throttled (5 calls/min, 25/day on basic free).
We self-throttle with a minimum 12-second gap between calls.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from threading import Lock
from typing import Any

import requests

from core.config import get_settings
from core.exceptions import DataSourceError, RateLimitError

from .base import DataSource, retry_network
from .models import Fundamentals, NewsItem, Quote

_BASE = "https://www.alphavantage.co/query"
_MIN_INTERVAL_SECONDS = 12.0  # 5 calls/min ceiling


class AlphaVantageSource(DataSource):
    """Alpha Vantage REST client with built-in rate limiting."""

    name = "alpha_vantage"

    _last_call_at: float = 0.0
    _lock: Lock = Lock()

    def __init__(self) -> None:
        super().__init__()
        self._api_key = get_settings().alpha_vantage_key.get_secret_value()
        self._session = requests.Session()

    def _throttle(self) -> None:
        """Sleep just enough to respect the 5-call/min limit."""
        with self._lock:
            elapsed = time.monotonic() - AlphaVantageSource._last_call_at
            if elapsed < _MIN_INTERVAL_SECONDS:
                time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
            AlphaVantageSource._last_call_at = time.monotonic()

    @retry_network
    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        self._throttle()
        params = {**params, "apikey": self._api_key}
        self.logger.debug(
            f"GET function={params.get('function')} symbol={params.get('symbol')}"
        )
        response = self._session.get(_BASE, params=params, timeout=15)
        self._check_response(response, params.get("symbol", "?"))
        try:
            data = response.json()
        except ValueError as exc:
            raise DataSourceError(self.name, f"non-JSON body: {response.text[:200]}") from exc

        if "Note" in data or "Information" in data:
            # Alpha Vantage returns a 200 with a "Note" when throttled.
            msg = data.get("Note") or data.get("Information")
            raise RateLimitError(self.name, str(msg))
        if "Error Message" in data:
            raise DataSourceError(self.name, str(data["Error Message"]))
        return data

    def get_quote(self, ticker: str) -> Quote:
        self._log_call("get_quote", ticker)
        data = self._get({"function": "GLOBAL_QUOTE", "symbol": ticker.upper()})
        row = data.get("Global Quote", {})
        price_str = row.get("05. price")
        if not price_str:
            raise DataSourceError(self.name, f"empty quote for {ticker}")
        return Quote(
            ticker=ticker.upper(),
            price=float(price_str),
            currency="USD",
            timestamp=datetime.now(UTC),
            volume=int(row["06. volume"]) if row.get("06. volume") else None,
            previous_close=(
                float(row["08. previous close"]) if row.get("08. previous close") else None
            ),
        )

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        self._log_call("get_fundamentals", ticker)
        data = self._get({"function": "OVERVIEW", "symbol": ticker.upper()})
        if not data or data.get("Symbol") is None:
            raise DataSourceError(self.name, f"no overview for {ticker}")

        def f(key: str) -> float | None:
            value = data.get(key)
            if value in (None, "None", "-", ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        return Fundamentals(
            ticker=ticker.upper(),
            pe_ratio=f("PERatio"),
            forward_pe=f("ForwardPE"),
            pb_ratio=f("PriceToBookRatio"),
            ps_ratio=f("PriceToSalesRatioTTM"),
            ev_to_ebitda=f("EVToEBITDA"),
            peg_ratio=f("PEGRatio"),
            roe=f("ReturnOnEquityTTM"),
            roa=f("ReturnOnAssetsTTM"),
            gross_margin=f("GrossProfitTTM"),  # raw value, not ratio
            operating_margin=f("OperatingMarginTTM"),
            net_margin=f("ProfitMargin"),
            eps=f("EPS"),
            book_value_per_share=f("BookValue"),
            revenue=f("RevenueTTM"),
            ebitda=f("EBITDA"),
            dividend_yield=f("DividendYield"),
            payout_ratio=f("PayoutRatio"),
            shares_outstanding=f("SharesOutstanding"),
            period="ttm",
        )

    def get_news_sentiment(self, ticker: str, limit: int = 50) -> list[NewsItem]:
        """Return news articles with sentiment scores for ``ticker``."""
        self._log_call("get_news_sentiment", ticker, limit=limit)
        data = self._get(
            {
                "function": "NEWS_SENTIMENT",
                "tickers": ticker.upper(),
                "limit": str(limit),
            }
        )
        results: list[NewsItem] = []
        for row in (data.get("feed") or [])[:limit]:
            try:
                results.append(
                    NewsItem(
                        title=str(row.get("title", "")),
                        url=str(row.get("url", "")),
                        published_at=self._parse_av_date(row.get("time_published", "")),
                        source=str(row.get("source", "alpha_vantage")),
                        sentiment=float(row.get("overall_sentiment_score", 0.0)),
                        summary=row.get("summary"),
                        tickers=[ticker.upper()],
                    )
                )
            except (ValueError, TypeError) as exc:
                self.logger.warning(f"skipping news row: {exc}")
        return results

    @staticmethod
    def _parse_av_date(s: str) -> datetime:
        """Parse Alpha Vantage's ``YYYYMMDDTHHMMSS`` timestamps."""
        return datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)


__all__ = ["AlphaVantageSource"]
