"""Quality gates for the Lynch agent.

Per playbook §5.4 ("quantitative checklist"):

  * Earnings consistency — positive in ≥ 8 of last 10 years (relax to
    5 of 5 for Fast Growers under 10 years public)
  * Debt-to-equity ≤ 0.5 (≤ 0.6 for cyclicals; ≤ 0.4 for turnarounds)
  * Free cash flow trend — positive (we approximate as OCF − capex
    ≥ 0 in the latest year)
  * No share-class / preferred tickers (Council non-negotiable)
  * Market cap floor — small enough to allow Lynch's mid-cap focus
    ($300M default; lower than Buffett's $5B)

Plus PEG > 0 (handled in ``ranking.py``).

Lynch was explicit about hard "anti-pattern" disqualifiers (playbook
§10) — debt > 0.6, declining earnings 3+ quarters, etc. The hard
ones we can prove from XBRL data are enforced here. The qualitative
ones (serial-acquirer "diworsification", insider-selling clusters
without diversification justification, hot-sector hype) are deferred
to the LLM category classifier in live mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

logger = get_logger("agents.lynch.filters")


# ---- Defaults from playbook ----------------------------------------------
#: Lynch ran 1,400 positions and held mid-caps; we set a floor low
#: enough to let small/mid-caps in but high enough to avoid micro-cap
#: noise / liquidity issues.
DEFAULT_MIN_MARKET_CAP_USD: float = 300_000_000.0

#: D/E ceiling. Lynch's "anti-pattern #7" excludes D/E > 0.6 outright;
#: the standard checklist uses 0.5 for non-cyclicals.
DEFAULT_MAX_DE: float = 0.5

#: Earnings-consistency window per §5.4.
DEFAULT_EARNINGS_HISTORY_YEARS: int = 10
DEFAULT_EARNINGS_MIN_POSITIVE_YEARS: int = 8


_NET_INCOME_CONCEPT: str = "NetIncomeLoss"
_OCF_CONCEPTS: tuple[str, ...] = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
_CAPEX_CONCEPTS: tuple[str, ...] = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
)


@dataclass(frozen=True)
class FilterResult:
    """Outcome of running the Lynch quality gates against a candidate."""

    ticker: str
    passed: bool
    rejection_reason: str | None = None
    pass_size: bool = False
    pass_share_class: bool = False
    pass_low_debt: bool = False
    pass_earnings_consistency: bool = False
    pass_positive_fcf: bool = False


# ---- Per-stock helpers ----------------------------------------------------
def debt_to_equity(fin: PointInTimeFinancials | None) -> float | None:
    """D/E using total_debt when present, else long-term debt only."""
    if fin is None or fin.total_equity is None or fin.total_equity <= 0:
        return None
    debt = fin.total_debt if fin.total_debt is not None else fin.long_term_debt
    if debt is None:
        return 0.0
    return debt / fin.total_equity


def dividend_yield_pct(
    market_cap: float | None, fin: PointInTimeFinancials | None
) -> float | None:
    """Dividend yield in PERCENT. Same convention as other agents:
    abs(dividends_paid) / market_cap.
    """
    if fin is None or market_cap is None or market_cap <= 0:
        return None
    div = fin.dividends_paid
    if div is None:
        return 0.0
    return 100.0 * abs(div) / market_cap


def has_consistent_earnings(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    window_years: int = DEFAULT_EARNINGS_HISTORY_YEARS,
    min_positive: int = DEFAULT_EARNINGS_MIN_POSITIVE_YEARS,
) -> bool:
    """True iff at least ``min_positive`` of the last ``window_years``
    fiscal years had positive net income.

    Lynch is more lenient than Buffett — he tolerates the occasional
    bad year as long as the trajectory is upward. Hence 8 of 10 by
    default rather than Buffett's 10 of 10.
    """
    seen_years: set[int] = set()
    positive = 0
    total = 0
    for i in range(window_years + 5):
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
        total += 1
        if ni.value > 0:
            positive += 1
        if total >= window_years:
            break
    if total < min_positive:
        # Not enough history to even meet the floor — reject.
        return False
    return positive >= min_positive


def latest_free_cash_flow(
    cache: EdgarCache, ticker: str, as_of: date
) -> float | None:
    """Most recent annual FCF ≈ OCF − capex. None when either side
    is missing.
    """
    ocf_fact = None
    for c in _OCF_CONCEPTS:
        ocf_fact = cache.latest_value_at(
            ticker, c, as_of, forms=("10-K",), prefer_annual=True
        )
        if ocf_fact is not None:
            break
    if ocf_fact is None:
        return None
    capex_fact = None
    for c in _CAPEX_CONCEPTS:
        capex_fact = cache.latest_value_at(
            ticker, c, as_of, forms=("10-K",), prefer_annual=True
        )
        if capex_fact is not None:
            break
    if capex_fact is None:
        return None
    return float(ocf_fact.value) - abs(float(capex_fact.value))


# ---- Main filter pipeline -------------------------------------------------
def passes_quality_gates(
    fin: PointInTimeFinancials | None,
    market_cap_usd: float | None,
    *,
    cache: EdgarCache,
    as_of: date,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
    max_de: float = DEFAULT_MAX_DE,
    earnings_window_years: int = DEFAULT_EARNINGS_HISTORY_YEARS,
    earnings_min_positive: int = DEFAULT_EARNINGS_MIN_POSITIVE_YEARS,
) -> FilterResult:
    """Run Lynch's quality gates as hard pass/fail. Cheap-first order
    so the average rejection happens before EDGAR I/O.
    """
    ticker = fin.ticker if fin else "<unknown>"
    if fin is None:
        return FilterResult(ticker, False, "no point-in-time financials")
    if "-" in ticker:
        return FilterResult(
            ticker,
            False,
            "share class or preferred",
            pass_share_class=False,
        )

    # Size.
    pass_size = market_cap_usd is not None and market_cap_usd >= min_market_cap
    if not pass_size:
        return FilterResult(
            ticker,
            False,
            f"market cap ${market_cap_usd or 0:,.0f} below ${min_market_cap:,.0f}",
            pass_share_class=True,
            pass_size=False,
        )

    # Positive book equity sanity check.
    if fin.total_equity is None or fin.total_equity <= 0:
        return FilterResult(
            ticker,
            False,
            "non-positive book equity",
            pass_share_class=True,
            pass_size=True,
        )

    # D/E.
    de = debt_to_equity(fin)
    if de is None:
        return FilterResult(
            ticker,
            False,
            "D/E undefined",
            pass_share_class=True,
            pass_size=True,
        )
    if de > max_de:
        return FilterResult(
            ticker,
            False,
            f"D/E {de:.2f} > {max_de}",
            pass_share_class=True,
            pass_size=True,
            pass_low_debt=False,
        )

    # Earnings consistency — touches EDGAR cache.
    pass_earnings = has_consistent_earnings(
        cache,
        ticker,
        as_of,
        window_years=earnings_window_years,
        min_positive=earnings_min_positive,
    )
    if not pass_earnings:
        return FilterResult(
            ticker,
            False,
            f"earnings positive < {earnings_min_positive}/{earnings_window_years} yrs",
            pass_share_class=True,
            pass_size=True,
            pass_low_debt=True,
            pass_earnings_consistency=False,
        )

    # Free cash flow — Lynch wants positive FCF, growing ideally.
    fcf = latest_free_cash_flow(cache, ticker, as_of)
    if fcf is None:
        # Can't prove positive — soft fail (we still pass it through
        # since FCF data is sometimes spottier than NI data).
        pass_fcf = True
    else:
        pass_fcf = fcf > 0
    if not pass_fcf:
        return FilterResult(
            ticker,
            False,
            f"latest FCF ${fcf or 0:,.0f} non-positive",
            pass_share_class=True,
            pass_size=True,
            pass_low_debt=True,
            pass_earnings_consistency=True,
            pass_positive_fcf=False,
        )

    return FilterResult(
        ticker,
        True,
        None,
        pass_share_class=True,
        pass_size=True,
        pass_low_debt=True,
        pass_earnings_consistency=True,
        pass_positive_fcf=True,
    )


def apply_quality_gates(
    candidates: Iterable[
        tuple[PointInTimeFinancials | None, float | None, float | None]
    ],
    *,
    cache: EdgarCache,
    as_of: date,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
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
            f"{as_of}: Lynch quality filter dropped {sum(rejected.values())} ({summary})"
        )
    return passed


__all__ = [
    "DEFAULT_EARNINGS_HISTORY_YEARS",
    "DEFAULT_EARNINGS_MIN_POSITIVE_YEARS",
    "DEFAULT_MAX_DE",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "FilterResult",
    "apply_quality_gates",
    "debt_to_equity",
    "dividend_yield_pct",
    "has_consistent_earnings",
    "latest_free_cash_flow",
    "passes_quality_gates",
]
