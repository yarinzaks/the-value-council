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
from datetime import date

import pandas as pd

from core.data.edgar_cache import EdgarCache
from core.data.fundamentals_fetcher import MAX_FACT_AGE_DAYS
from core.logger import get_logger
from core.research.splits import COVER_CONCEPTS, split_adjusted_share_count

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
    count used is the newest one filed on or before ``as_of`` — a 10-Q's
    cover count beats the last 10-K's, which is what the point-in-time
    loader's ``shares_outstanding`` hands back — restated for every
    split detected after that filing (:func:`shares_known_at`).

    ``filed_shares`` (the loader's count, as filed) is used only when no
    such count exists: no cache, a ticker without cover or balance-sheet
    share facts, or a newest count older than the loader's own
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
        hit = _newest_known(cache, ticker, as_of)
        if hit is not None:
            filed, count = hit
            if (as_of - filed).days <= MAX_FACT_AGE_DAYS:
                shares = count
            else:
                logger.debug(
                    f"{ticker} @ {as_of}: newest share count filed {filed} "
                    f"is older than {MAX_FACT_AGE_DAYS} days; "
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
