"""Joining filings to prices, and the ratios that come out of the pair.

Two panels arrive from different places and on different grids: prices
are monthly and complete, filings are quarterly and arrive whenever a
company files. Joining them is where a backtest usually acquires
look-ahead bias, so the join here is explicitly backwards-only — each
rebalance takes the newest filing dated on or before it, and never the
one that lands next week.

Market capitalisation
~~~~~~~~~~~~~~~~~~~~~

Price times shares, with the share count restated through
:mod:`core.research.splits` first. Skipping that step gives a figure
wrong by the cumulative split factor, and wrong in a direction that
tracks what happened to the company afterwards — NVDA's end-2020
capitalisation comes out at $8bn against a true $323bn because of two
splits it had not done yet. Every ratio below that divides by market
cap or enterprise value inherits that error, so it is fixed once, here.
"""

from __future__ import annotations

import pandas as pd

from core.logger import get_logger

logger = get_logger("core.research.factors")

#: Enterprise value is bounded below at this fraction of market cap.
#:
#: A company holding more cash than its whole market value gives a
#: negative or near-zero enterprise value, and EBIT divided by that is
#: either a nonsense sign or an enormous number that sweeps the top of
#: any value ranking. Both are artefacts of the arithmetic rather than
#: statements about cheapness, so the ratio is refused instead of
#: reported.
MIN_EV_TO_MARKET_CAP = 0.05


def _latest_on_or_before(
    fundamentals: pd.DataFrame, dates: pd.DatetimeIndex
) -> pd.DataFrame:
    """Reindex quarterly filings onto ``dates``, carrying the last one.

    Backwards only. ``merge_asof`` with the default direction takes the
    most recent row at or before each key, which is exactly the filing
    an investor could have read; the forward variant would hand a
    January rebalance the numbers published in April.
    """
    # An empty frame has no index levels to group on, and a panel build
    # that resolved nothing is a plausible state — it is what a missing
    # or half-written parquet looks like. Crashing here would take down
    # the price-only designs too, which need none of this.
    if fundamentals.empty or "ticker" not in (fundamentals.index.names or []):
        return pd.DataFrame()

    # The two panels label dates differently — the fundamentals sweep
    # writes `datetime.date` because that is what the point-in-time
    # loader takes, while the price panel carries `pd.Timestamp` from
    # the bar index. Unioning the two produces a mixed-type index that
    # pandas will not reindex against, so they are made the same type
    # once, here, rather than at each of the several places they meet.
    fundamentals = fundamentals.copy()
    fundamentals.index = pd.MultiIndex.from_arrays(
        [
            pd.to_datetime(fundamentals.index.get_level_values("date")),
            fundamentals.index.get_level_values("ticker"),
        ],
        names=["date", "ticker"],
    )

    out: list[pd.DataFrame] = []
    for ticker, group in fundamentals.groupby(level="ticker"):
        flat = group.droplevel("ticker").sort_index()
        # A ticker filing twice against the same sample date would make
        # the index non-unique and stop the reindex below; keep the
        # later row, which is the fresher filing.
        flat = flat[~flat.index.duplicated(keep="last")]
        # A filing is known from its filing date, not its period end.
        # The panel is already indexed on the quarter it was sampled at,
        # which was itself resolved point-in-time, so the index is safe
        # to align on directly.
        aligned = flat.reindex(flat.index.union(dates)).ffill().reindex(dates)
        aligned["ticker"] = ticker
        out.append(aligned.set_index("ticker", append=True))
    if not out:
        return pd.DataFrame()
    joined = pd.concat(out)
    joined.index.names = ["date", "ticker"]
    return joined.sort_index()


def market_capitalisation(panel: pd.DataFrame) -> pd.Series:
    """Split-consistent market capitalisation, in dollars."""
    shares = panel["shares_split_adjusted"]
    cap = panel["price"] * shares
    return cap.where(cap > 0)


def enterprise_value(panel: pd.DataFrame) -> pd.Series:
    """Market cap plus debt less cash, floored away from zero."""
    cap = market_capitalisation(panel)
    debt = panel["total_debt"].fillna(0.0)
    cash = panel["cash_and_equivalents"].fillna(0.0)
    ev = cap + debt - cash
    return ev.where(ev >= cap * MIN_EV_TO_MARKET_CAP)


def add_fundamental_factors(
    prices: pd.DataFrame, fundamentals: pd.DataFrame
) -> pd.DataFrame:
    """Price panel with filing-derived columns joined on and ratios built.

    Adds ``earnings_yield`` (EBIT over enterprise value, in percent),
    ``op_profitability`` (operating income over assets, in percent) and
    ``asset_growth`` (year-on-year change in total assets, in percent).

    Rows whose filing data is missing keep the price features and carry
    ``NaN`` in the new columns, which drops them from any design that
    uses a fundamental leg and leaves the price-only designs intact.
    """
    dates = pd.DatetimeIndex(prices.index.get_level_values("date").unique())
    aligned = _latest_on_or_before(fundamentals, dates)
    if aligned.empty:
        logger.warning("no fundamentals aligned; returning price panel unchanged")
        return prices.copy()

    merged = prices.join(aligned, how="left", rsuffix="_fund")

    cap = market_capitalisation(merged)
    ev = enterprise_value(merged)

    merged["market_cap"] = cap
    merged["enterprise_value"] = ev
    merged["earnings_yield"] = (merged["operating_income"] / ev) * 100.0
    merged["op_profitability"] = (
        merged["operating_income"] / merged["total_assets"].where(lambda s: s > 0)
    ) * 100.0

    prior = merged.groupby(level="ticker")["total_assets"].shift(12)
    merged["asset_growth"] = (
        merged["total_assets"] / prior.where(lambda s: s > 0) - 1.0
    ) * 100.0

    # An earnings yield outside this range is a parse artefact rather
    # than a cheap company: EBIT equal to the whole enterprise value
    # means one of the two numbers is wrong.
    merged["earnings_yield"] = merged["earnings_yield"].where(
        merged["earnings_yield"].abs() < 100.0
    )

    resolved = int(merged["earnings_yield"].notna().sum())
    logger.info(
        f"fundamentals joined: {resolved:,} of {len(merged):,} rows carry an "
        f"earnings yield ({resolved / max(len(merged), 1) * 100:.1f}%)"
    )
    return merged


__all__ = [
    "MIN_EV_TO_MARKET_CAP",
    "add_fundamental_factors",
    "enterprise_value",
    "market_capitalisation",
]
