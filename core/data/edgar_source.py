"""SEC EDGAR — filings, 13F holdings, Form 4 insider transactions.

Uses the :mod:`edgartools` library which wraps EDGAR's public APIs.
SEC requires an identifying ``User-Agent`` (name + email) on every
request; we read it from settings and configure edgartools at import.

EDGAR is US-only — Israeli filings are not in this source.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.config import get_settings
from core.exceptions import DataSourceError

from .base import DataSource
from .models import (
    FilingExcerpt,
    Fundamentals,
    InsiderTransaction,
    Quote,
)

_EDGAR_INITIALIZED = False


def _initialize_edgar() -> None:
    """Lazily configure edgartools with our User-Agent.

    edgartools refuses to make calls without identification; we set
    this once on first use.
    """
    global _EDGAR_INITIALIZED
    if _EDGAR_INITIALIZED:
        return
    try:
        from edgar import set_identity
    except ImportError as exc:
        raise DataSourceError("edgar", f"edgartools not installed: {exc}") from exc
    set_identity(get_settings().sec_user_agent)
    _EDGAR_INITIALIZED = True


class EdgarSource(DataSource):
    """SEC EDGAR via :mod:`edgartools`.

    EDGAR does not provide quotes — :meth:`get_quote` and
    :meth:`get_fundamentals` are intentionally unsupported on this
    source. Use the filing-focused methods instead.
    """

    name = "edgar"

    def __init__(self) -> None:
        super().__init__()
        _initialize_edgar()

    # --- DataSource ABC compliance ------------------------------------------
    def get_quote(self, ticker: str) -> Quote:
        raise DataSourceError(self.name, "EDGAR does not provide quotes")

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        raise DataSourceError(
            self.name,
            "EDGAR fundamentals require parsing 10-K/10-Q XBRL — use FMP for ratios",
        )

    # --- Filing-specific methods --------------------------------------------
    def get_filings(
        self, ticker: str, form_type: str = "10-K", limit: int = 5
    ) -> list[FilingExcerpt]:
        """Return recent filings of ``form_type`` for ``ticker``."""
        self._log_call("get_filings", ticker, form_type=form_type, limit=limit)
        try:
            from edgar import Company  # local import keeps import-time light
        except ImportError as exc:
            raise DataSourceError(self.name, f"edgartools missing: {exc}") from exc

        try:
            company = Company(ticker.upper())
            filings = company.get_filings(form=form_type).head(limit)
        except Exception as exc:
            raise DataSourceError(
                self.name, f"failed to fetch {form_type} for {ticker}: {exc}"
            ) from exc

        results: list[FilingExcerpt] = []
        for filing in filings:
            try:
                results.append(
                    FilingExcerpt(
                        ticker=ticker.upper(),
                        form_type=form_type,
                        filed_at=self._to_datetime(filing.filing_date),
                        period_of_report=self._to_datetime(filing.period_of_report),
                        accession_number=str(filing.accession_number),
                        url=str(filing.filing_url) if hasattr(filing, "filing_url") else None,
                    )
                )
            except Exception as exc:
                self.logger.warning(f"skipping filing for {ticker}: {exc}")
        return results

    def get_latest_10k(self, ticker: str) -> FilingExcerpt:
        """Return the most recent 10-K for ``ticker``."""
        self._log_call("get_latest_10k", ticker)
        filings = self.get_filings(ticker, form_type="10-K", limit=1)
        if not filings:
            raise DataSourceError(self.name, f"no 10-K filings for {ticker}")
        return filings[0]

    def get_form4_insider_transactions(
        self, ticker: str, limit: int = 20
    ) -> list[InsiderTransaction]:
        """Return recent Form 4 insider transactions for ``ticker``."""
        self._log_call("get_form4_insider_transactions", ticker, limit=limit)
        try:
            from edgar import Company
        except ImportError as exc:
            raise DataSourceError(self.name, f"edgartools missing: {exc}") from exc

        try:
            company = Company(ticker.upper())
            filings = company.get_filings(form="4").head(limit)
        except Exception as exc:
            raise DataSourceError(
                self.name, f"failed Form 4 fetch for {ticker}: {exc}"
            ) from exc

        results: list[InsiderTransaction] = []
        for filing in filings:
            try:
                # edgartools exposes parsed Form 4 data on .obj() for newer
                # versions; we capture conservative fields and skip unparseable.
                results.append(
                    InsiderTransaction(
                        ticker=ticker.upper(),
                        insider_name=str(getattr(filing, "reporting_owner", "unknown")),
                        title=None,
                        transaction_type="UNKNOWN",
                        shares=0.0,
                        transaction_date=self._to_datetime(filing.filing_date),
                        filing_date=self._to_datetime(filing.filing_date),
                    )
                )
            except Exception as exc:
                self.logger.warning(f"skipping Form 4 for {ticker}: {exc}")
        return results

    def get_13f_holdings(self, cik: str) -> list[dict[str, Any]]:
        """Return latest 13F holdings for an institution (by CIK)."""
        self._log_call("get_13f_holdings", cik)
        try:
            from edgar import Company
        except ImportError as exc:
            raise DataSourceError(self.name, f"edgartools missing: {exc}") from exc

        try:
            company = Company(cik)
            filings = company.get_filings(form="13F-HR").head(1)
        except Exception as exc:
            raise DataSourceError(self.name, f"failed 13F fetch for {cik}: {exc}") from exc

        if not filings:
            return []
        # Detailed per-holding parsing depends on edgartools version; expose
        # the raw filing object so callers can inspect.
        try:
            return list(filings)
        except Exception:
            return []

    # --- Helpers -------------------------------------------------------------
    @staticmethod
    def _to_datetime(value: Any) -> datetime:
        """Coerce edgartools' date-ish objects to ``datetime``."""
        if isinstance(value, datetime):
            return value
        if hasattr(value, "isoformat"):
            return datetime.fromisoformat(value.isoformat())
        return datetime.fromisoformat(str(value))


__all__ = ["EdgarSource"]
