"""Normalized data models shared across all data sources.

Every data source returns these types regardless of the underlying API
shape, so downstream code (scoring, screening, LLM prompts) never has
to branch on the source.

All numeric fields default to ``None`` to model "data not available" —
real markets do not always have every metric, and ``None`` is more
honest than zero.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class _BaseModel(BaseModel):
    """Project-wide BaseModel with strict, immutable defaults."""

    model_config = ConfigDict(
        frozen=False,
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="ignore",
    )


class Quote(_BaseModel):
    """Real-time or last-traded price for a ticker."""

    ticker: str
    price: float
    currency: str = "USD"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    volume: int | None = None
    market_cap: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    previous_close: float | None = None


class Fundamentals(_BaseModel):
    """Fundamental valuation and quality metrics for a ticker.

    All values are point-in-time. The ``fiscal_year`` and ``period`` fields
    indicate which reporting period the data describes; quotes and ratios
    derived from market price are usually current ("ttm").
    """

    ticker: str

    # --- Valuation multiples ------------------------------------------------
    pe_ratio: float | None = None
    forward_pe: float | None = None
    pb_ratio: float | None = None
    ps_ratio: float | None = None
    ev_to_ebitda: float | None = None
    peg_ratio: float | None = None

    # --- Quality / profitability --------------------------------------------
    roe: float | None = None
    roa: float | None = None
    roic: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None

    # --- Per-share metrics --------------------------------------------------
    eps: float | None = None
    book_value_per_share: float | None = None
    revenue_per_share: float | None = None
    free_cash_flow_per_share: float | None = None

    # --- Balance sheet ------------------------------------------------------
    total_assets: float | None = None
    total_equity: float | None = None
    total_debt: float | None = None
    long_term_debt: float | None = None
    cash: float | None = None
    working_capital: float | None = None
    retained_earnings: float | None = None

    # --- Income statement ---------------------------------------------------
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    ebit: float | None = None
    ebitda: float | None = None
    net_income: float | None = None

    # --- Cash flow ----------------------------------------------------------
    operating_cash_flow: float | None = None
    free_cash_flow: float | None = None
    capex: float | None = None

    # --- Solvency / liquidity -----------------------------------------------
    current_ratio: float | None = None
    quick_ratio: float | None = None
    debt_to_equity: float | None = None
    interest_coverage: float | None = None

    # --- Returns to shareholders --------------------------------------------
    dividend_yield: float | None = None
    payout_ratio: float | None = None
    buyback_yield: float | None = None

    # --- Period metadata ----------------------------------------------------
    fiscal_year: int | None = None
    period: str | None = None  # e.g., "ttm", "annual", "Q3-2025"
    shares_outstanding: float | None = None


class NewsItem(_BaseModel):
    """A single news article relevant to a ticker."""

    title: str
    url: str
    published_at: datetime
    source: str
    sentiment: float | None = Field(default=None, ge=-1.0, le=1.0)
    summary: str | None = None
    tickers: list[str] = Field(default_factory=list)


class InsiderTransaction(_BaseModel):
    """A single insider transaction (Form 4 or equivalent)."""

    ticker: str
    insider_name: str
    title: str | None = None
    transaction_type: str  # e.g., "BUY", "SELL"
    shares: float
    price: float | None = None
    value: float | None = None
    transaction_date: datetime
    filing_date: datetime | None = None


class FilingExcerpt(_BaseModel):
    """Excerpt of a regulatory filing."""

    ticker: str
    form_type: str  # e.g., "10-K", "10-Q", "8-K", "13F"
    filed_at: datetime
    period_of_report: datetime | None = None
    accession_number: str | None = None
    url: str | None = None
    text_excerpt: str | None = None


class StockSnapshot(_BaseModel):
    """A unified, source-agnostic view of a ticker.

    Produced by :class:`core.data.unified_fetcher.UnifiedFetcher` by
    combining results from multiple sources. Fields are populated
    best-effort; missing data is represented as ``None`` or empty lists.
    """

    ticker: str
    quote: Quote | None = None
    fundamentals: Fundamentals | None = None
    news: list[NewsItem] = Field(default_factory=list)
    insider_transactions: list[InsiderTransaction] = Field(default_factory=list)
    filings: list[FilingExcerpt] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "FilingExcerpt",
    "Fundamentals",
    "InsiderTransaction",
    "NewsItem",
    "Quote",
    "StockSnapshot",
]
