"""Berkshire Acquisition Criteria — quality gates for the Buffett agent.

Per playbook §4.1, every candidate must pass ALL six criteria:

  1. Adequate size — market cap ≥ $5B (modern Buffett bar)
  2. Demonstrated consistent earning power — positive net income for
     each of the last 10 fiscal years (10-K filings)
  3. High ROE without excessive debt — 5-yr avg ROE ≥ 15% AND
     debt/equity ≤ 0.5
  4. Capable management — proxy: positive operating cash flow each of
     the last 5 years (no quality-of-earnings red flags)
  5. Simple business — proxy: SIC code not in an exclusion list
     (no airlines, no pre-revenue biotech, no commodity miners)
  6. Sensible price — deferred to ranking; this filter only handles
     the boolean quality gates

Plus the Council non-negotiables: positive equity, USD reporting,
no share-class tickers (handled here in the pre-screen).

These are HARD filters per playbook §10 ("must pass all six"). Soft
scoring is appropriate for Neff but not for Buffett — Buffett's whole
point is that he WALKS AWAY when any criterion fails.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.data.sic_codes import sic_for
from core.logger import get_logger
from core.scoring.leverage import debt_to_equity as _shared_debt_to_equity

logger = get_logger("agents.buffett.filters")


# ---- Defaults from playbook §4.1 ------------------------------------------
DEFAULT_MIN_MARKET_CAP_USD: float = 5_000_000_000.0
DEFAULT_MIN_AVG_ROE_PCT: float = 15.0
DEFAULT_MAX_DE: float = 0.5
DEFAULT_EARNINGS_HISTORY_YEARS: int = 10
DEFAULT_OCF_HISTORY_YEARS: int = 5
DEFAULT_ROE_AVG_YEARS: int = 5

#: SIC2 (first 2 digits of SIC code) groups Buffett has explicitly
#: avoided for decades. Sourced from playbook §8 + §10.
EXCLUDED_SIC2: frozenset[int] = frozenset(
    {
        45,  # Transportation by air (airlines — "shoot Wilbur Wright")
        10,  # Metal mining (commodity, no pricing power)
        12,  # Coal mining
        13,  # Oil & gas extraction (cyclical commodity; Buffett's
        # Chevron / OXY positions are exceptions, not the rule)
        14,  # Mining & quarrying of nonmetallic minerals
        29,  # Petroleum refining (commodity)
        17,  # Construction (cyclical, low-margin)
        15,  # Building construction
    }
)


# ---- Concept aliases ------------------------------------------------------
_NET_INCOME_CONCEPT: str = "NetIncomeLoss"
_OCF_CONCEPTS: tuple[str, ...] = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
_EQUITY_CONCEPTS: tuple[str, ...] = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)


@dataclass(frozen=True)
class FilterResult:
    """Outcome of running the Buffett quality gates against a candidate."""

    ticker: str
    passed: bool
    rejection_reason: str | None = None
    # Per-criterion verdicts for audit + downstream UX. Each one is True
    # iff that single criterion was met (or N/A means a soft criterion
    # that didn't apply).
    pass_size: bool = False
    pass_earnings_consistency: bool = False
    pass_roe: bool = False
    pass_low_debt: bool = False
    pass_ocf_consistency: bool = False
    pass_simple_business: bool = False


# ---- Per-stock metric helpers ---------------------------------------------
def debt_to_equity(fin: PointInTimeFinancials | None) -> float | None:
    """D/E, or None when the ratio cannot be honestly established.

    Delegates to :func:`core.scoring.leverage.debt_to_equity`. This used
    to return 0.0 when no debt concept was tagged, which is the best
    possible score on every leverage gate — 37% of the judgeable
    universe passed on no evidence. See that module for why plain None
    is also wrong and what distinguishes the two cases.
    """
    return _shared_debt_to_equity(fin)


def current_roe(fin: PointInTimeFinancials | None) -> float | None:
    """ROE for the most recent fiscal year (snapshot)."""
    if fin is None or fin.total_equity is None or fin.total_equity <= 0:
        return None
    if fin.net_income is None:
        return None
    return fin.net_income / fin.total_equity


def avg_roe_5yr(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    years: int = DEFAULT_ROE_AVG_YEARS,
) -> float | None:
    """5-year average ROE in PERCENT, sourced from EDGAR cache.

    Looks up the most recent {years} fiscal-year (10-K) values of
    NetIncomeLoss and StockholdersEquity, divides pairwise, averages.

    Returns ``None`` if fewer than ``years`` annual pairs are available.
    """
    nis: list[float] = []
    eqs: list[float] = []
    # Walk backwards in 1-year steps. ``latest_value_at`` returns the
    # most recent fact filed on or before the lookup date, so stepping
    # back yields a chain of distinct fiscal years (provided the cache
    # has them).
    seen_years: set[int] = set()
    for i in range(years + 3):  # extra slack for missing years
        lookup = as_of - timedelta(days=int(365.25 * i))
        ni = cache.latest_value_at(
            ticker,
            _NET_INCOME_CONCEPT,
            lookup,
            forms=("10-K",),
            prefer_annual=True,
        )
        eq = None
        for c in _EQUITY_CONCEPTS:
            eq = cache.latest_value_at(
                ticker,
                c,
                lookup,
                forms=("10-K",),
                prefer_annual=True,
            )
            if eq is not None:
                break
        if ni is None or eq is None:
            continue
        if ni.fiscal_year in seen_years:
            continue
        if eq.value <= 0:
            # Negative book equity makes ROE meaningless; skip year.
            continue
        seen_years.add(ni.fiscal_year)
        nis.append(float(ni.value))
        eqs.append(float(eq.value))
        if len(nis) >= years:
            break
    if len(nis) < years:
        return None
    pairs = [n / e for n, e in zip(nis, eqs, strict=False) if e > 0]
    if not pairs:
        return None
    return 100.0 * sum(pairs) / len(pairs)


def has_consistent_earnings(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    years: int = DEFAULT_EARNINGS_HISTORY_YEARS,
) -> bool:
    """True iff the company posted positive net income each fiscal
    year going back ``years`` years from ``as_of``.

    "No turnarounds" per Berkshire's official acquisition criteria.
    """
    seen_years: set[int] = set()
    positive_count = 0
    for i in range(years + 3):
        lookup = as_of - timedelta(days=int(365.25 * i))
        ni = cache.latest_value_at(
            ticker,
            _NET_INCOME_CONCEPT,
            lookup,
            forms=("10-K",),
            prefer_annual=True,
        )
        if ni is None or ni.fiscal_year in seen_years:
            continue
        seen_years.add(ni.fiscal_year)
        if ni.value <= 0:
            return False  # one negative year disqualifies
        positive_count += 1
        if positive_count >= years:
            return True
    return False  # not enough history


def has_consistent_ocf(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    years: int = DEFAULT_OCF_HISTORY_YEARS,
) -> bool:
    """Operating cash flow positive each year for ``years`` years.

    Proxy for "capable management": companies whose net income looks
    great but OCF is negative are flagging quality-of-earnings issues.
    """
    seen_years: set[int] = set()
    positive_count = 0
    for i in range(years + 3):
        lookup = as_of - timedelta(days=int(365.25 * i))
        ocf = None
        for c in _OCF_CONCEPTS:
            ocf = cache.latest_value_at(
                ticker,
                c,
                lookup,
                forms=("10-K",),
                prefer_annual=True,
            )
            if ocf is not None:
                break
        if ocf is None or ocf.fiscal_year in seen_years:
            continue
        seen_years.add(ocf.fiscal_year)
        if ocf.value <= 0:
            return False
        positive_count += 1
        if positive_count >= years:
            return True
    return False


def is_simple_business(fin: PointInTimeFinancials | None) -> bool:
    """SIC-based proxy for "simple, understandable business".

    Returns False if the SIC2 is in the explicit exclusion list per
    Buffett's avoided-sector list (playbook §8 + §10).

    A True return does NOT mean the business is genuinely simple — it
    means the SIC code is not in a known-bad bucket. The LLM moat
    analyzer (live mode) does the real qualitative check.
    """
    if fin is None:
        return False
    sic = sic_for(fin.ticker)
    if sic is None:
        # Unknown SIC — give the benefit of the doubt; LLM will catch.
        return True
    sic2 = sic // 100
    return sic2 not in EXCLUDED_SIC2


# ---- Main filter pipeline -------------------------------------------------
def passes_quality_gates(
    fin: PointInTimeFinancials | None,
    market_cap_usd: float | None,
    *,
    cache: EdgarCache,
    as_of: date,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
    min_avg_roe_pct: float = DEFAULT_MIN_AVG_ROE_PCT,
    max_de: float = DEFAULT_MAX_DE,
    earnings_history_years: int = DEFAULT_EARNINGS_HISTORY_YEARS,
    ocf_history_years: int = DEFAULT_OCF_HISTORY_YEARS,
    roe_avg_years: int = DEFAULT_ROE_AVG_YEARS,
) -> FilterResult:
    """Run the six Berkshire Acquisition Criteria as hard gates.

    Order is cheapest-first so the average rejection happens before
    we touch the EDGAR cache.
    """
    ticker = fin.ticker if fin else "<unknown>"
    if fin is None:
        return FilterResult(ticker, False, "no point-in-time financials")
    if "-" in ticker:
        return FilterResult(ticker, False, "share class or preferred")

    # 1. Adequate size.
    pass_size = market_cap_usd is not None and market_cap_usd >= min_market_cap
    if not pass_size:
        return FilterResult(
            ticker,
            False,
            f"market cap ${market_cap_usd or 0:,.0f} below ${min_market_cap:,.0f}",
            pass_size=False,
        )

    # 5. Simple business (SIC exclusions) — cheap, do early.
    pass_simple = is_simple_business(fin)
    if not pass_simple:
        return FilterResult(
            ticker,
            False,
            f"SIC code in excluded list (sic={fin.sic_code or sic_for(ticker)})",
            pass_size=True,
            pass_simple_business=False,
        )

    # 4 (light): positive book equity sanity check before any ROE math.
    if fin.total_equity is None or fin.total_equity <= 0:
        return FilterResult(
            ticker,
            False,
            "non-positive book equity",
            pass_size=True,
            pass_simple_business=True,
        )

    # 3a. D/E ≤ 0.5.
    de = debt_to_equity(fin)
    if de is None:
        return FilterResult(
            ticker,
            False,
            "D/E undefined (no positive equity, or no debt reported on a sparse balance sheet)",
            pass_size=True,
            pass_simple_business=True,
        )
    pass_low_debt = de <= max_de
    if not pass_low_debt:
        return FilterResult(
            ticker,
            False,
            f"D/E {de:.2f} > {max_de}",
            pass_size=True,
            pass_simple_business=True,
            pass_low_debt=False,
        )

    # 2. Demonstrated consistent earnings — touches EDGAR cache.
    pass_earnings = has_consistent_earnings(
        cache, ticker, as_of, years=earnings_history_years
    )
    if not pass_earnings:
        return FilterResult(
            ticker,
            False,
            f"earnings not positive each of last {earnings_history_years} years",
            pass_size=True,
            pass_simple_business=True,
            pass_low_debt=True,
            pass_earnings_consistency=False,
        )

    # 4. OCF consistency proxy.
    pass_ocf = has_consistent_ocf(cache, ticker, as_of, years=ocf_history_years)
    if not pass_ocf:
        return FilterResult(
            ticker,
            False,
            f"OCF not positive each of last {ocf_history_years} years",
            pass_size=True,
            pass_simple_business=True,
            pass_low_debt=True,
            pass_earnings_consistency=True,
            pass_ocf_consistency=False,
        )

    # 3b. Avg ROE ≥ 15%.
    avg_roe = avg_roe_5yr(cache, ticker, as_of, years=roe_avg_years)
    if avg_roe is None:
        return FilterResult(
            ticker,
            False,
            f"insufficient {roe_avg_years}-yr ROE history",
            pass_size=True,
            pass_simple_business=True,
            pass_low_debt=True,
            pass_earnings_consistency=True,
            pass_ocf_consistency=True,
        )
    pass_roe = avg_roe >= min_avg_roe_pct
    if not pass_roe:
        return FilterResult(
            ticker,
            False,
            f"5-yr avg ROE {avg_roe:.1f}% < {min_avg_roe_pct}%",
            pass_size=True,
            pass_simple_business=True,
            pass_low_debt=True,
            pass_earnings_consistency=True,
            pass_ocf_consistency=True,
            pass_roe=False,
        )

    return FilterResult(
        ticker,
        True,
        None,
        pass_size=True,
        pass_simple_business=True,
        pass_low_debt=True,
        pass_earnings_consistency=True,
        pass_ocf_consistency=True,
        pass_roe=True,
    )


def apply_quality_gates(
    candidates: Iterable[
        tuple[PointInTimeFinancials | None, float | None, float | None]
    ],
    *,
    cache: EdgarCache,
    as_of: date,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
    min_avg_roe_pct: float = DEFAULT_MIN_AVG_ROE_PCT,
    max_de: float = DEFAULT_MAX_DE,
) -> list[tuple[PointInTimeFinancials, float, float]]:
    """Run :func:`passes_quality_gates` over a batch."""
    passed: list[tuple[PointInTimeFinancials, float, float]] = []
    rejected: dict[str, int] = {}
    for fin, mcap, price in candidates:
        r = passes_quality_gates(
            fin,
            mcap,
            cache=cache,
            as_of=as_of,
            min_market_cap=min_market_cap,
            min_avg_roe_pct=min_avg_roe_pct,
            max_de=max_de,
        )
        if r.passed and fin is not None and mcap is not None and price is not None:
            passed.append((fin, mcap, price))
        else:
            cat = (r.rejection_reason or "unknown").split(" (")[0].split(":")[0]
            rejected[cat] = rejected.get(cat, 0) + 1
    if rejected:
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(rejected.items()))
        logger.info(
            f"{as_of}: Buffett quality filter dropped {sum(rejected.values())} ({summary})"
        )
    return passed


__all__ = [
    "DEFAULT_EARNINGS_HISTORY_YEARS",
    "DEFAULT_MAX_DE",
    "DEFAULT_MIN_AVG_ROE_PCT",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_OCF_HISTORY_YEARS",
    "DEFAULT_ROE_AVG_YEARS",
    "EXCLUDED_SIC2",
    "FilterResult",
    "apply_quality_gates",
    "avg_roe_5yr",
    "current_roe",
    "debt_to_equity",
    "has_consistent_earnings",
    "has_consistent_ocf",
    "is_simple_business",
    "passes_quality_gates",
]
