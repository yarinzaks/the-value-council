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

from datetime import date

import pandas as pd

from core.data.edgar_cache import EdgarCache
from core.logger import get_logger
from core.research.splits import adjusted_shares

logger = get_logger("agents.market_core.shares")

#: XBRL concepts carrying a share count, best first. The ``dei``
#: cover-page fact is preferred because it is dated at the filing rather
#: than at a period end — a 10-K restates the same concept for every
#: year in its comparative columns, so taking the largest reaches years
#: into the past and reads a count that was stale when filed.
SHARE_CONCEPTS: tuple[str, ...] = (
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding",
)


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

    rows = facts[facts["concept"].isin(SHARE_CONCEPTS)]
    if len(rows) == 0:
        return pd.Series(dtype="float64")

    preference = {name: i for i, name in enumerate(SHARE_CONCEPTS)}
    ordered = rows.assign(_rank=rows["concept"].map(preference)).sort_values(
        ["filed", "_rank", "period_end"], ascending=[True, False, True]
    )
    return adjusted_shares(ordered.groupby("filed")["value"].last())


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
        series = split_adjusted_shares(cache, ticker)
        if series.empty:
            continue
        known = series[series.index <= as_of]
        if known.empty:
            continue
        value = float(known.iloc[-1])
        if value > 0:
            out[ticker] = value
    return out


__all__ = ["SHARE_CONCEPTS", "shares_known_at", "split_adjusted_shares"]
