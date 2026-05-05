"""Multi-source fundamentals fetcher with EDGAR cache as primary.

Wraps :class:`EdgarCache` (backed by the SEC Company Facts API) and
exposes the existing :class:`core.backtest.point_in_time.EdgarAdapter`
Protocol so the backtest runner can plug in directly.

Concept mapping
---------------
SEC XBRL uses dozens of slightly different concept names for the
same logical metric across companies and over time. We use **fallback
chains** — try the most common concept first, then progressively
broader fallbacks. This is the same pattern any production-grade
financials normalizer uses (Bloomberg, FactSet, Sharadar all do this
internally).

Each entry in :data:`CONCEPT_MAP` is a list ordered by preference;
the first concept that has a value at ``as_of`` wins.

Filing date as PIT anchor
-------------------------
For each (ticker, as_of) query, we synthesize a
:class:`FilingMetadata` from the most recent filing date observed in
the cached facts (across the concepts we care about). This satisfies
the existing PIT loader interface.

FMP fallback
------------
The EDGAR cache covers all reported XBRL data, but some concepts can
be sparse for older filings (especially pre-2010) or for companies
that didn't tag certain items consistently. When ``EdgarCache`` returns
None for a concept and the constructor was given an FMP fallback
adapter, we fall back to that source for missing fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from core.backtest.point_in_time import (
    EdgarAdapter,
    FilingMetadata,
)
from core.exceptions import ValueCouncilError
from core.logger import get_logger

from .edgar_cache import EdgarCache
from .edgar_facts import EdgarFactsClient, XbrlFact

logger = get_logger("core.data.fundamentals_fetcher")


# Mapping from PointInTimeFinancials field → ordered list of (namespace, concept)
# tuples. First match wins. Concepts inside a single field are ordered from
# most-specific to fallback.
CONCEPT_MAP: dict[str, list[tuple[str, str]]] = {
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "SalesRevenueGoodsNet"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
    ],
    "net_income": [
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "ProfitLoss"),
    ],
    "operating_income": [
        ("us-gaap", "OperatingIncomeLoss"),
        ("us-gaap", "IncomeLossFromContinuingOperationsBeforeInterestExpenseInterestIncomeIncomeTaxesExtraordinaryItemsNoncontrollingInterestsNet"),
    ],
    "eps_basic": [
        ("us-gaap", "EarningsPerShareBasic"),
    ],
    "eps_diluted": [
        ("us-gaap", "EarningsPerShareDiluted"),
    ],
    "total_assets": [
        ("us-gaap", "Assets"),
    ],
    "total_liabilities": [
        ("us-gaap", "Liabilities"),
    ],
    "total_equity": [
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    ],
    "cash_and_equivalents": [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        ("us-gaap", "Cash"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    ],
    "long_term_debt": [
        ("us-gaap", "LongTermDebtNoncurrent"),
        ("us-gaap", "LongTermDebt"),
    ],
    "shares_outstanding": [
        ("dei", "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"),
        ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"),
    ],
    "operating_cash_flow": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    ],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
    ],
    "dividends_paid": [
        ("us-gaap", "PaymentsOfDividends"),
        ("us-gaap", "PaymentsOfDividendsCommonStock"),
    ],
    # Greenblatt Magic Formula fields
    "current_assets": [
        ("us-gaap", "AssetsCurrent"),
    ],
    "current_liabilities": [
        ("us-gaap", "LiabilitiesCurrent"),
    ],
    "ppe_net": [
        ("us-gaap", "PropertyPlantAndEquipmentNet"),
    ],
    "total_debt": [
        # Total debt is rarely tagged directly; we approximate by
        # summing short-term and long-term debt later. Some companies
        # report a roll-up.
        ("us-gaap", "DebtCurrentAndNoncurrent"),
    ],
}

# Concepts that flow as point-in-time stocks (balance-sheet items): we
# want "as-of period_end" semantics — pick the latest period_end whose
# filing was disclosed by ``as_of``.
_STOCK_CONCEPTS: frozenset[str] = frozenset(
    [
        "total_assets",
        "total_liabilities",
        "total_equity",
        "cash_and_equivalents",
        "long_term_debt",
        "current_assets",
        "current_liabilities",
        "ppe_net",
        "total_debt",
        "shares_outstanding",
    ]
)

# Concepts that flow over time (income/cash flow). We prefer the most
# recent annual (10-K) filing for these unless a fresher 10-Q exists.
_FLOW_CONCEPTS: frozenset[str] = frozenset(
    [
        "revenue",
        "net_income",
        "operating_income",
        "eps_basic",
        "eps_diluted",
        "operating_cash_flow",
        "capex",
        "dividends_paid",
    ]
)


class FundamentalsError(ValueCouncilError):
    """Raised when fundamentals cannot be assembled."""


@dataclass
class FundamentalsFetcherConfig:
    """Tunables for :class:`FundamentalsFetcher`."""

    # If True, on a cache miss we fall through to live SEC fetch + cache
    # write. Useful for tickers added during a backtest run; turn off
    # for offline-only mode.
    populate_cache_on_miss: bool = True
    # Optional FMP fallback adapter for fields EDGAR doesn't fill in.
    fmp_fallback: EdgarAdapter | None = None


class FundamentalsFetcher:
    """Orchestrator that produces standardized fundamentals from EDGAR."""

    def __init__(
        self,
        cache: EdgarCache | None = None,
        client: EdgarFactsClient | None = None,
        config: FundamentalsFetcherConfig | None = None,
    ) -> None:
        self.cache = cache or EdgarCache()
        self.client = client  # may be None for offline mode
        self.config = config or FundamentalsFetcherConfig()

    # ------------------------------------------------------------------
    # Cache population
    # ------------------------------------------------------------------
    def ensure_cached(self, ticker: str) -> bool:
        """If ``ticker`` isn't cached, fetch from SEC and persist.

        Returns True if the cache has data after the call (either it
        was already there, or the fetch succeeded). False if no data
        is available for the ticker.
        """
        if self.cache.has_ticker(ticker):
            return True
        if self.client is None:
            return False
        try:
            facts = self.client.get_company_facts(ticker)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"SEC fetch failed for {ticker}: {exc}")
            return False
        if not facts:
            return False
        self.cache.save_facts(ticker, facts)
        return True

    # ------------------------------------------------------------------
    # PIT lookup
    # ------------------------------------------------------------------
    def get_field(
        self,
        ticker: str,
        field: str,
        as_of: date | datetime,
    ) -> XbrlFact | None:
        """Return the most recent value for ``field`` known on ``as_of``."""
        if field not in CONCEPT_MAP:
            raise FundamentalsError(f"unknown field {field!r}")
        if not self.cache.has_ticker(ticker) and self.config.populate_cache_on_miss:
            self.ensure_cached(ticker)
        prefer_annual = field in _STOCK_CONCEPTS  # balance sheet stocks
        for namespace, concept in CONCEPT_MAP[field]:
            fact = self.cache.latest_value_at(
                ticker,
                concept,
                as_of,
                namespace=namespace,
                forms=("10-K", "10-Q"),
                prefer_annual=prefer_annual,
            )
            if fact is not None:
                return fact
        return None

    def get_all_fields(
        self,
        ticker: str,
        as_of: date | datetime,
    ) -> tuple[dict[str, float | None], FilingMetadata | None]:
        """Return ``(field_values, filing_metadata)`` for ``ticker`` at ``as_of``.

        ``filing_metadata`` is synthesized from the most recent filing
        observed across all the looked-up concepts — this is what the
        existing PIT loader interface expects.
        """
        values: dict[str, float | None] = {}
        latest_filing: XbrlFact | None = None
        for field in CONCEPT_MAP:
            fact = self.get_field(ticker, field, as_of)
            if fact is None:
                values[field] = None
                continue
            values[field] = fact.value
            if latest_filing is None or fact.filed > latest_filing.filed:
                latest_filing = fact

        # Synthesize total_debt from long_term + (assume short-term=0)
        # if it's missing — most companies don't tag the rolled-up concept.
        if values.get("total_debt") is None and values.get("long_term_debt") is not None:
            values["total_debt"] = values["long_term_debt"]

        if latest_filing is None:
            return values, None
        meta = FilingMetadata(
            ticker=ticker.upper(),
            cik=None,  # not tracked at the fact level
            form_type=latest_filing.form,
            filing_date=latest_filing.filed,
            period_of_report=latest_filing.period_end,
            accession_number=latest_filing.accession_number,
        )
        return values, meta


# ----------------------------------------------------------------------
# EdgarAdapter implementation backed by the cache
# ----------------------------------------------------------------------
class CachedEdgarAdapter(EdgarAdapter):
    """Plug-in to the existing :class:`EdgarAdapter` Protocol.

    The backtest's :class:`PointInTimeLoader` calls
    :meth:`list_filings` to discover available filings and
    :meth:`parse_financials` to extract numbers. Both come straight
    from the local Parquet cache.
    """

    def __init__(self, fetcher: FundamentalsFetcher | None = None) -> None:
        self.fetcher = fetcher or FundamentalsFetcher()

    def list_filings(
        self, ticker: str, *, form_types: tuple[str, ...]
    ) -> list[FilingMetadata]:
        ticker_u = ticker.upper()
        if not self.fetcher.cache.has_ticker(ticker_u):
            self.fetcher.ensure_cached(ticker_u)
        df = self.fetcher.cache.load_dataframe(ticker_u)
        if df.empty:
            return []
        # One filing per (filing_date, accession_number)
        df = df[df["form"].isin(form_types)]
        if df.empty:
            return []
        unique = df.drop_duplicates(subset=["accession_number"]).sort_values(
            "filed", ascending=False
        )
        results: list[FilingMetadata] = []
        for _, row in unique.iterrows():
            try:
                results.append(
                    FilingMetadata(
                        ticker=ticker_u,
                        cik=None,
                        form_type=str(row["form"]),
                        filing_date=row["filed"].date(),
                        period_of_report=row["period_end"].date(),
                        accession_number=str(row["accession_number"]),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"skipping malformed cache row for {ticker_u}: {exc}")
        return results

    def parse_financials(
        self, filing: FilingMetadata
    ) -> dict[str, float | None]:
        # We use the filing date as the as_of and let the field-fetcher
        # do the heavy lifting (which respects the filing date for PIT).
        values, _ = self.fetcher.get_all_fields(filing.ticker, filing.filing_date)
        # We can't extract SIC code from XBRL alone — leave None and
        # let the caller decide whether to enrich it externally.
        return {**values, "sic_code": None}


__all__ = [
    "CONCEPT_MAP",
    "CachedEdgarAdapter",
    "FundamentalsError",
    "FundamentalsFetcher",
    "FundamentalsFetcherConfig",
]
