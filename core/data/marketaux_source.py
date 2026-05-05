"""MarketAux — news from 5,000+ sources with sentiment.

API docs: https://www.marketaux.com/documentation
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from core.config import get_settings
from core.exceptions import DataSourceError

from .base import DataSource, retry_network
from .models import Fundamentals, NewsItem, Quote

_BASE = "https://api.marketaux.com/v1"


class MarketauxSource(DataSource):
    """MarketAux REST client (news only)."""

    name = "marketaux"

    def __init__(self) -> None:
        super().__init__()
        self._api_key = get_settings().marketaux_api_key.get_secret_value()
        self._session = requests.Session()

    @retry_network
    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "api_token": self._api_key}
        url = f"{_BASE}{path}"
        self.logger.debug(f"GET {path} params={ {k: v for k, v in params.items() if k != 'api_token'} }")
        response = self._session.get(url, params=params, timeout=15)
        self._check_response(response, params.get("symbols", path))
        try:
            return response.json()
        except ValueError as exc:
            raise DataSourceError(self.name, f"non-JSON body: {response.text[:200]}") from exc

    # MarketAux is news-only; quote/fundamentals are unsupported but we
    # implement them per the ABC contract by raising informative errors.
    def get_quote(self, ticker: str) -> Quote:
        raise DataSourceError(self.name, "MarketAux does not provide quotes")

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        raise DataSourceError(self.name, "MarketAux does not provide fundamentals")

    def get_news(self, ticker: str, limit: int = 25) -> list[NewsItem]:
        """Return news for ``ticker``.

        Args:
            ticker: Symbol, e.g. ``AAPL``.
            limit: Max items (free tier caps at 3 per call; paid more).
        """
        self._log_call("get_news", ticker, limit=limit)
        data = self._get(
            "/news/all",
            {"symbols": ticker.upper(), "limit": min(limit, 100), "language": "en"},
        )
        results: list[NewsItem] = []
        for row in (data.get("data") or [])[:limit]:
            try:
                sentiment = self._extract_sentiment(row, ticker.upper())
                results.append(
                    NewsItem(
                        title=str(row.get("title", "")),
                        url=str(row.get("url", "")),
                        published_at=datetime.fromisoformat(
                            str(row["published_at"]).replace("Z", "+00:00")
                        ),
                        source=str(row.get("source", "marketaux")),
                        sentiment=sentiment,
                        summary=row.get("description") or row.get("snippet"),
                        tickers=[
                            str(e["symbol"])
                            for e in row.get("entities", [])
                            if "symbol" in e
                        ],
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                self.logger.warning(f"skipping news row: {exc}")
        return results

    @staticmethod
    def _extract_sentiment(row: dict[str, Any], ticker: str) -> float | None:
        """Pull the per-ticker sentiment score from an entity, if present."""
        for entity in row.get("entities", []):
            if entity.get("symbol", "").upper() == ticker:
                score = entity.get("sentiment_score")
                if score is not None:
                    try:
                        return float(score)
                    except (TypeError, ValueError):
                        pass
        return None


__all__ = ["MarketauxSource"]
