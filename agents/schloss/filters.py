"""Universe filters for the Walter Schloss deep-value strategy.

Implements the gates from Section 4.1 of ``playbook.md`` adapted for
the data we actually have point-in-time:

* **Primary cheapness gate** — P/B < 0.75 (book value below market).
  Slightly stricter than the playbook's 0.8 default to focus on the
  deeper-discount segment Schloss preferred.
* **Manageable debt** — Debt/Equity ≤ 1.0. Schloss's hard rule from
  his 16 Rules.
* **Positive book value** — total_equity > 0. A negative-book company
  cannot have a meaningful P/B and is almost always in distress.
* **Long operating history** — at least 5 years of EDGAR filings
  visible at as_of. The playbook specifies 15 years; we use 5 because
  the cache is shallower and Schloss's "survived a recession" intent
  is well-served by 5 years that include the COVID downturn (2020).
* **Earnings stability (relaxed)** — net income positive in the most
  recent 12 months. Schloss tolerated occasional losses but avoided
  serial money-losers.

Filters not implemented (documented as deferred):

* **Familiar industry / sector exclusions** — XBRL alone has no SIC
  code. The Greenblatt agent has the same gap.
* **Insider ownership ≥ 5%** — requires Form 4/DEF-14A parsing, not
  in the cache.
**Multi-year low / 50% below 5-year high** — now implemented, and it
took two steps. :func:`is_distressed_price` encoded the rule and
:func:`passes_filters` gated on it behind ``require_distressed_price``,
which defaulted to False and which no caller ever passed — a rule
written, tested, and unreachable. It is reached now: pass
``price_history`` to :func:`filter_candidates` and the strategy supplies
a lookup backed by the price cache, so the "expensive" objection above
no longer applies. Nothing is fetched per candidate; the extremes are
read from bars already on disk, and a ticker with no cached history
returns ``(None, None)``, which the filter reads as "cannot tell" and
rejects. Schloss bought capitulation, and a name we cannot show is
beaten down has not shown it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger
from core.scoring.leverage import debt_to_equity as _shared_debt_to_equity

logger = get_logger("agents.schloss.filters")

#: ``(ticker, as_of) -> (low_52w, high_5y)``. Kept as a callable so
#: the filter never needs the price loader itself — the strategy owns
#: that dependency and hands over just the two numbers.
PriceHistoryLookup = Callable[[str, date], tuple[float | None, float | None]]


DEFAULT_MAX_PB: float = 0.75
DEFAULT_MAX_DE: float = 1.0
DEFAULT_MIN_YEARS_PUBLIC: int = 5
DEFAULT_MIN_MARKET_CAP_USD: float = 300_000_000.0  # micro-cap floor


@dataclass(frozen=True)
class FilterResult:
    """Pass/fail with attribution for one candidate."""

    ticker: str
    passed: bool
    rejection_reason: str | None = None


def book_value_per_share(
    fin: PointInTimeFinancials | None,
) -> float | None:
    """Equity per share. Returns None when either component is missing
    or non-positive (a non-positive book yields a meaningless P/B)."""
    if fin is None:
        return None
    if fin.total_equity is None or fin.shares_outstanding is None:
        return None
    if fin.total_equity <= 0 or fin.shares_outstanding <= 0:
        return None
    return fin.total_equity / fin.shares_outstanding


def tangible_common_equity(fin: PointInTimeFinancials | None) -> float | None:
    """Stated equity less goodwill and other intangibles.

    Schloss bought below book because book was an estimate of what the
    business would fetch if broken up. Goodwill is the premium a prior
    management paid on an acquisition; it has no liquidation value and
    it is the first thing written off when things go wrong. Counting it
    as book is counting the very optimism the discount is supposed to
    protect against.

    A filer that tags neither concept plausibly carries neither, so
    absence is read as zero rather than as unknown — the balance-sheet
    corroboration is the equity figure itself, which must be present.
    """
    if fin is None or fin.total_equity is None:
        return None
    goodwill = fin.goodwill or 0.0
    intangibles = fin.intangible_assets or 0.0
    return fin.total_equity - goodwill - intangibles


def tangible_book_value_per_share(
    fin: PointInTimeFinancials | None,
) -> float | None:
    """Tangible common equity per share; None when non-positive.

    A company whose intangibles exceed its equity has no tangible book
    at all, and a P/B computed against a negative denominator is not a
    small number — it is a meaningless one.
    """
    tce = tangible_common_equity(fin)
    if tce is None or tce <= 0:
        return None
    if fin is None or not fin.shares_outstanding or fin.shares_outstanding <= 0:
        return None
    return tce / fin.shares_outstanding


def price_to_book(
    price: float | None,
    fin: PointInTimeFinancials | None,
    *,
    tangible: bool = True,
) -> float | None:
    """P/B against tangible book by default.

    ``tangible=False`` returns the stated-equity ratio, which is what
    this used to compute unconditionally.
    """
    bvps = (
        tangible_book_value_per_share(fin) if tangible else book_value_per_share(fin)
    )
    if bvps is None or price is None or price <= 0:
        return None
    return price / bvps


#: How close to the 52-week low still counts as "near" it. Schloss was
#: buying capitulation, not the exact tick.
DEFAULT_NEAR_LOW_TOLERANCE_PCT: float = 10.0

#: Alternative route in: down at least this much from the five-year
#: high. The playbook writes the two as OR — reject only if *neither*
#: holds — so a name well off its multi-year peak qualifies even if it
#: has bounced off the recent low.
DEFAULT_MIN_DRAWDOWN_FROM_HIGH_PCT: float = 50.0


def is_distressed_price(
    price: float | None,
    *,
    low_52w: float | None,
    high_5y: float | None,
    near_low_tolerance_pct: float = DEFAULT_NEAR_LOW_TOLERANCE_PCT,
    min_drawdown_pct: float = DEFAULT_MIN_DRAWDOWN_FROM_HIGH_PCT,
) -> bool | None:
    """Schloss's entry condition: near the 52-week low, OR far off the
    five-year high.

    The playbook states it as a rejection — "if neither, REJECT" — so
    the two routes are alternatives, not a conjunction. A stock that has
    fallen 70% over three years and stabilised is a Schloss situation
    even though it is no longer making new lows.

    Returns ``None`` when neither reference price is available, so the
    caller can distinguish "does not qualify" from "cannot tell".
    """
    if price is None or price <= 0:
        return None
    near_low = None
    if low_52w is not None and low_52w > 0:
        near_low = price <= low_52w * (1.0 + near_low_tolerance_pct / 100.0)
    off_high = None
    if high_5y is not None and high_5y > 0:
        off_high = (1.0 - price / high_5y) * 100.0 >= min_drawdown_pct
    if near_low is None and off_high is None:
        return None
    return bool(near_low) or bool(off_high)


def debt_to_equity(fin: PointInTimeFinancials | None) -> float | None:
    """D/E, or None when the ratio cannot be honestly established.

    Delegates to :func:`core.scoring.leverage.debt_to_equity`. This used
    to return 0.0 when no debt concept was tagged, which is the best
    possible score on every leverage gate — 37% of the judgeable
    universe passed on no evidence. See that module for why plain None
    is also wrong and what distinguishes the two cases. Schloss cares
    more than most: "little or no debt" is one of his sixteen rules, and
    it was being satisfied by absence of data.
    """
    return _shared_debt_to_equity(fin)


def years_public(
    fin: PointInTimeFinancials | None,
    *,
    as_of: date,
) -> int | None:
    """Estimate years of public filings using the source filing date."""
    if fin is None or fin.source_filing is None:
        return None
    earliest = fin.source_filing.filing_date
    return max(0, (as_of - earliest).days // 365)


def passes_filters(
    fin: PointInTimeFinancials | None,
    market_cap_usd: float | None,
    price: float | None,
    *,
    as_of: date,
    max_pb: float = DEFAULT_MAX_PB,
    max_de: float = DEFAULT_MAX_DE,
    min_years_public: int = DEFAULT_MIN_YEARS_PUBLIC,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
    low_52w: float | None = None,
    high_5y: float | None = None,
    require_distressed_price: bool = False,
) -> FilterResult:
    """Apply the full Schloss filter pipeline to one candidate.

    ``require_distressed_price`` is opt-in because it needs price
    history the caller has to supply; with it off the behaviour is
    unchanged.
    """
    ticker = fin.ticker if fin else "<unknown>"

    if fin is None:
        return FilterResult(ticker, False, "no point-in-time financials available")

    # Same dedup as Greenblatt: any ticker with a hyphen is a share
    # class / preferred / warrant — not a primary common-stock listing.
    if "-" in ticker:
        return FilterResult(ticker, False, "share class or preferred (hyphen in ticker)")

    if market_cap_usd is None or market_cap_usd < min_market_cap:
        return FilterResult(
            ticker,
            False,
            f"market cap ${market_cap_usd or 0:,.0f} below minimum ${min_market_cap:,.0f}",
        )

    pb = price_to_book(price, fin)
    if pb is None:
        return FilterResult(
            ticker,
            False,
            "P/B unavailable (no tangible book, or missing price or shares)",
        )
    if pb >= max_pb:
        return FilterResult(
            ticker, False, f"P/B {pb:.3f} >= {max_pb} threshold"
        )

    de = debt_to_equity(fin)
    if de is None:
        return FilterResult(
            ticker, False, "D/E undefined (no positive equity, or no debt reported on a sparse balance sheet)"
        )
    if de > max_de:
        return FilterResult(
            ticker, False, f"D/E {de:.2f} > {max_de} threshold"
        )

    if fin.net_income is not None and fin.net_income < 0:
        # Schloss tolerated occasional losses, but the most recent
        # period being negative is a strong negative signal alongside
        # a low P/B — many such candidates are deteriorating, not
        # cheap.
        return FilterResult(ticker, False, "negative net income (most recent filing)")

    if require_distressed_price:
        distressed = is_distressed_price(
            price, low_52w=low_52w, high_5y=high_5y
        )
        if distressed is None:
            return FilterResult(ticker, False, "no price history for the low check")
        if not distressed:
            return FilterResult(
                ticker,
                False,
                "not near a 52-week low nor 50% off the 5-year high",
            )

    yp = years_public(fin, as_of=as_of)
    if yp is None or yp < min_years_public:
        return FilterResult(
            ticker,
            False,
            f"years public {yp if yp is not None else 'unknown'} < {min_years_public}",
        )

    return FilterResult(ticker, True, None)


def filter_candidates(
    candidates: Iterable[
        tuple[PointInTimeFinancials | None, float | None, float | None]
    ],
    *,
    as_of: date,
    max_pb: float = DEFAULT_MAX_PB,
    max_de: float = DEFAULT_MAX_DE,
    min_years_public: int = DEFAULT_MIN_YEARS_PUBLIC,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
    price_history: PriceHistoryLookup | None = None,
) -> list[tuple[PointInTimeFinancials, float, float]]:
    """Run the filter pipeline over a batch of candidates.

    ``candidates`` is an iterable of ``(financials, market_cap, price)``
    tuples — the strategy precomputes price and market cap so the
    filter doesn't need access to the price loader directly.

    ``price_history`` turns on the multi-year-low condition. Pass a
    callable returning ``(low_52w, high_5y)`` for a ticker and date; the
    strategy supplies one backed by the price cache. Left None the
    condition is skipped, which is what every caller did before it
    existed — the rule was written, tested, and reachable only by
    passing a flag nobody passed.
    """
    passed: list[tuple[PointInTimeFinancials, float, float]] = []
    rejected: dict[str, int] = {}

    for fin, mcap, price in candidates:
        low_52w = high_5y = None
        if price_history is not None and fin is not None:
            low_52w, high_5y = price_history(fin.ticker, as_of)
        result = passes_filters(
            fin,
            mcap,
            price,
            as_of=as_of,
            max_pb=max_pb,
            max_de=max_de,
            min_years_public=min_years_public,
            min_market_cap=min_market_cap,
            low_52w=low_52w,
            high_5y=high_5y,
            require_distressed_price=price_history is not None,
        )
        if result.passed and fin is not None and mcap is not None and price is not None:
            passed.append((fin, mcap, price))
        else:
            reason = (result.rejection_reason or "unknown").split(" (")[0]
            rejected[reason] = rejected.get(reason, 0) + 1

    if rejected:
        summary = ", ".join(f"{cat}: {n}" for cat, n in sorted(rejected.items()))
        logger.info(
            f"{as_of}: filtered {sum(rejected.values())} candidates ({summary})"
        )
    return passed


__all__ = [
    "DEFAULT_MAX_DE",
    "DEFAULT_MAX_PB",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_MIN_YEARS_PUBLIC",
    "FilterResult",
    "PriceHistoryLookup",
    "book_value_per_share",
    "debt_to_equity",
    "filter_candidates",
    "passes_filters",
    "price_to_book",
    "years_public",
]
