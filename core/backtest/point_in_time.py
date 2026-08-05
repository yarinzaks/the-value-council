"""Point-in-time financial data — EDGAR filing-date-aware.

When the agent asks "what was AAPL's reported EPS on 2015-03-15?",
this module finds the most recent 10-K or 10-Q filing whose
``filing_date <= 2015-03-15`` and returns the data from THAT filing.
Restatements that happened later are ignored.

This is the cornerstone of bias-free backtesting.

Data flow::

    PointInTimeLoader.get_financials(ticker, as_of)
        ↓
    list filings via edgartools (cached locally)
        ↓
    pick latest filing with filing_date ≤ as_of
        ↓
    parse XBRL via edgartools.Financials
        ↓
    return PointInTimeFinancials

Cache is in ``data/cache/edgar_filings.sqlite``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Protocol

from core.exceptions import ValueCouncilError
from core.logger import get_logger

logger = get_logger("core.backtest.point_in_time")

from core.paths import PROJECT_ROOT, edgar_filings_db as _edgar_filings_db
DEFAULT_CACHE_PATH = _edgar_filings_db()


class PointInTimeError(ValueCouncilError):
    """Raised when point-in-time data cannot be retrieved."""


@dataclass(frozen=True)
class FilingMetadata:
    """Metadata for one EDGAR filing — what we need for PIT lookups."""

    ticker: str
    cik: str | None
    form_type: str  # "10-K" or "10-Q"
    filing_date: date
    period_of_report: date | None
    accession_number: str

    def to_dict(self) -> dict[str, str | None]:
        d = asdict(self)
        d["filing_date"] = self.filing_date.isoformat()
        d["period_of_report"] = (
            self.period_of_report.isoformat() if self.period_of_report else None
        )
        return d

    @classmethod
    def from_dict(cls, d: dict[str, str | None]) -> "FilingMetadata":
        return cls(
            ticker=str(d["ticker"]),
            cik=d.get("cik"),
            form_type=str(d["form_type"]),
            filing_date=date.fromisoformat(str(d["filing_date"])),
            period_of_report=(
                date.fromisoformat(str(d["period_of_report"]))
                if d.get("period_of_report")
                else None
            ),
            accession_number=str(d["accession_number"]),
        )


@dataclass(frozen=True)
class PointInTimeFinancials:
    """Subset of fundamentals known to a public investor on a given date.

    All fields are best-effort — companies with sparse XBRL reporting
    may have ``None`` values. Callers must handle missing fields.
    """

    ticker: str
    as_of: date
    source_filing: FilingMetadata
    revenue: float | None = None
    net_income: float | None = None
    eps_basic: float | None = None
    eps_diluted: float | None = None
    operating_income: float | None = None  # EBIT proxy
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    cash_and_equivalents: float | None = None
    long_term_debt: float | None = None
    shares_outstanding: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None
    dividends_paid: float | None = None
    # Greenblatt Magic Formula fields (added 2026-04-28)
    current_assets: float | None = None
    current_liabilities: float | None = None
    ppe_net: float | None = None  # net property, plant & equipment (after depreciation)
    total_debt: float | None = None  # short-term + long-term debt for EV
    goodwill: float | None = None
    intangible_assets: float | None = None  # excluding goodwill
    sic_code: str | None = None  # for sector exclusions

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        d["source_filing"] = self.source_filing.to_dict()
        return d


# Fields that carry actual reported numbers. ``sic_code`` is excluded on
# purpose: it is classification metadata, so a payload holding nothing
# but a SIC code still says nothing about the company's financials.
_DATA_FIELDS: frozenset[str] = frozenset(
    f.name
    for f in fields(PointInTimeFinancials)
    if f.name not in {"ticker", "as_of", "source_filing", "sic_code"}
)


def _has_financial_data(payload: Mapping[str, object]) -> bool:
    """True when ``payload`` carries at least one reported value.

    Sparse XBRL is normal and stays valid — one real number is enough.
    A payload with none at all is a parse failure, not a fact about the
    company, and must not be dressed up as data.
    """
    return any(payload.get(name) is not None for name in _DATA_FIELDS)


# ----------------------------------------------------------------------
# Adapter protocol — the EDGAR layer is pluggable for testability.
# ----------------------------------------------------------------------
class EdgarAdapter(Protocol):
    """Minimal protocol the loader needs from an EDGAR client.

    The default implementation wraps :mod:`edgartools`; tests can supply
    a fake adapter to exercise PIT logic without network calls.
    """

    def list_filings(
        self, ticker: str, *, form_types: tuple[str, ...]
    ) -> list[FilingMetadata]:
        """Return all filings for ``ticker`` matching ``form_types``."""

    def parse_financials(
        self, filing: FilingMetadata
    ) -> dict[str, float | None]:
        """Return a dict of standardized financial metrics for ``filing``.

        Keys must match the field names of :class:`PointInTimeFinancials`
        (excluding ``ticker``, ``as_of``, ``source_filing``).
        """


class EdgartoolsAdapter:
    """Default adapter wrapping :mod:`edgartools`.

    Lazily imports edgartools so that test environments without the
    library can still import and use a fake adapter.

    Only :meth:`list_filings` is implemented. :meth:`parse_financials`
    raises — see its docstring — so a default-constructed
    :class:`PointInTimeLoader` will fail loudly rather than hand back
    empty financials. Inject
    :class:`~core.data.fundamentals_fetcher.CachedEdgarAdapter` to read
    actual numbers.
    """

    def __init__(self) -> None:
        # Defer import for graceful failure
        try:
            from edgar import set_identity  # noqa: F401
        except ImportError as exc:
            raise PointInTimeError(f"edgartools not installed: {exc}") from exc
        # Identity is set elsewhere via core.data.edgar_source._initialize_edgar;
        # we re-trigger to be safe.
        from core.data.edgar_source import _initialize_edgar

        _initialize_edgar()

    def list_filings(
        self, ticker: str, *, form_types: tuple[str, ...]
    ) -> list[FilingMetadata]:
        from edgar import Company

        results: list[FilingMetadata] = []
        company = Company(ticker.upper())
        cik_str = str(company.cik) if hasattr(company, "cik") else None
        for form in form_types:
            try:
                filings = company.get_filings(form=form)
            except Exception as exc:  # noqa: BLE001 — edgartools throws broad
                logger.warning(f"list_filings({ticker}, {form}) failed: {exc}")
                continue
            for f in filings:
                try:
                    fd = _to_date(f.filing_date)
                    pr = _to_date(f.period_of_report) if f.period_of_report else None
                    results.append(
                        FilingMetadata(
                            ticker=ticker.upper(),
                            cik=cik_str,
                            form_type=form,
                            filing_date=fd,
                            period_of_report=pr,
                            accession_number=str(f.accession_number),
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — defensive
                    logger.debug(f"skipping malformed filing for {ticker}: {exc}")
        return results

    def parse_financials(
        self, filing: FilingMetadata
    ) -> dict[str, float | None]:
        """Unimplemented against the installed edgartools — always raises.

        This path has never worked. It called ``Filing()`` with keyword
        names the library does not accept (``accession_number`` for
        ``accession_no``, ``form_type`` for ``form``, and omitting the
        required ``company`` and ``filing_date``), swallowed the
        resulting ``TypeError``, and returned ``{}``.

        Fixing the constructor alone would change nothing observable:
        :data:`_FINANCIAL_GETTERS` walks attribute paths such as
        ``financials.income_statement.revenues``, but on the installed
        version those statement accessors are *methods* and the values
        live behind ``Financials.get_revenue()`` and friends — so every
        field would still resolve to ``None``.

        :class:`~core.data.fundamentals_fetcher.CachedEdgarAdapter`
        already extracts these numbers correctly from the local EDGAR
        cache, and is what every caller in the project injects.
        """
        raise PointInTimeError(
            f"EdgartoolsAdapter cannot parse financials for {filing.ticker} "
            f"(accession {filing.accession_number}): this adapter's XBRL "
            f"extraction is not implemented against the installed edgartools. "
            f"Pass CachedEdgarAdapter from core.data.fundamentals_fetcher to "
            f"PointInTimeLoader instead."
        )


# Mapping from PointInTimeFinancials field → list of getter callables
# tried in order. Each getter takes the edgartools Financials object
# and returns a value or raises.
def _safe_get(obj: object, *attrs: str) -> object | None:
    for attr in attrs:
        if not hasattr(obj, attr):
            return None
        obj = getattr(obj, attr)
    return obj


# Currently unused: these attribute paths target an edgartools API
# surface the installed version does not expose — the statement
# accessors are methods there, and values live behind
# ``Financials.get_revenue()`` and friends (see parse_financials above).
# Kept as the field map for whoever implements that extraction: they
# record which concept each field wants, not how to reach it.
_FINANCIAL_GETTERS: dict[str, list] = {
    "revenue": [
        lambda f: _safe_get(f, "income_statement", "revenues"),
        lambda f: _safe_get(f, "income", "revenues"),
    ],
    "net_income": [
        lambda f: _safe_get(f, "income_statement", "net_income"),
    ],
    "operating_income": [
        lambda f: _safe_get(f, "income_statement", "operating_income"),
    ],
    "eps_basic": [
        lambda f: _safe_get(f, "income_statement", "earnings_per_share_basic"),
    ],
    "eps_diluted": [
        lambda f: _safe_get(f, "income_statement", "earnings_per_share_diluted"),
    ],
    "total_assets": [
        lambda f: _safe_get(f, "balance_sheet", "total_assets"),
    ],
    "total_liabilities": [
        lambda f: _safe_get(f, "balance_sheet", "total_liabilities"),
    ],
    "total_equity": [
        lambda f: _safe_get(f, "balance_sheet", "total_equity"),
    ],
    "cash_and_equivalents": [
        lambda f: _safe_get(f, "balance_sheet", "cash"),
    ],
    "long_term_debt": [
        lambda f: _safe_get(f, "balance_sheet", "long_term_debt"),
    ],
    "shares_outstanding": [
        lambda f: _safe_get(f, "balance_sheet", "shares_outstanding"),
    ],
    "operating_cash_flow": [
        lambda f: _safe_get(f, "cash_flow", "operating_cash_flow"),
    ],
    "capex": [
        lambda f: _safe_get(f, "cash_flow", "capital_expenditure"),
    ],
    "dividends_paid": [
        lambda f: _safe_get(f, "cash_flow", "dividends_paid"),
    ],
    # Greenblatt Magic Formula fields
    "current_assets": [
        lambda f: _safe_get(f, "balance_sheet", "current_assets"),
        lambda f: _safe_get(f, "balance_sheet", "total_current_assets"),
    ],
    "current_liabilities": [
        lambda f: _safe_get(f, "balance_sheet", "current_liabilities"),
        lambda f: _safe_get(f, "balance_sheet", "total_current_liabilities"),
    ],
    "ppe_net": [
        lambda f: _safe_get(f, "balance_sheet", "property_plant_equipment_net"),
        lambda f: _safe_get(f, "balance_sheet", "net_ppe"),
    ],
    "total_debt": [
        lambda f: _safe_get(f, "balance_sheet", "total_debt"),
    ],
}


# ----------------------------------------------------------------------
# Cache schema
# ----------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    ticker            TEXT NOT NULL,
    cik               TEXT,
    form_type         TEXT NOT NULL,
    filing_date       TEXT NOT NULL,
    period_of_report  TEXT,
    accession_number  TEXT NOT NULL,
    PRIMARY KEY (ticker, accession_number)
);
CREATE INDEX IF NOT EXISTS idx_filings_ticker_date ON filings(ticker, filing_date);

CREATE TABLE IF NOT EXISTS financials (
    accession_number  TEXT PRIMARY KEY,
    payload_json      TEXT NOT NULL
);
"""

