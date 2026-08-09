"""Price-derived features, measured once for every (date, ticker).

Why these features
~~~~~~~~~~~~~~~~~~

Gu, Kelly & Xiu (2020) threw ~900 candidate predictors at trees and
neural networks and found the same three families dominating every
model: **momentum, liquidity and volatility**. All three come from the
price series alone, which is the part of this project's data with no
gaps — ``prices.sqlite`` holds 12.4M bars from 2010-01-04 with no null
adjusted closes. So this module is where the strongest evidence and the
cleanest data happen to coincide.

Look-ahead discipline
~~~~~~~~~~~~~~~~~~~~~

Every feature at date ``t`` is computed from bars up to and including
``t``, and every forward return starts at ``t + 1``. The two never
overlap. ``pandas`` rolling windows are right-closed, so a window
ending at ``t`` is exactly "the last N bars an investor could have
seen at the close of ``t``".

Momentum skips the most recent 21 bars (the standard 12-1
construction) for a reason that matters here: without the skip, a
signal measured through ``t`` and a fill struck at ``t``'s close use
the same bar, and the backtest quietly earns the one-month reversal.

What this cannot fix
~~~~~~~~~~~~~~~~~~~~

Survivorship. The ticker roster comes from currently-registered
issuers, so companies that failed are absent at every historical date.
It was worth checking whether the price database could patch the hole —
1,699 tickers have series ending before 2026-06 — but 961 of them stop
on exactly 2024-12-31 and 490 on exactly 2026-04-29. Those are fetch
dates, not delistings: ``DPZ`` and ``BPOP`` are in the group and both
still trade. The database records when a ticker was last downloaded,
not when it died, so it cannot identify a single delisting.

This bites momentum hardest. A company on its way to failing has
catastrophic trailing returns, and momentum's edge is partly the
avoidance of exactly those names — which are the ones missing. Read
every cross-sectional number here as an upper bound, and read the
momentum leg as the loosest bound of the three.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from core.data.ticker_filter import is_common_equity, is_primary_listing
from core.logger import get_logger
from core.paths import DATA_ROOT

logger = get_logger("core.research.price_panel")

#: The market proxy every beta and residual is measured against. It is
#: the same benchmark ``BacktestRunner`` scores against, so a strategy's
#: measured beta and its reported alpha refer to the same index.
MARKET_TICKER = "SPY"

#: Trading days per window. 21 ≈ a month, 252 ≈ a year.
DAYS_PER_MONTH = 21
DAYS_PER_YEAR = 252

#: Momentum: twelve months of return, skipping the most recent one.
#: Jegadeesh & Titman (1993); the skip avoids the one-month reversal.
MOMENTUM_LOOKBACK = 12 * DAYS_PER_MONTH
MOMENTUM_SKIP = DAYS_PER_MONTH

#: Volatility, beta and residual volatility all share a six-month
#: window — long enough to estimate a second moment, short enough to
#: still describe the stock's current regime.
VOL_WINDOW = 6 * DAYS_PER_MONTH

#: Liquidity: median daily dollar volume over a quarter. Median rather
#: than mean because one earnings-day spike should not make an
#: otherwise untradeable name look tradeable.
LIQUIDITY_WINDOW = 3 * DAYS_PER_MONTH

#: A bar is usable only if the adjusted close is strictly positive.
#: 5,890 of 12.4M rows fail this; they are holes, not prices.
MIN_VALID_PRICE = 0.0

#: Median daily dollar volume a name must clear to enter the panel.
#:
#: This is the *only* investability screen, and the choice is forced
#: rather than stylistic. Every absurd row in the first build — a
#: momentum of +517,600%, an annualised volatility of 1,415,100%, a
#: share price of $3.9e16 — belonged to a shell with **zero** traded
#: dollars: BINI, FIISO, TWOH, CDIX. Nothing was wrong with the
#: arithmetic; those are what a price series looks like after a company
#: goes to nothing and reverse-splits its way back up. A strategy
#: allowed to hold them would "discover" enormous returns in names that
#: could not be bought at any size.
#:
#: A price floor is the other half of the usual screen, and it is
#: deliberately absent. ``close`` and ``adj_close`` are identical in
#: this database — both hold the split-adjusted series — so a
#: historical "price" is stated in today's share terms. GEVO shows
#: $1,056 on 2015-07-31; it actually traded near $3.50 and the figure
#: is that price carried through the 2017 and 2019 reverse splits.
#: Screening on it would drop GEVO from the 2015 universe **because of
#: a split that had not happened yet**, which is look-ahead bias
#: wearing the costume of a hygiene filter.
#:
#: Dollar volume has no such problem: back-adjustment multiplies price
#: and divides volume by the same factor, so the product is invariant.
#: GEVO's 2,570 adjusted shares at $1,056 is $2.7M — the real July 2015
#: figure.
#:
#: $1M/day admits ~1,360 names per date. Higher floors were tried
#: ($5M → 1,010 names, $10M → 808) and the extreme values are already
#: gone at $1M; going higher trades universe breadth for nothing.
MIN_DOLLAR_VOLUME = 1_000_000.0


@dataclass(frozen=True)
class PanelSpec:
    """What to measure, and over what stretch of history."""

    start: date
    end: date
    #: Minimum bars of history before a ticker may enter the panel.
    #: One year plus the momentum skip, so every feature below has a
    #: full window rather than a partially-filled one.
    min_history: int = MOMENTUM_LOOKBACK + MOMENTUM_SKIP

    #: How often the strategy is asked to decide: ``"M"`` for month-end,
    #: ``"Q"`` for quarter-end.
    #:
    #: This has to match how the agent will actually be deployed, not
    #: what is convenient to research with. The leaderboard runs every
    #: doctrine quarterly (see
    #: :data:`core.backtest.validation_window.VALIDATION_REBALANCE`), and
    #: a design tuned monthly is a different strategy: it trades three
    #: times as often, pays three times the cost, and acts on signals
    #: while they are two months fresher. Designing at one frequency and
    #: deploying at another is how a research result stops reproducing.
    #:
    #: ``fwd_next`` is always measured rebalance-to-rebalance, so it
    #: follows this setting rather than assuming a month.
    frequency: Literal["M", "Q"] = "Q"

    #: Restrict the roster to ordinary common stock, one listing per
    #: issuer. On by default, and it is not housekeeping.
    #:
    #: Without it a low-volatility screen returns MER-PK, BAC-PL, C-PN
    #: and AXS-PE — preferred series, which are bonds wearing equity
    #: tickers. They barely move, so they sweep the top of any
    #: volatility ranking, and a book of them posted a 1.51 Sharpe with
    #: a 6.8% maximum drawdown across 2013-2018. That is not a discovery
    #: about equities; it is an interest-rate portfolio, and its upside
    #: is capped at the call price no matter how well the issuer does.
    common_equity_only: bool = True

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(f"start {self.start} is not before end {self.end}")


def _prices_db() -> Path:
    return DATA_ROOT / "cache" / "prices.sqlite"


def load_price_matrices(
    spec: PanelSpec,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(adjusted_close, dollar_volume)`` as date-by-ticker frames.

    Loading the whole table and pivoting costs about a gigabyte and
    twenty seconds, and buys vectorised feature maths across all 5,651
    tickers at once. The per-ticker alternative is a 5,651-iteration
    Python loop, which is how the production backtest ends up taking an
    hour per strategy.

    Bars are read from a buffer before ``spec.start`` so that a feature
    at the first rebalance date has its full trailing window.
    """
    # Read enough history before the first rebalance to fill the
    # longest window, plus slack for holidays.
    buffer_days = int((spec.min_history + VOL_WINDOW) * 1.6)
    read_from = (pd.Timestamp(spec.start) - pd.Timedelta(days=buffer_days)).date()

    logger.info(f"loading bars {read_from} → {spec.end} from prices.sqlite")
    conn = sqlite3.connect(_prices_db())
    try:
        raw = pd.read_sql_query(
            """
            SELECT ticker, trade_date, adj_close, close, volume
            FROM prices
            WHERE trade_date >= ? AND trade_date <= ? AND adj_close > ?
            """,
            conn,
            params=(read_from.isoformat(), spec.end.isoformat(), MIN_VALID_PRICE),
            parse_dates=["trade_date"],
        )
    finally:
        conn.close()

    logger.info(f"{len(raw):,} bars, {raw['ticker'].nunique():,} tickers")

    raw["dollar_volume"] = raw["close"].astype("float64") * raw["volume"].astype(
        "float64"
    )

    adj = raw.pivot_table(
        index="trade_date", columns="ticker", values="adj_close", aggfunc="last"
    ).astype("float32")
    dv = raw.pivot_table(
        index="trade_date", columns="ticker", values="dollar_volume", aggfunc="last"
    ).astype("float32")

    adj = adj.sort_index()

    if spec.common_equity_only:
        before = adj.shape[1]
        keep = [
            t
            for t in adj.columns
            if t == MARKET_TICKER or (is_common_equity(t) and is_primary_listing(t))
        ]
        adj = adj[keep]
        logger.info(
            f"common-equity filter: {before:,} → {len(keep):,} tickers "
            f"({before - len(keep):,} preferreds, warrants, ADRs and "
            f"secondary listings removed)"
        )

    dv = dv.reindex(index=adj.index, columns=adj.columns)
    logger.info(f"price matrix: {adj.shape[0]:,} dates x {adj.shape[1]:,} tickers")
    return adj, dv


