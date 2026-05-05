"""Tel Aviv Stock Exchange (TASE) Open API client.

API docs: https://openapi.tase.co.il

TASE uses OAuth 2.0 client credentials. Tokens last ~24h; we cache and
re-fetch on expiry. Endpoints are organized into ``/v1/{section}/`` paths.

This source is **optional** — if ``TASE_CLIENT_ID``/``TASE_CLIENT_SECRET``
are unset, the source raises :class:`DataSourceError` on use rather than
silently returning empty data, so callers can route past it cleanly.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import requests

from core.config import get_settings
from core.exceptions import DataSourceError

from .base import DataSource, retry_network
from .models import Fundamentals, Quote

_BASE = "https://openapigw.tase.co.il/tase/prod"
_TOKEN_URL = "https://openapigw.tase.co.il/tase/prod/oauth/oauth2/token"


class TaseSource(DataSource):
    """TASE Open API client.

    Israeli tickers can be supplied either as numeric securityIDs (TASE's
    native identifier) or as Yahoo-style ``XXXX.TA`` symbols; the latter
    are stripped of the suffix before lookup.
    """

    name = "tase"

    def __init__(self) -> None:
        super().__init__()
        settings = get_settings()
        if not settings.tase_enabled:
            self.logger.info("TASE disabled — credentials not configured")
            self._enabled = False
            self._client_id = ""
            self._client_secret = ""
        else:
            self._enabled = True
            self._client_id = settings.tase_client_id.get_secret_value()  # type: ignore[union-attr]
            self._client_secret = settings.tase_client_secret.get_secret_value()  # type: ignore[union-attr]

        self._session = requests.Session()
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # --- Auth ----------------------------------------------------------------
    @retry_network
    def _refresh_token(self) -> None:
        """Fetch a fresh OAuth 2.0 access token."""
        if not self._enabled:
            raise DataSourceError(self.name, "TASE credentials not configured")
        self.logger.debug("refreshing TASE OAuth token")
        response = self._session.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        self._check_response(response, "<auth>")
        body = response.json()
        self._token = str(body["access_token"])
        # Expire 60s early to avoid edge cases.
        self._token_expires_at = time.monotonic() + int(body.get("expires_in", 3600)) - 60

    def _ensure_token(self) -> str:
        if self._token is None or time.monotonic() >= self._token_expires_at:
            self._refresh_token()
        assert self._token is not None
        return self._token

    @retry_network
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self._ensure_token()
        url = f"{_BASE}{path}"
        self.logger.debug(f"GET {path} params={params}")
        response = self._session.get(
            url,
            params=params or {},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        self._check_response(response, str(params))
        try:
            return response.json()
        except ValueError as exc:
            raise DataSourceError(self.name, f"non-JSON body: {response.text[:200]}") from exc

    # --- Public API ----------------------------------------------------------
    @staticmethod
    def _normalize_ticker(ticker: str) -> str:
        return ticker.upper().removesuffix(".TA").strip()

    def get_quote(self, ticker: str) -> Quote:
        self._log_call("get_quote", ticker)
        symbol = self._normalize_ticker(ticker)
        # TASE end-of-day pricing is exposed via /api/v1/info/...; the
        # exact path differs per package. We use the basic securities
        # endpoint and trust the schema.
        data = self._get("/api/content/api/Indices/eng/SecurityHistoryData", {"securityId": symbol})
        items = data.get("Items") or data.get("items") or []
        if not items:
            raise DataSourceError(self.name, f"no quote rows for {ticker}")
        latest = items[0]
        price = float(latest.get("ClosingPrice") or latest.get("Price") or 0.0)
        if price == 0.0:
            raise DataSourceError(self.name, f"empty price for {ticker}")
        return Quote(
            ticker=symbol + ".TA",
            price=price,
            currency="ILS",
            timestamp=datetime.now(UTC),
            volume=int(latest["Volume"]) if latest.get("Volume") is not None else None,
            previous_close=(
                float(latest["PreviousClose"]) if latest.get("PreviousClose") is not None else None
            ),
        )

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        # TASE Open API exposes minimal fundamentals on the free tier.
        # We return a near-empty model to satisfy the contract.
        raise DataSourceError(
            self.name,
            "TASE Open API fundamentals require an enterprise package",
        )

    def get_securities_list(self) -> list[dict[str, Any]]:
        """Return the list of all securities currently traded on TASE."""
        self._log_call("get_securities_list", "<all>")
        data = self._get("/api/content/api/BasicData/eng/BasicSecuritiesData", {})
        return list(data.get("Items") or data.get("items") or [])


__all__ = ["TaseSource"]
