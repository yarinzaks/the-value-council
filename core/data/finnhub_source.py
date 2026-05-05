"""Finnhub — real-time quotes plus company news.

API docs: https://finnhub.io/docs/api

Auth via the ``X-Finnhub-Token`` header. Free tier: 60 calls/min,
which is plenty for our cadence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from core.config import get_settings
from core.exceptions import DataSourceError

from .base import DataSource, retry_network
from .models import Fundamentals, NewsItem, Quote

_BASE = "https://finnhub.io/api/v1"


class FinnhubSource(DataSource):
    """Finnhub REST client."""

    name = "finnhub"

    def __init__(self) -> None:
        super().__init__()
        self._api_key = get_settings().finnhub_api_key.get_secret_value()
        self._session = requests.Session()
        self._session.headers["X-Finnhub-Token"] = self._api_key

    @retry_network
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{_BASE}{path}"
        self.logger.debug(f"GET {path} params={params}")
        response = self._session.get(url, params=params or {}, timeout=15)
        self._check_response(response, params.get("symbol", path) if params else path)
        try:
            return response.json()
        except ValueError as exc:
            raise DataSourceError(self.name, f"non-JSON body: {response.text[:200]}") from exc

    def get_quote(self, ticker: str) -> Quote:
        self._log_call("get_quote", ticker)
        data = self._get("/quote", {"symbol": ticker.upper()})
        if not data or data.get("c") in (None, 0):
            raise DataSourceError(self.name, f"empty quote for {ticker}")
        return Quote(
            ticker=ticker.upper(),
            price=float(data["c"]),
            currency="USD",
            timestamp=datetime.fromtimestamp(int(data.get("t", 0)), tz=UTC)
            if data.get("t")
            else datetime.now(UTC),
            day_high=float(data["h"]) if data.get("h") is not None else None,
            day_low=float(data["l"]) if data.get("l") is not None else None,
            previous_close=float(data["pc"]) if data.get("pc") is not None else None,
        )

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        self._log_call("get_fundamentals", ticker)
        data = self._get(
            "/stock/metric",
            {"symbol": ticker.upper(), "metric": "all"},
        )
        m: dict[str, Any] = (data or {}).get("metric", {})
        if not m:
            raise DataSourceError(self.name, f"no fundamentals for {ticker}")

        def f(*keys: str) -> float | None:
            for k in keys:
                if k in m and m[k] is not None:
                    try:
                        return float(m[k])
                    except (TypeError, ValueError):
                        continue
            return None

        return Fundamentals(
            ticker=ticker.upper(),
            pe_ratio=f("peTTM", "peNormalizedAnnual"),
            pb_ratio=f("pbAnnual", "pbQuarterly"),
            ps_ratio=f("psTTM"),
            ev_to_ebitda=f("currentEv/freeCashFlowTTM"),
            roe=f("roeTTM", "roeRfy"),
            roa=f("roaTTM", "roaRfy"),
            roic=f("roicAnnual"),
            gross_margin=f("grossMarginTTM", "grossMarginAnnual"),
            operating_margin=f("operatingMarginTTM", "operatingMarginAnnual"),
            net_margin=f("netProfitMarginTTM", "netProfitMarginAnnual"),
            eps=f("epsTTM", "epsAnnual"),
            book_value_per_share=f("bookValuePerShareAnnual"),
            current_ratio=f("currentRatioAnnual", "currentRatioQuarterly"),
            quick_ratio=f("quickRatioAnnual", "quickRatioQuarterly"),
            debt_to_equity=f("totalDebt/totalEquityAnnual"),
            dividend_yield=f("dividendYieldIndicatedAnnual"),
            payout_ratio=f("payoutRatioAnnual"),
            period="ttm",
        )

    def get_company_news(
        self, ticker: str, from_date: str, to_date: str, limit: int = 50
    ) -> list[NewsItem]:
        """Return news articles for ``ticker`` between ``from_date`` and ``to_date``.

        Date format: ``YYYY-MM-DD``.
        """
        self._log_call("get_company_news", ticker, from_date=from_date, to_date=to_date)
        data = self._get(
            "/company-news",
            {"symbol": ticker.upper(), "from": from_date, "to": to_date},
        )
        results: list[NewsItem] = []
        for row in (data or [])[:limit]:
            try:
                results.append(
                    NewsItem(
                        title=str(row.get("headline", "")),
                        url=str(row.get("url", "")),
                        published_at=datetime.fromtimestamp(int(row["datetime"]), tz=UTC),
                        source=str(row.get("source", "finnhub")),
                        summary=row.get("summary"),
                        tickers=[ticker.upper()],
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                self.logger.warning(f"skipping news row: {exc}")
        return results


__all__ = ["FinnhubSource"]