# Stamped into every stored payload. Bump whenever parse_financials
# starts producing a field it did not before, otherwise the cache keeps
# serving payloads that silently lack it — an accession is immutable, so
# nothing else would ever invalidate the row. A mismatch is treated as a
# miss and the filing is re-parsed on next access.
#
# 2: sic_code populated from the bundled SEC map (was always None).
# 3: goodwill and intangible_assets, for tangible common equity.
_PAYLOAD_VERSION = 3
_VERSION_KEY = "_payload_version"


class PointInTimeLoader:
    """Looks up the financial data publicly known on a given date."""

    def __init__(
        self,
        adapter: EdgarAdapter | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self._adapter = adapter or EdgartoolsAdapter()
        self._cache_path = cache_path or DEFAULT_CACHE_PATH
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._cache_path, check_same_thread=False)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Cache I/O
    # ------------------------------------------------------------------
    def _cached_filings(self, ticker: str) -> list[FilingMetadata]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ticker, cik, form_type, filing_date,
                       period_of_report, accession_number
                FROM filings WHERE ticker = ?
                """,
                (ticker.upper(),),
            ).fetchall()
        return [
            FilingMetadata(
                ticker=r[0],
                cik=r[1],
                form_type=r[2],
                filing_date=date.fromisoformat(r[3]),
                period_of_report=date.fromisoformat(r[4]) if r[4] else None,
                accession_number=r[5],
            )
            for r in rows
        ]

    def _store_filings(self, filings: list[FilingMetadata]) -> None:
        if not filings:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO filings
                    (ticker, cik, form_type, filing_date,
                     period_of_report, accession_number)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        f.ticker,
                        f.cik,
                        f.form_type,
                        f.filing_date.isoformat(),
                        f.period_of_report.isoformat() if f.period_of_report else None,
                        f.accession_number,
                    )
                    for f in filings
                ],
            )

    def _cached_financials(self, accession: str) -> dict[str, float | None] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM financials WHERE accession_number = ?",
                (accession,),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        if payload.get(_VERSION_KEY) != _PAYLOAD_VERSION:
            # Written before parse_financials produced its current field
            # set. Treat as a miss so the filing is re-parsed.
            return None
        return payload

    def _store_financials(
        self, accession: str, payload: dict[str, float | None]
    ) -> None:
        stamped = {**payload, _VERSION_KEY: _PAYLOAD_VERSION}
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO financials (accession_number, payload_json) VALUES (?, ?)",
                (accession, json.dumps(stamped)),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def list_filings(
        self,
        ticker: str,
        *,
        form_types: tuple[str, ...] = ("10-K", "10-Q"),
        force_refresh: bool = False,
    ) -> list[FilingMetadata]:
        """Return all known filings for ticker — from cache when possible."""
        ticker = ticker.upper()
        if not force_refresh:
            cached = self._cached_filings(ticker)
            if cached:
                return [f for f in cached if f.form_type in form_types]
        filings = self._adapter.list_filings(ticker, form_types=form_types)
        self._store_filings(filings)
        return filings

    def latest_filing_before(
        self,
        ticker: str,
        as_of: date | datetime,
        *,
        form_types: tuple[str, ...] = ("10-K", "10-Q"),
    ) -> FilingMetadata | None:
        """Return the most recent filing with ``filing_date <= as_of``."""
        as_of_d = _to_date(as_of)
        filings = self.list_filings(ticker, form_types=form_types)
        eligible = [f for f in filings if f.filing_date <= as_of_d]
        if not eligible:
            return None
        return max(eligible, key=lambda f: f.filing_date)

    def get_financials(
        self,
        ticker: str,
        as_of: date | datetime,
        *,
        form_types: tuple[str, ...] = ("10-K", "10-Q"),
    ) -> PointInTimeFinancials | None:
        """Return point-in-time financials for ticker as of date.

        Returns ``None`` when no filing exists on or before ``as_of``.

        Raises :class:`PointInTimeError` when a filing *was* found but
        yielded no usable data. That is a parse failure, and it is
        deliberately not reported as ``None`` — which would be
        indistinguishable from having no filing at all — nor as a
        :class:`PointInTimeFinancials` whose every field is ``None``,
        which would look like real data to every caller.
        """
        as_of_d = _to_date(as_of)
        filing = self.latest_filing_before(
            ticker, as_of_d, form_types=form_types
        )
        if filing is None:
            logger.info(
                f"no filing for {ticker} on or before {as_of_d}"
            )
            return None

        cached = self._cached_financials(filing.accession_number)
        if cached is not None and _has_financial_data(cached):
            payload = cached
        else:
            # Either nothing cached, or a row holding no data at all —
            # written back when parse failures were stored as if they
            # were results. Re-parse instead of replaying it, so a cache
            # poisoned by a broken adapter heals once the adapter works.
            logger.debug(
                f"parsing financials for {ticker} {filing.form_type} "
                f"filed {filing.filing_date} (acc {filing.accession_number})"
            )
            payload = self._adapter.parse_financials(filing)
            # Never store an empty payload. An accession is immutable, so
            # nothing would ever invalidate the row and the parse failure
            # would outlive whatever caused it.
            if _has_financial_data(payload):
                self._store_financials(filing.accession_number, payload)

        if not _has_financial_data(payload):
            raise PointInTimeError(
                f"{ticker.upper()}: {filing.form_type} filed "
                f"{filing.filing_date} (accession {filing.accession_number}) "
                f"yielded no usable financial data from "
                f"{type(self._adapter).__name__}. The filing exists — this is "
                f"a parse failure, not a missing filing."
            )

        return PointInTimeFinancials(
            ticker=ticker.upper(),
            as_of=as_of_d,
            source_filing=filing,
            revenue=payload.get("revenue"),
            net_income=payload.get("net_income"),
            eps_basic=payload.get("eps_basic"),
            eps_diluted=payload.get("eps_diluted"),
            operating_income=payload.get("operating_income"),
            total_assets=payload.get("total_assets"),
            total_liabilities=payload.get("total_liabilities"),
            total_equity=payload.get("total_equity"),
            cash_and_equivalents=payload.get("cash_and_equivalents"),
            long_term_debt=payload.get("long_term_debt"),
            shares_outstanding=payload.get("shares_outstanding"),
            operating_cash_flow=payload.get("operating_cash_flow"),
            capex=payload.get("capex"),
            dividends_paid=payload.get("dividends_paid"),
            current_assets=payload.get("current_assets"),
            current_liabilities=payload.get("current_liabilities"),
            ppe_net=payload.get("ppe_net"),
            total_debt=payload.get("total_debt"),
            goodwill=payload.get("goodwill"),
            intangible_assets=payload.get("intangible_assets"),
            sic_code=str(payload["sic_code"]) if payload.get("sic_code") else None,
        )


def _to_date(value: date | datetime | str) -> date:
    """Coerce to a :class:`date`. Accepts date, datetime, or ISO string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


__all__ = [
    "EdgarAdapter",
    "EdgartoolsAdapter",
    "FilingMetadata",
    "PointInTimeError",
    "PointInTimeFinancials",
    "PointInTimeLoader",
]
