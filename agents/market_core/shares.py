"""Share counts a market capitalisation can actually be built from.

Two corrections stand between a filed share count and a number worth
multiplying by a price.

**Split units.** ``prices.sqlite`` stores split-adjusted prices — NVDA
reads $120.99 on 2024-06-06 where the tape said about $1,210 — while
EDGAR reports what the company filed. Their product is wrong by the
cumulative split factor, and wrong in the direction of what happened to
the company afterwards: winners split forward and come out too small,
failures split backward and come out too large. A size screen built on
the raw product excludes future winners and admits future failures.
:mod:`core.research.splits` restates the count into the price series'
units; verified against NVDA's end-2020 capitalisation, $323.2bn
reconstructed against $323bn true.

**Filed nonsense.** Some counts are simply wrong. ``PKG`` is filed at
94.1 billion shares against an actual 95 million or so — three orders
of magnitude — which computed to a $6,276bn company and, being the
largest thing in the universe, took a 41% weight in a
capitalisation-weighted book and turned it into -0.94% a year. The tell
is not the size but the trading: a $6trn company does not change hands
a few million dollars a day. That check lives in
:func:`core.research.factors.market_capitalisation` and is applied by
the caller.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd

from core.data.edgar_cache import EdgarCache
from core.data.fundamentals_fetcher import MAX_FACT_AGE_DAYS
from core.logger import get_logger
from core.research.splits import (
    COVER_CONCEPTS,
    WEIGHTED_AVERAGE_CONCEPT,
    split_adjusted_counts,
    split_adjusted_share_count,
)

logger = get_logger("agents.market_core.shares")

#: XBRL concepts carrying a share count, best first. The ``dei``
#: cover-page fact is preferred because it is dated at the filing rather
#: than at a period end — a 10-K restates the same concept for every
#: year in its comparative columns, so taking the largest reaches years
#: into the past and reads a count that was stale when filed. Owned by
#: :mod:`core.research.splits`, which also falls back to the weighted
#: average when this series goes dark (audit FUND-8).
SHARE_CONCEPTS: tuple[str, ...] = COVER_CONCEPTS


def split_adjusted_shares(cache: EdgarCache, ticker: str) -> pd.Series:
    """Share count by filing date, restated into the price's units.

    Returns an empty series when the ticker has no usable filings, which
    a caller must read as "cannot tell" rather than "no shares".
    """
    try:
        facts = cache.load_dataframe(ticker)
    # A missing or corrupt parquet is a fact about this ticker, not a
    # reason to fail a screen running across the whole universe.
    except Exception:
        return pd.Series(dtype="float64")
    if facts is None or len(facts) == 0:
        return pd.Series(dtype="float64")
    # Sanity floor, weighted-average fallback and the filer's-restatement
    # check on every detected split (audit FUND-8) live there.
    return split_adjusted_share_count(facts)


#: What the point-in-time loader reads ``shares_outstanding`` from
#: (:meth:`~core.data.fundamentals_fetcher.FundamentalsFetcher.get_field`).
_LOADER_FORMS: tuple[str, ...] = ("10-K", "10-Q")
_SHARE_UNIT = "shares"


def _newest_filed(
    cache: EdgarCache, ticker: str, as_of: date
) -> tuple[date, float] | None:
    """``(filed, count)`` of the newest filing's split-adjusted count.

    Unlike :func:`_newest_known`, a filing that carries only a basic
    weighted average counts too, and wins when it is the newer one; on
    the same filing date the cover or balance-sheet count wins, being
    dated nearer the filing. That is what the point-in-time loader does
    with the same three concepts (the freshest survivor of its chain),
    and it is what keeps a multi-class filer whole: MIR's balance-sheet
    count is one class, 18.75M, and its 10-K tags only the all-class
    weighted average, 229.96M (review of audit FUND-5).

    The evidence is the loader's too: 10-K and 10-Q facts in ``shares``,
    and only a filing holding a share count for a period that ended
    within :data:`~core.data.fundamentals_fetcher.MAX_FACT_AGE_DAYS` of
    ``as_of`` can be the newest. An amendment or a registration
    statement reprints old counts under a new filing date (EONR's
    10-K/A of 2026-04-27 carries its 2024 year-end count), and a 10-K
    may carry nothing newer than last year's (VLGEA's). ``None`` when
    no filing qualifies; the caller falls back to the loader's count.
    """
    try:
        facts = cache.load_dataframe(ticker)
    # Same reasoning as split_adjusted_shares: one unreadable parquet is
    # a fact about this ticker, not a reason to fail the whole screen.
    except Exception:
        return None
    if facts is None or len(facts) == 0:
        return None
    if "form" in facts.columns:
        facts = facts[facts["form"].isin(_LOADER_FORMS)]
    if "unit" in facts.columns:
        facts = facts[facts["unit"] == _SHARE_UNIT]
    if len(facts) == 0:
        return None
    filed_at = pd.to_datetime(facts["filed"], errors="coerce")
    period_end = pd.to_datetime(facts["period_end"], errors="coerce")
    cutoff = pd.Timestamp(as_of - timedelta(days=MAX_FACT_AGE_DAYS))
    current = (period_end >= cutoff) & (filed_at <= pd.Timestamp(as_of))

    def fresh(concepts: tuple[str, ...]) -> set[date]:
        return set(filed_at[current & facts["concept"].isin(concepts)].dt.date)

    # Each series is judged by its own concepts: VRNO's 2024 10-K holds
    # a current weighted average and a cover-series row from 2020, and
    # the filing's freshness must not lend itself to the stale row.
    cover, weighted = split_adjusted_counts(facts)
    best: tuple[date, float] | None = None
    for series, filings in (
        (cover, fresh(COVER_CONCEPTS)),
        (weighted, fresh((WEIGHTED_AVERAGE_CONCEPT,))),
    ):
        known = series[[d in filings for d in series.index]]
        if known.empty:
            continue
        filed, value = known.index[-1], float(known.iloc[-1])
        if value > 0 and (best is None or filed > best[0]):
            best = (filed, value)
    return best


def _newest_known(
    cache: EdgarCache, ticker: str, as_of: date
) -> tuple[date, float] | None:
    """``(filed, count)`` of the newest split-adjusted count filed by ``as_of``."""
    series = split_adjusted_shares(cache, ticker)
    if series.empty:
        return None
    known = series[series.index <= as_of]
    if known.empty:
        return None
    value = float(known.iloc[-1])
    if value <= 0:
        return None
    return known.index[-1], value


def shares_known_at(
    cache: EdgarCache, tickers: list[str], as_of: date
) -> dict[str, float]:
    """Latest split-adjusted share count filed on or before ``as_of``.

    Strictly backwards: a company's next filing cannot inform today's
    portfolio, and letting it would hand the screen a share count that
    had not been published.
    """
    out: dict[str, float] = {}
    for ticker in tickers:
        hit = _newest_known(cache, ticker, as_of)
        if hit is not None:
            out[ticker] = hit[1]
    return out


def market_cap_at(
    cache: EdgarCache | None,
    ticker: str,
    as_of: date,
    price: float | None,
    filed_shares: float | None,
) -> float | None:
    """Market capitalisation in the adjusted price's units (audit FUND-5).

    ``price`` is split-adjusted, so the share count must be too. The
    count used is the one in the newest filing on or before ``as_of`` —
    a 10-Q's cover count beats the last 10-K's, and a 10-K that tags only
    the basic weighted average beats an older 10-Q's cover count
    (:func:`_newest_filed`) — restated for every split detected after
    that filing.

    ``filed_shares`` (the loader's count, as filed) is used only when no
    such count exists: no cache, or no 10-K or 10-Q holding a cover,
    balance-sheet or weighted-average count for a period ending within
    the loader's own
    :data:`~core.data.fundamentals_fetcher.MAX_FACT_AGE_DAYS` bound. A
    series that went dark years ago (AOS's stops in 2015) is not "the
    newest count known" in any sense a screen should act on. That
    fallback carries the old unit risk, and it is the same number every
    agent used before this helper existed.

    Returns ``None`` when there is no usable price or no positive count:
    a zero share count is a parse failure, not a worthless company.
    """
    if price is None or not math.isfinite(price) or price <= 0:
        return None
    shares: float | None = None
    if cache is not None:
        hit = _newest_filed(cache, ticker, as_of)
        if hit is not None:
            shares = hit[1]
        else:
            logger.debug(
                f"{ticker} @ {as_of}: no 10-K/10-Q share count for a period "
                f"ending within {MAX_FACT_AGE_DAYS} days; "
                f"using the loader's count"
            )
    if shares is None:
        shares = filed_shares
    if shares is None or not math.isfinite(shares) or shares <= 0:
        return None
    return price * shares


__all__ = [
    "SHARE_CONCEPTS",
    "market_cap_at",
    "shares_known_at",
    "split_adjusted_shares",
]
