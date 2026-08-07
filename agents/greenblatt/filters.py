"""Universe filters for Greenblatt's Magic Formula.

Implements the exclusions specified in Section 4.2 of the Greenblatt
playbook ("The Magic Formula procedure"):

* **Adequate size** — minimum market capitalization (default $50M; the
  agent uses $1B for the modern S&P 500 universe, per Greenblatt's
  recommendation for retirement accounts).
* **Sector exclusions** — financial services (SIC 6000-6999) and
  utilities (SIC 4900-4999). Their balance sheets do not fit the
  Net Working Capital + Net Fixed Assets denominator of ROC.
* **Stale-data exclusion** — the agent does not screen stocks that
  reported earnings within the last 7 days. Implemented via the
  ``earnings_recency_days`` parameter of :func:`apply_filters`.
* **Extreme low P/E exclusion** — Greenblatt's "P/E below 5" guard
  against accounting anomalies. We approximate this by excluding
  stocks with non-positive EBIT (a hard requirement of the formula
  anyway, since negative EBIT makes EY meaningless).

Only stocks passing every filter are eligible for ranking.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger

logger = get_logger("agents.greenblatt.filters")


# Sector exclusion ranges via SIC (Standard Industrial Classification) codes.
# SEC EDGAR assigns one SIC code per filer; we treat the first 4 digits as
# the lookup key.
EXCLUDED_SIC_RANGES: tuple[tuple[int, int], ...] = (
    (4900, 4999),  # Electric, Gas & Sanitary Services (utilities)
    (6000, 6999),  # Finance, Insurance & Real Estate (financials)
)

# Default minimum market cap. Greenblatt recommends $50M for individual
# investors and $1B+ for retirement accounts; we use $1B for the
# institutional-grade default.
DEFAULT_MIN_MARKET_CAP_USD: float = 1_000_000_000.0

# Maximum plausible Earnings Yield (EBIT/EV). Real-world EY rarely
# exceeds 30% even for the cheapest names; values above 1.0 (i.e.,
# EBIT > Enterprise Value) are mathematically possible only for tiny
# distressed companies and almost always indicate XBRL tagging or
# shares-outstanding data anomalies. Capping here protects the
# ranking from being skewed by such artifacts.
DEFAULT_MAX_EARNINGS_YIELD: float = 1.0


def is_share_class_or_preferred(ticker: str) -> bool:
    """Heuristic: filter likely share classes / preferred / warrant rows.

    Examples we want to exclude:

    * ``BRK-A`` / ``BRK-B`` — both share classes; without dedup we'd pick
      both for the same company.
    * ``BAC-PB`` / ``GS-PJ`` — preferred shares.
    * ``XYZ-W`` / ``ABC-WI`` / ``ABC-WS`` — warrants and when-issued.

    Strategy: any ticker containing a hyphen is excluded. yfinance and
    SEC use ``-`` in tickers for these structurally different securities.
    """
    return "-" in ticker


@dataclass(frozen=True)
class FilterResult:
    """Result of running the filter pipeline against one candidate.

    Captures both the boolean pass/fail and the specific reason a
    candidate was rejected, so the strategy log can attribute exclusion
    causes accurately.
    """

    ticker: str
    passed: bool
    rejection_reason: str | None = None


# Industry-name patterns matching the SIC exclusion ranges. Used as a
# fallback when the data source returns an industry string (e.g., FMP's
# ``industry`` field on the free tier) instead of a numeric SIC code.
EXCLUDED_INDUSTRY_KEYWORDS: tuple[str, ...] = (
    "bank",
    "insurance",
    "reit",
    "real estate",
    "asset management",
    "capital market",
    "credit service",
    "financial",  # broad financials catch-all
    "mortgage",
    "utilities",
    "utility",
    "water utilit",
    "gas utilit",
    "electric utilit",
)


def is_excluded_sector(sic_code: str | None) -> bool:
    """Return True if ``sic_code`` indicates an excluded sector.

    Accepts either a numeric SIC code (preferred — first 4 digits compared
    against :data:`EXCLUDED_SIC_RANGES`) or a free-text industry name (the
    fallback when free-tier data sources return industry strings instead
    of SIC codes — matched against :data:`EXCLUDED_INDUSTRY_KEYWORDS`).

    A missing/invalid value is treated as **not excluded** — we cannot
    reject a stock for a sector we cannot identify. The market-cap and
    EBIT filters provide additional protection.
    """
    if not sic_code:
        return False
    s = str(sic_code).strip()
    # Numeric SIC path
    try:
        code = int(s[:4])
        return any(low <= code <= high for low, high in EXCLUDED_SIC_RANGES)
    except (TypeError, ValueError):
        pass
    # Industry-name path
    lower = s.lower()
    return any(keyword in lower for keyword in EXCLUDED_INDUSTRY_KEYWORDS)


def passes_market_cap(
    market_cap_usd: float | None, *, minimum: float = DEFAULT_MIN_MARKET_CAP_USD
) -> bool:
    """True if ``market_cap_usd`` meets the minimum threshold.

    Missing market cap = REJECT. We will not buy a stock whose size we
    cannot verify.
    """
    if market_cap_usd is None:
        return False
    return market_cap_usd >= minimum


def has_positive_ebit(operating_income: float | None) -> bool:
    """True if EBIT (proxy: operating income) is strictly positive.

    Negative EBIT makes Earnings Yield (EBIT/EV) meaningless or negative
    — Greenblatt's framework excludes these. Also covers the
    "extremely low P/E" guard since most P/E < 5 cases are the result
    of accounting anomalies surfacing at the EBIT line.
    """
    return operating_income is not None and operating_income > 0


def has_recent_earnings(
    last_filing_date: date | None,
    *,
    as_of: date,
    cutoff_days: int = 7,
) -> bool:
    """True if the company filed earnings within the last ``cutoff_days``.

    Greenblatt explicitly excludes companies that just reported, since
    their stale data may distort the rankings before the market has
    had time to react.
    """
    if last_filing_date is None:
        return False
    delta = as_of - last_filing_date
    return timedelta(days=0) <= delta < timedelta(days=cutoff_days)


def passes_filters(
    fin: PointInTimeFinancials | None,
    market_cap_usd: float | None,
    *,
    as_of: date,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
    earnings_recency_days: int = 7,
) -> FilterResult:
    """Apply the full filter pipeline to one candidate.

    Order matters — we check cheap conditions first (sector, market
    cap) before pulling in any computed metrics. The earnings-recency
    check is last because it requires looking at the source filing.

    Args:
        fin: Point-in-time financials (or None if unavailable).
        market_cap_usd: Current market cap; computed by the caller from
            shares outstanding × price as of ``as_of``.
        as_of: The rebalance date.
        min_market_cap: Minimum market cap in USD.
        earnings_recency_days: Reject if a filing landed within this
            many days of ``as_of``.
    """
    ticker = fin.ticker if fin else "<unknown>"

    if fin is None:
        return FilterResult(ticker, False, "no point-in-time financials available")

    if is_share_class_or_preferred(ticker):
        return FilterResult(ticker, False, "share class or preferred (hyphen in ticker)")

    if is_excluded_sector(fin.sic_code):
        return FilterResult(ticker, False, f"excluded sector (SIC {fin.sic_code})")

    if not passes_market_cap(market_cap_usd, minimum=min_market_cap):
        return FilterResult(
            ticker,
            False,
            f"market cap ${market_cap_usd or 0:,.0f} below minimum ${min_market_cap:,.0f}",
        )

    if not has_positive_ebit(fin.operating_income):
        return FilterResult(
            ticker,
            False,
            f"non-positive EBIT ({fin.operating_income})",
        )

    if has_recent_earnings(
        fin.source_filing.filing_date,
        as_of=as_of,
        cutoff_days=earnings_recency_days,
    ):
        return FilterResult(
            ticker,
            False,
            f"earnings filed within last {earnings_recency_days} days",
        )

    return FilterResult(ticker, True, None)


def filter_candidates(
    candidates: Iterable[tuple[PointInTimeFinancials | None, float | None]],
    *,
    as_of: date,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
    earnings_recency_days: int = 7,
) -> list[tuple[PointInTimeFinancials, float]]:
    """Filter a batch of candidates, returning the qualifying subset.

    Args:
        candidates: Iterable of ``(financials, market_cap)`` pairs.
        as_of: The rebalance date.
        min_market_cap: Minimum market cap in USD.
        earnings_recency_days: Earnings-recency cutoff.

    Returns:
        List of ``(financials, market_cap)`` pairs that pass every
        filter. Both fields are guaranteed non-None in the result.
    """
    passed: list[tuple[PointInTimeFinancials, float]] = []
    rejected_count: dict[str, int] = {}

    for fin, mcap in candidates:
        result = passes_filters(
            fin,
            mcap,
            as_of=as_of,
            min_market_cap=min_market_cap,
            earnings_recency_days=earnings_recency_days,
        )
        if result.passed and fin is not None and mcap is not None:
            passed.append((fin, mcap))
        else:
            reason = result.rejection_reason or "unknown"
            # Track the broad category for logging
            category = reason.split(" (")[0]
            rejected_count[category] = rejected_count.get(category, 0) + 1

    if rejected_count:
        summary = ", ".join(
            f"{cat}: {n}" for cat, n in sorted(rejected_count.items())
        )
        logger.info(f"{as_of}: filtered {sum(rejected_count.values())} candidates ({summary})")

    return passed


__all__ = [
    "DEFAULT_MAX_EARNINGS_YIELD",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "EXCLUDED_INDUSTRY_KEYWORDS",
    "EXCLUDED_SIC_RANGES",
    "FilterResult",
    "filter_candidates",
    "has_positive_ebit",
    "has_recent_earnings",
    "is_excluded_sector",
    "is_share_class_or_preferred",
    "passes_filters",
    "passes_market_cap",
]
