"""FMP-backed point-in-time adapter.

Wraps the existing :class:`core.data.fmp_source.FMPSource` to satisfy the
:class:`EdgarAdapter` Protocol. Uses FMP's `acceptedDate` / `fillingDate`
field as the point-in-time filter — i.e. only filings whose
``acceptedDate <= as_of`` are considered visible.

This is the practical alternative to the edgartools-based adapter when:

* The installed edgartools version has API breakage in
  ``Filing.__init__()`` (the case in this build).
* You need higher throughput than EDGAR's 10 req/s rate limit.
* FMP's structured data is richer than what edgartools' XBRL parser
  surfaces (income statement and balance sheet are first-class).

Limitations:

* FMP free tier returns approximately 5 years of historical filings.
  For longer windows, a paid plan is required.
* SIC codes are not directly available in FMP's standard endpoints;
  we pull them from the ``/profile`` endpoint and cache aggressively.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from core.data.fmp_source import FMPSource
from core.exceptions import DataSourceError
from core.logger import get_logger

from .point_in_time import EdgarAdapter, FilingMetadata

logger = get_logger("core.backtest.fmp_adapter")


class FMPAdapter(EdgarAdapter):
    """FMP-backed adapter that fits the :class:`EdgarAdapter` Protocol."""

    def __init__(self, fmp: FMPSource | None = None) -> None:
        self._fmp = fmp or FMPSource()
        self._sic_cache: dict[str, str | None] = {}

    # ------------------------------------------------------------------
    def list_filings(
        self, ticker: str, *, form_types: tuple[str, ...]
    ) -> list[FilingMetadata]:
        """Build :class:`FilingMetadata` objects from FMP's income-statement
        endpoint, which carries ``date``, ``fillingDate``, ``acceptedDate``,
        ``period``, and ``cik`` for each historical filing.
        """
        ticker = ticker.upper()
        out: list[FilingMetadata] = []
        try:
            statements = self._fmp.get_income_statement(ticker, limit=5)
        except DataSourceError as exc:
            logger.warning(f"FMP income statement failed for {ticker}: {exc}")
            return out

        for s in statements or []:
            try:
                period = s.get("period", "FY")
                form_type = "10-K" if period == "FY" else "10-Q"
                if form_type not in form_types:
                    continue
                filing_date_str = s.get("fillingDate") or s.get("acceptedDate") or s.get("date")
                if not filing_date_str:
                    continue
                filing_date = _parse_fmp_date(filing_date_str)
                period_of_report = _parse_fmp_date(s.get("date"))
                # Use the FMP statement record's period+date as a deterministic accession.
                accession = f"FMP-{ticker}-{period}-{period_of_report.isoformat()}"
                out.append(
                    FilingMetadata(
                        ticker=ticker,
                        cik=str(s.get("cik")) if s.get("cik") else None,
                        form_type=form_type,
                        filing_date=filing_date,
                        period_of_report=period_of_report,
                        accession_number=accession,
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug(f"skipping FMP statement row for {ticker}: {exc}")
        return out

    # ------------------------------------------------------------------
    def parse_financials(
        self, filing: FilingMetadata
    ) -> dict[str, float | None]:
        """Pull income-statement + balance-sheet rows for the filing and
        produce the standardized field set.
        """
        ticker = filing.ticker
        period_iso = filing.period_of_report.isoformat() if filing.period_of_report else None
        out: dict[str, float | None] = {}

        try:
            income = self._fmp.get_income_statement(ticker, limit=5)
            balance = self._fmp.get_balance_sheet(ticker, limit=5)
            cash_flow = self._fmp.get_cash_flow(ticker, limit=5)
        except DataSourceError as exc:
            logger.warning(f"FMP statements failed for {ticker}: {exc}")
            return out

        # Find the rows whose `date` matches our period_of_report
        income_row = _row_for_period(income, period_iso)
        balance_row = _row_for_period(balance, period_iso)
        cash_row = _row_for_period(cash_flow, period_iso)

        if income_row:
            out["revenue"] = _f(income_row.get("revenue"))
            out["net_income"] = _f(income_row.get("netIncome"))
            out["operating_income"] = _f(income_row.get("operatingIncome"))
            out["eps_basic"] = _f(income_row.get("eps"))
            out["eps_diluted"] = _f(income_row.get("epsdiluted"))
            out["shares_outstanding"] = _f(income_row.get("weightedAverageShsOut"))
        if balance_row:
            out["total_assets"] = _f(balance_row.get("totalAssets"))
            out["total_liabilities"] = _f(balance_row.get("totalLiabilities"))
            out["total_equity"] = _f(balance_row.get("totalStockholdersEquity"))
            out["cash_and_equivalents"] = _f(balance_row.get("cashAndCashEquivalents"))
            out["long_term_debt"] = _f(balance_row.get("longTermDebt"))
            out["total_debt"] = _f(balance_row.get("totalDebt"))
            out["current_assets"] = _f(balance_row.get("totalCurrentAssets"))
            out["current_liabilities"] = _f(balance_row.get("totalCurrentLiabilities"))
            out["ppe_net"] = _f(balance_row.get("propertyPlantEquipmentNet"))
        if cash_row:
            out["operating_cash_flow"] = _f(cash_row.get("operatingCashFlow"))
            out["capex"] = _f(cash_row.get("capitalExpenditure"))
            out["dividends_paid"] = _f(cash_row.get("dividendsPaid"))

        # SIC code via /profile (cached per ticker)
        sic = self._lookup_sic(ticker)
        if sic:
            out["sic_code"] = sic  # type: ignore[assignment]

        return out

    # ------------------------------------------------------------------
    def _lookup_sic(self, ticker: str) -> str | None:
        if ticker in self._sic_cache:
            return self._sic_cache[ticker]
        try:
            data = self._fmp._get("/profile", {"symbol": ticker})
        except DataSourceError as exc:
            logger.debug(f"FMP profile failed for {ticker}: {exc}")
            self._sic_cache[ticker] = None
            return None
        if data and isinstance(data, list) and data:
            sic = data[0].get("sicCode") or data[0].get("industry")
            self._sic_cache[ticker] = str(sic) if sic else None
        else:
            self._sic_cache[ticker] = None
        return self._sic_cache[ticker]


# ----------------------------------------------------------------------
def _parse_fmp_date(value: Any) -> date:
    """FMP returns ``YYYY-MM-DD`` strings; sometimes with a trailing time."""
    s = str(value).strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    if " " in s:
        s = s.split(" ", 1)[0]
    return datetime.strptime(s, "%Y-%m-%d").date()


def _row_for_period(
    rows: list[dict[str, Any]] | None, period_iso: str | None
) -> dict[str, Any] | None:
    if not rows:
        return None
    if period_iso:
        for r in rows:
            if str(r.get("date")) == period_iso:
                return r
    return rows[0]  # fallback to most recent


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["FMPAdapter"]
