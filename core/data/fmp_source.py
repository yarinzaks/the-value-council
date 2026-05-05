"""Financial Modeling Prep — fundamentals, screener, transcripts.

API docs: https://site.financialmodelingprep.com/developer/docs

Uses FMP's "stable" API (the legacy ``/api/v3/`` endpoints were
sunset on 2025-08-31). Auth is via ``apikey`` query parameter on
every call; the symbol is also a query parameter, not a path part.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from core.config import get_settings
from core.exceptions import DataSourceError

from .base import DataSource, retry_network
from .models import Fundamentals, Quote

_BASE = "https://financialmodelingprep.com/stable"


class FMPSource(DataSource):
    """Financial Modeling Prep REST client (stable API)."""

    name = "fmp"

    def __init__(self) -> None:
        super().__init__()
        self._api_key = get_settings().fmp_api_key.get_secret_value()
        self._session = requests.Session()

    # --- Internal HTTP -------------------------------------------------------
    @retry_network
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        params["apikey"] = self._api_key
        url = f"{_BASE}{path}"
        self.logger.debug(
            f"GET {path} params={ {k: v for k, v in params.items() if k != 'apikey'} }"
        )
        response = self._session.get(url, params=params, timeout=15)
        self._check_response(response, params.get("symbol", path))
        try:
            return response.json()
        except ValueError as exc:
            raise DataSourceError(self.name, f"non-JSON body: {response.text[:200]}") from exc

    # --- Public methods ------------------------------------------------------
    def get_quote(self, ticker: str) -> Quote:
        self._log_call("get_quote", ticker)
        symbol = ticker.upper()
        data = self._get("/quote", {"symbol": symbol})
        if not data:
            raise DataSourceError(self.name, f"empty quote for {ticker}")
        row = data[0]
        return Quote(
            ticker=row.get("symbol", symbol),
            price=float(row["price"]),
            currency="USD",
            timestamp=datetime.now(UTC),
            volume=int(row["volume"]) if row.get("volume") is not None else None,
            market_cap=float(row["marketCap"]) if row.get("marketCap") is not None else None,
            day_high=float(row["dayHigh"]) if row.get("dayHigh") is not None else None,
            day_low=float(row["dayLow"]) if row.get("dayLow") is not None else None,
            previous_close=(
                float(row["previousClose"]) if row.get("previousClose") is not None else None
            ),
        )

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        self._log_call("get_fundamentals", ticker)
        symbol = ticker.upper()
        ratios = self._get("/ratios-ttm", {"symbol": symbol}) or []
        metrics = self._get("/key-metrics-ttm", {"symbol": symbol}) or []

        r = ratios[0] if ratios else {}
        m = metrics[0] if metrics else {}

        if not (r or m):
            raise DataSourceError(self.name, f"no fundamentals for {ticker}")

        def f(d: dict[str, Any], *keys: str) -> float | None:
            for k in keys:
                if k in d and d[k] is not None:
                    try:
                        return float(d[k])
                    except (TypeError, ValueError):
                        continue
            return None

        return Fundamentals(
            ticker=symbol,
            pe_ratio=f(r, "priceToEarningsRatioTTM"),
            pb_ratio=f(r, "priceToBookRatioTTM"),
            ps_ratio=f(r, "priceToSalesRatioTTM"),
            ev_to_ebitda=f(m, "evToEBITDATTM"),
            peg_ratio=f(r, "priceToEarningsGrowthRatioTTM"),
            roe=f(m, "returnOnEquityTTM"),
            roa=f(m, "returnOnAssetsTTM"),
            roic=f(m, "returnOnInvestedCapitalTTM", "returnOnCapitalEmployedTTM"),
            gross_margin=f(r, "grossProfitMarginTTM"),
            operating_margin=f(r, "operatingProfitMarginTTM"),
            net_margin=f(r, "netProfitMarginTTM"),
            eps=f(r, "netIncomePerShareTTM"),
            book_value_per_share=f(r, "bookValuePerShareTTM"),
            revenue_per_share=f(r, "revenuePerShareTTM"),
            free_cash_flow_per_share=f(r, "freeCashFlowPerShareTTM"),
            current_ratio=f(r, "currentRatioTTM", "currentRatio"),
            quick_ratio=f(r, "quickRatioTTM"),
            debt_to_equity=f(r, "debtToEquityRatioTTM"),
            interest_coverage=f(r, "interestCoverageRatioTTM"),
            dividend_yield=f(r, "dividendYieldTTM"),
            payout_ratio=f(r, "dividendPayoutRatioTTM"),
            period="ttm",
        )

    def get_income_statement(self, ticker: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return annual income statements (most recent first)."""
        self._log_call("get_income_statement", ticker, limit=limit)
        return (
            self._get("/income-statement", {"symbol": ticker.upper(), "limit": limit}) or []
        )

    def get_balance_sheet(self, ticker: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return annual balance sheets (most recent first)."""
        self._log_call("get_balance_sheet", ticker, limit=limit)
        return (
            self._get(
                "/balance-sheet-statement", {"symbol": ticker.upper(), "limit": limit}
            )
            or []
        )

    def get_cash_flow(self, ticker: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return annual cash flow statements (most recent first)."""
        self._log_call("get_cash_flow", ticker, limit=limit)
        return (
            self._get(
                "/cash-flow-statement", {"symbol": ticker.upper(), "limit": limit}
            )
            or []
        )

    def screen(self, **filters: Any) -> list[dict[str, Any]]:
        """Run the FMP company screener with the given filters.

        Common filters: ``marketCapMoreThan``, ``peRatioLowerThan``,
        ``betaLowerThan``, ``sector``, ``industry``, ``country``,
        ``exchange``. See FMP docs for the full list.
        """
        self._log_call("screen", "<screener>", **filters)
        return self._get("/company-screener", filters) or []

    def get_earnings_transcript(self, ticker: str, year: int, quarter: int) -> str:
        """Return earnings call transcript text for a specific quarter.

        Note: this endpoint requires a paid FMP subscription on the
        stable API. Free-tier callers receive HTTP 402.
        """
        self._log_call("get_earnings_transcript", ticker, year=year, quarter=quarter)
        data = self._get(
            "/earning-call-transcript",
            {"symbol": ticker.upper(), "year": year, "quarter": quarter},
        )
        if not data:
            raise DataSourceError(
                self.name, f"no transcript for {ticker} {year}Q{quarter}"
            )
        return str(data[0].get("content", ""))


__all__ = ["FMPSource"]
