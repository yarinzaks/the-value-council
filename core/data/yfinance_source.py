"""yfinance data source — global market data, no API key required.

Used as the primary fallback for quotes and as a complement for
fundamentals. Israeli stocks use the ``.TA`` suffix (e.g., ``TEVA.TA``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import yfinance as yf

from core.exceptions import DataSourceError

from .base import DataSource, retry_network
from .models import Fundamentals, Quote


class YFinanceSource(DataSource):
    """Yahoo Finance via the :mod:`yfinance` library.

    No authentication required. Reasonably reliable for US and major
    international tickers; adds ``.TA`` suffix routing for Tel Aviv.
    """

    name = "yfinance"

    @staticmethod
    def _normalize_ticker(ticker: str) -> str:
        """Normalize a ticker for yfinance.

        Israeli tickers should use the ``.TA`` suffix. This is a
        passthrough helper today but exists so callers can route
        without knowing yfinance's conventions.
        """
        return ticker.upper().strip()

    @retry_network
    def get_quote(self, ticker: str) -> Quote:
        self._log_call("get_quote", ticker)
        symbol = self._normalize_ticker(ticker)
        try:
            t = yf.Ticker(symbol)
            info = t.fast_info
            price = float(info.last_price) if info.last_price is not None else None
        except Exception as exc:
            raise DataSourceError(self.name, f"quote failed for {ticker}: {exc}") from exc

        if price is None:
            raise DataSourceError(self.name, f"no price returned for {ticker}")

        currency = getattr(info, "currency", None) or "USD"
        return Quote(
            ticker=symbol,
            price=price,
            currency=currency,
            timestamp=datetime.now(UTC),
            volume=int(info.last_volume) if info.last_volume is not None else None,
            market_cap=float(info.market_cap) if info.market_cap is not None else None,
            day_high=float(info.day_high) if info.day_high is not None else None,
            day_low=float(info.day_low) if info.day_low is not None else None,
            previous_close=(
                float(info.previous_close) if info.previous_close is not None else None
            ),
        )

    @retry_network
    def get_fundamentals(self, ticker: str) -> Fundamentals:
        self._log_call("get_fundamentals", ticker)
        symbol = self._normalize_ticker(ticker)
        try:
            t = yf.Ticker(symbol)
            info: dict[str, Any] = t.info or {}
        except Exception as exc:
            raise DataSourceError(
                self.name, f"fundamentals failed for {ticker}: {exc}"
            ) from exc

        if not info:
            raise DataSourceError(self.name, f"no info returned for {ticker}")

        def _f(key: str) -> float | None:
            value = info.get(key)
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        def _i(key: str) -> int | None:
            value = info.get(key)
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        return Fundamentals(
            ticker=symbol,
            pe_ratio=_f("trailingPE"),
            forward_pe=_f("forwardPE"),
            pb_ratio=_f("priceToBook"),
            ps_ratio=_f("priceToSalesTrailing12Months"),
            ev_to_ebitda=_f("enterpriseToEbitda"),
            peg_ratio=_f("pegRatio"),
            roe=_f("returnOnEquity"),
            roa=_f("returnOnAssets"),
            gross_margin=_f("grossMargins"),
            operating_margin=_f("operatingMargins"),
            net_margin=_f("profitMargins"),
            eps=_f("trailingEps"),
            book_value_per_share=_f("bookValue"),
            revenue=_f("totalRevenue"),
            gross_profit=_f("grossProfits"),
            ebitda=_f("ebitda"),
            net_income=_f("netIncomeToCommon"),
            operating_cash_flow=_f("operatingCashflow"),
            free_cash_flow=_f("freeCashflow"),
            total_debt=_f("totalDebt"),
            cash=_f("totalCash"),
            current_ratio=_f("currentRatio"),
            quick_ratio=_f("quickRatio"),
            debt_to_equity=_f("debtToEquity"),
            dividend_yield=_f("dividendYield"),
            payout_ratio=_f("payoutRatio"),
            shares_outstanding=_f("sharesOutstanding"),
            fiscal_year=_i("lastFiscalYearEnd"),
            period="ttm",
        )


__all__ = ["YFinanceSource"]