def rebalance_dates(adj: pd.DataFrame, spec: PanelSpec) -> pd.DatetimeIndex:
    """Last trading day of each period inside the spec's window.

    Derived from the price index rather than a calendar, so every date
    is one the market actually traded and no feature is measured on a
    day with no close.
    """
    idx = adj.index
    in_window = idx[
        (idx >= pd.Timestamp(spec.start)) & (idx <= pd.Timestamp(spec.end))
    ]
    period_ends = (
        pd.Series(in_window, index=in_window)
        .groupby(in_window.to_period(spec.frequency))
        .max()
    )
    return pd.DatetimeIndex(period_ends.values)


def benchmark_returns(adj: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.Series:
    """Market return from each rebalance date to the next.

    Measured on the same grid and from the same matrix as every
    strategy, so an alpha is a difference between two things computed
    the same way rather than against a number from somewhere else.
    """
    series = adj[MARKET_TICKER].loc[dates]
    return (series.shift(-1) / series - 1.0).rename("benchmark")


def trend_exposure(
    adj: pd.DataFrame,
    dates: pd.DatetimeIndex,
    *,
    window: int = 10 * DAYS_PER_MONTH,
    defensive_exposure: float = 0.0,
) -> pd.Series:
    """Full exposure while the market is above its own trend, less below.

    A ten-month moving average on the index, evaluated at each
    rebalance: the oldest and most-replicated timing rule there is.
    Time-series momentum has produced positive average returns in every
    decade since 1880 and did its job in eight of the ten largest
    drawdowns of the past century, which is precisely when a long-only
    book needs it.

    It also happens to be the one component of a strategy here that
    survivorship bias cannot inflate. The cross-sectional signals are
    measured on a roster of companies that still exist; ``SPY``'s own
    history has no such hole, so what this rule earns is what it earned.

    The signal uses closes up to and including the rebalance date, and
    is applied to the period that follows it.
    """
    market = adj[MARKET_TICKER]
    trend = market.rolling(window, min_periods=window // 2).mean()
    above = (market > trend).loc[dates]
    return above.map({True: 1.0, False: defensive_exposure}).astype("float64")


def _residual_volatility(
    returns: pd.DataFrame, market: pd.Series, window: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rolling beta and idiosyncratic volatility against the market.

    Idiosyncratic rather than total volatility, and idiosyncratic
    rather than beta, because that is what the evidence singles out:
    across the low-risk family, volatility-sorted portfolios carry the
    effect and beta-sorted ones are the weakest version of it.

    Both come out of one pass. For each column,
    ``beta = cov(r, m) / var(m)`` over the window, and the residual
    ``r - alpha - beta*m`` has a standard deviation that is the part of
    the stock's movement the market does not explain.
    """
    m = market.reindex(returns.index)
    m_mean = m.rolling(window, min_periods=window // 2).mean()
    m_var = m.rolling(window, min_periods=window // 2).var()

    r_mean = returns.rolling(window, min_periods=window // 2).mean()
    # E[r*m] - E[r]E[m] is the rolling covariance of each column with m.
    rm_mean = returns.mul(m, axis=0).rolling(window, min_periods=window // 2).mean()
    cov = rm_mean.sub(r_mean.mul(m_mean, axis=0))

    beta = cov.div(m_var, axis=0)
    alpha = r_mean.sub(beta.mul(m_mean, axis=0))

    # Residual standard deviation, annualised. Building the full
    # residual matrix is the memory-heavy step, so it is done in place.
    fitted = beta.mul(m, axis=0).add(alpha)
    resid = returns.sub(fitted)
    ivol = resid.rolling(window, min_periods=window // 2).std() * np.sqrt(DAYS_PER_YEAR)
    return beta, ivol


def build_price_panel(spec: PanelSpec) -> pd.DataFrame:
    """One row per ``(rebalance_date, ticker)`` with every price feature.

    Columns:

    ``mom_12_1``      twelve-month return skipping the last month
    ``mom_6_1``       six-month return skipping the last month
    ``reversal_1m``   the most recent month's return
    ``vol_6m``        annualised total volatility
    ``ivol_6m``       annualised volatility of the market residual
    ``beta_6m``       rolling market beta
    ``dollar_volume`` median daily dollar volume over a quarter
    ``price``         split-adjusted close, stated in today's share
                      terms — usable for market cap against an equally
                      adjusted share count, but never as a price screen
                      (see :data:`MIN_DOLLAR_VOLUME`)
    ``fwd_next``      return from this rebalance to the next one
    ``fwd_1m``        return over the next 21 trading days
    ``fwd_3m``        return over the next 63 trading days

    The three ``fwd_`` columns are the research target and must never be
    fed to a signal — they exist so a variant can be scored without
    running a full backtest. ``fwd_next`` is the one to compound;
    ``fwd_1m`` and ``fwd_3m`` are fixed horizons for measuring how fast
    a signal decays.
    """
    adj, dv = load_price_matrices(spec)
    if MARKET_TICKER not in adj.columns:
        raise ValueError(f"{MARKET_TICKER} missing from prices.sqlite")

    dates = rebalance_dates(adj, spec)
    logger.info(f"{len(dates)} rebalance dates: {dates[0].date()} → {dates[-1].date()}")

    daily_ret = adj.pct_change(fill_method=None)
    market_ret = daily_ret[MARKET_TICKER]

    # --- trailing features -------------------------------------------
    mom_12_1 = adj.shift(MOMENTUM_SKIP) / adj.shift(MOMENTUM_SKIP + MOMENTUM_LOOKBACK) - 1.0
    mom_6_1 = adj.shift(MOMENTUM_SKIP) / adj.shift(MOMENTUM_SKIP + 6 * DAYS_PER_MONTH) - 1.0
    reversal = adj / adj.shift(DAYS_PER_MONTH) - 1.0
    vol = daily_ret.rolling(VOL_WINDOW, min_periods=VOL_WINDOW // 2).std() * np.sqrt(
        DAYS_PER_YEAR
    )
    beta, ivol = _residual_volatility(daily_ret, market_ret, VOL_WINDOW)
    liquidity = dv.rolling(LIQUIDITY_WINDOW, min_periods=LIQUIDITY_WINDOW // 2).median()
    history = adj.notna().rolling(spec.min_history, min_periods=1).sum()

    # --- forward returns, strictly after the rebalance date ----------
    # ``fwd_next`` runs from this rebalance to the next one, so chaining
    # it reproduces a monthly-rebalanced book exactly: no gap between
    # consecutive holdings and no overlap double-counting a day. A fixed
    # 21-bar horizon does neither, because months are 19 to 23 sessions
    # long and the error accumulates in one direction.
    at_rebalance = adj.loc[dates]
    fwd_next = at_rebalance.shift(-1) / at_rebalance - 1.0
    fwd_1m = adj.shift(-DAYS_PER_MONTH) / adj - 1.0
    fwd_3m = adj.shift(-3 * DAYS_PER_MONTH) / adj - 1.0

    frames = {
        "mom_12_1": mom_12_1,
        "mom_6_1": mom_6_1,
        "reversal_1m": reversal,
        "vol_6m": vol,
        "ivol_6m": ivol,
        "beta_6m": beta,
        "dollar_volume": liquidity,
        "price": adj,
        "bars_of_history": history,
        "fwd_next": fwd_next,
        "fwd_1m": fwd_1m,
        "fwd_3m": fwd_3m,
    }

    logger.info("stacking features into a panel")
    # Build the target index explicitly and reindex every feature onto
    # it. ``DataFrame.stack`` has changed its NA semantics across pandas
    # versions; aligning to a known index makes the result identical
    # under either implementation, and guarantees that a value missing
    # for one feature cannot shift another feature's rows.
    tickers = adj.columns
    full_index = pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"])
    stacked = {
        name: frame.loc[dates]
        .reindex(columns=tickers)
        .to_numpy(dtype="float32")
        .reshape(-1)
        for name, frame in frames.items()
    }
    panel = pd.DataFrame(stacked, index=full_index)

    # The market proxy is a benchmark, not a candidate holding.
    panel = panel.drop(index=MARKET_TICKER, level="ticker", errors="ignore")

    before = len(panel)
    panel = panel[
        panel["price"].notna()
        & (panel["bars_of_history"] >= spec.min_history)
        & (panel["dollar_volume"] >= MIN_DOLLAR_VOLUME)
        & panel["mom_12_1"].notna()
        & panel["ivol_6m"].notna()
    ]
    n_dates = panel.index.get_level_values("date").nunique()
    logger.info(
        f"panel: {len(panel):,} rows kept of {before:,} "
        f"({panel.index.get_level_values('ticker').nunique():,} tickers, "
        f"{len(panel) / max(n_dates, 1):.0f} names per date)"
    )
    return panel


__all__ = [
    "DAYS_PER_MONTH",
    "DAYS_PER_YEAR",
    "MARKET_TICKER",
    "PanelSpec",
    "benchmark_returns",
    "build_price_panel",
    "load_price_matrices",
    "rebalance_dates",
    "trend_exposure",
]
