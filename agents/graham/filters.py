"""Universe filters for Graham's classic Net-Net strategy.

Graham's deepest-value test (Section 4.3 of ``playbook.md``):

    Net Current Asset Value (NCAV)  = Current Assets − Total Liabilities
    Net-Net buy condition          = Price ≤ ⅔ × NCAV per share

Plus the Graham non-negotiables:

* Trailing 12-month EPS > 0 (Graham excluded loss-makers)
* Positive book value
* Manageable debt: D/E ≤ 1.0 (mirrors Schloss's hard rule)
* Market-cap floor (per spec: $500M)

Filters Graham specified that we cannot enforce with current data
(documented as deferred): 20-year dividend history, 10-year positive
earnings record. The cache + S&P-500-history window only goes back to
~2008, so multi-decade history checks are infeasible. The active
2-year-revenue + positive-book filters at the universe level handle
the most important slice of "earnings stability."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger

logger = get_logger("agents.graham.filters")


DEFAULT_NCAV_DISCOUNT_FACTOR: float = 2.0 / 3.0  # Graham's 67% rule
DEFAULT_MAX_DE: float = 1.0
DEFAULT_MIN_MARKET_CAP_USD: float = 500_000_000.0

# Defensive Investor thresholds (The Intelligent Investor, Ch. 14).
# Used as automatic fallback when < 10 classic Net-Nets are available
# in the universe (i.e. the modern reality, post-1970).
DEFAULT_DEFENSIVE_MAX_PE: float = 15.0
DEFAULT_DEFENSIVE_MAX_PB: float = 1.5
DEFAULT_DEFENSIVE_MIN_CURRENT_RATIO: float = 2.0
DEFAULT_NET_NET_FALLBACK_THRESHOLD: int = 10


@dataclass(frozen=True)
class FilterResult:
    """Pass/fail with attribution."""

    ticker: str
    passed: bool
    rejection_reason: str | None = None


def ncav_per_share(fin: PointInTimeFinancials | None) -> float | None:
    """Net Current Asset Value per share.

    NCAV = Current Assets − Total Liabilities. Returns None when any
    component is missing or shares outstanding is non-positive.
    """
    if fin is None:
        return None
    if (
        fin.current_assets is None
        or fin.total_liabilities is None
        or fin.shares_outstanding is None
        or fin.shares_outstanding <= 0
    ):
        return None
    ncav_total = fin.current_assets - fin.total_liabilities
    return ncav_total / fin.shares_outstanding


def price_to_ncav(price: float | None, fin: PointInTimeFinancials | None) -> float | None:
    """Price as a multiple of NCAV per share.

    A value of 0.67 means price = ⅔ × NCAV (Graham's threshold).
    Values < 0.67 are deeper discounts. Returns None when NCAV is
    non-positive (the formula is meaningless then) or price missing.
    """
    ncav = ncav_per_share(fin)
    if ncav is None or ncav <= 0:
        return None
    if price is None or price <= 0:
        return None
    return price / ncav


def debt_to_equity(fin: PointInTimeFinancials | None) -> float | None:
    if fin is None or fin.total_equity is None or fin.total_equity <= 0:
        return None
    debt = fin.total_debt
    if debt is None:
        debt = fin.long_term_debt
    if debt is None:
        return 0.0
    return debt / fin.total_equity


def pe_ratio(price: float | None, fin: PointInTimeFinancials | None) -> float | None:
    """Trailing P/E using diluted EPS when available, else basic.

    Used by the Defensive Investor screen — Graham wanted P/E ≤ 15 of
    average earnings.
    """
    if fin is None or price is None or price <= 0:
        return None
    eps = fin.eps_diluted if fin.eps_diluted is not None else fin.eps_basic
    if eps is None or eps <= 0:
        return None
    return price / eps


def pb_ratio(
    market_cap: float | None, fin: PointInTimeFinancials | None
) -> float | None:
    """Price / book = market cap / total equity.

    Defensive Investor threshold: ≤ 1.5×.
    """
    if fin is None or market_cap is None or market_cap <= 0:
        return None
    eq = fin.total_equity
    if eq is None or eq <= 0:
        return None
    return market_cap / eq


def current_ratio(fin: PointInTimeFinancials | None) -> float | None:
    """Current assets / current liabilities.

    Defensive Investor threshold: ≥ 2.0× (Graham's "adequate financial
    condition" rule).
    """
    if fin is None or fin.current_assets is None:
        return None
    cl = fin.current_liabilities
    if cl is None or cl <= 0:
        return None
    return fin.current_assets / cl


def passes_filters(
    fin: PointInTimeFinancials | None,
    market_cap_usd: float | None,
    price: float | None,
    *,
    as_of: date,
    max_p_ncav: float = DEFAULT_NCAV_DISCOUNT_FACTOR,
    max_de: float = DEFAULT_MAX_DE,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
) -> FilterResult:
    """Apply the Graham Net-Net filter pipeline."""
    ticker = fin.ticker if fin else "<unknown>"

    if fin is None:
        return FilterResult(ticker, False, "no point-in-time financials available")

    if "-" in ticker:
        return FilterResult(ticker, False, "share class or preferred")

    if market_cap_usd is None or market_cap_usd < min_market_cap:
        return FilterResult(
            ticker,
            False,
            f"market cap ${market_cap_usd or 0:,.0f} below ${min_market_cap:,.0f}",
        )

    if fin.net_income is not None and fin.net_income <= 0:
        return FilterResult(ticker, False, "non-positive trailing net income")

    de = debt_to_equity(fin)
    if de is None:
        return FilterResult(ticker, False, "D/E undefined (non-positive equity)")
    if de > max_de:
        return FilterResult(ticker, False, f"D/E {de:.2f} > {max_de} threshold")

    p_ncav = price_to_ncav(price, fin)
    if p_ncav is None:
        return FilterResult(
            ticker,
            False,
            "P/NCAV undefined (non-positive NCAV or missing price)",
        )
    if p_ncav > max_p_ncav:
        return FilterResult(
            ticker, False, f"P/NCAV {p_ncav:.3f} > {max_p_ncav:.3f} threshold"
        )

    return FilterResult(ticker, True, None)


def filter_candidates(
    candidates: Iterable[
        tuple[PointInTimeFinancials | None, float | None, float | None]
    ],
    *,
    as_of: date,
    max_p_ncav: float = DEFAULT_NCAV_DISCOUNT_FACTOR,
    max_de: float = DEFAULT_MAX_DE,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
) -> list[tuple[PointInTimeFinancials, float, float]]:
    passed: list[tuple[PointInTimeFinancials, float, float]] = []
    rejected: dict[str, int] = {}
    for fin, mcap, price in candidates:
        r = passes_filters(
            fin,
            mcap,
            price,
            as_of=as_of,
            max_p_ncav=max_p_ncav,
            max_de=max_de,
            min_market_cap=min_market_cap,
        )
        if r.passed and fin is not None and mcap is not None and price is not None:
            passed.append((fin, mcap, price))
        else:
            cat = (r.rejection_reason or "unknown").split(" (")[0]
            rejected[cat] = rejected.get(cat, 0) + 1
    if rejected:
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(rejected.items()))
        logger.info(
            f"{as_of}: filtered {sum(rejected.values())} candidates ({summary})"
        )
    return passed


def passes_defensive_filters(
    fin: PointInTimeFinancials | None,
    market_cap_usd: float | None,
    price: float | None,
    *,
    as_of: date,
    max_pe: float = DEFAULT_DEFENSIVE_MAX_PE,
    max_pb: float = DEFAULT_DEFENSIVE_MAX_PB,
    min_current_ratio: float = DEFAULT_DEFENSIVE_MIN_CURRENT_RATIO,
    max_de: float = DEFAULT_MAX_DE,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
) -> FilterResult:
    """Graham's Defensive Investor (The Intelligent Investor, Ch. 14).

    All four hard checks:

    * P/E ≤ 15 (Graham allowed up to 15× average earnings)
    * P/B ≤ 1.5 (no more than 1.5× book)
    * Current ratio ≥ 2.0 (adequate financial condition)
    * D/E ≤ 1.0 + positive trailing earnings + size floor
    """
    ticker = fin.ticker if fin else "<unknown>"

    if fin is None:
        return FilterResult(ticker, False, "no point-in-time financials available")

    if "-" in ticker:
        return FilterResult(ticker, False, "share class or preferred")

    if market_cap_usd is None or market_cap_usd < min_market_cap:
        return FilterResult(
            ticker,
            False,
            f"market cap ${market_cap_usd or 0:,.0f} below ${min_market_cap:,.0f}",
        )

    if fin.net_income is None or fin.net_income <= 0:
        return FilterResult(ticker, False, "non-positive trailing net income")

    de = debt_to_equity(fin)
    if de is None:
        return FilterResult(ticker, False, "D/E undefined (non-positive equity)")
    if de > max_de:
        return FilterResult(ticker, False, f"D/E {de:.2f} > {max_de} threshold")

    pe = pe_ratio(price, fin)
    if pe is None:
        return FilterResult(ticker, False, "P/E undefined (non-positive EPS or price)")
    if pe > max_pe:
        return FilterResult(ticker, False, f"P/E {pe:.2f} > {max_pe} threshold")

    pb = pb_ratio(market_cap_usd, fin)
    if pb is None:
        return FilterResult(ticker, False, "P/B undefined")
    if pb > max_pb:
        return FilterResult(ticker, False, f"P/B {pb:.2f} > {max_pb} threshold")

    cr = current_ratio(fin)
    if cr is None:
        return FilterResult(ticker, False, "current ratio undefined")
    if cr < min_current_ratio:
        return FilterResult(
            ticker, False, f"current ratio {cr:.2f} < {min_current_ratio} threshold"
        )

    return FilterResult(ticker, True, None)


def filter_defensive_candidates(
    candidates: Iterable[
        tuple[PointInTimeFinancials | None, float | None, float | None]
    ],
    *,
    as_of: date,
    max_pe: float = DEFAULT_DEFENSIVE_MAX_PE,
    max_pb: float = DEFAULT_DEFENSIVE_MAX_PB,
    min_current_ratio: float = DEFAULT_DEFENSIVE_MIN_CURRENT_RATIO,
    max_de: float = DEFAULT_MAX_DE,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
) -> list[tuple[PointInTimeFinancials, float, float]]:
    """Batch wrapper for :func:`passes_defensive_filters`."""
    passed: list[tuple[PointInTimeFinancials, float, float]] = []
    rejected: dict[str, int] = {}
    for fin, mcap, price in candidates:
        r = passes_defensive_filters(
            fin,
            mcap,
            price,
            as_of=as_of,
            max_pe=max_pe,
            max_pb=max_pb,
            min_current_ratio=min_current_ratio,
            max_de=max_de,
            min_market_cap=min_market_cap,
        )
        if r.passed and fin is not None and mcap is not None and price is not None:
            passed.append((fin, mcap, price))
        else:
            cat = (r.rejection_reason or "unknown").split(" (")[0]
            rejected[cat] = rejected.get(cat, 0) + 1
    if rejected:
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(rejected.items()))
        logger.info(
            f"{as_of}: defensive filter dropped {sum(rejected.values())} "
            f"({summary})"
        )
    return passed


__all__ = [
    "DEFAULT_DEFENSIVE_MAX_PB",
    "DEFAULT_DEFENSIVE_MAX_PE",
    "DEFAULT_DEFENSIVE_MIN_CURRENT_RATIO",
    "DEFAULT_MAX_DE",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_NCAV_DISCOUNT_FACTOR",
    "DEFAULT_NET_NET_FALLBACK_THRESHOLD",
    "FilterResult",
    "current_ratio",
    "debt_to_equity",
    "filter_candidates",
    "filter_defensive_candidates",
    "ncav_per_share",
    "passes_defensive_filters",
    "passes_filters",
    "pb_ratio",
    "pe_ratio",
    "price_to_ncav",
]
