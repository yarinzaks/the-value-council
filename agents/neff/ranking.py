"""Neff 7-criteria scoring + Total-Return/PE ranking.

Population-aware: takes the post-quality-gate batch and computes
market-median P/E and yield, then evaluates each candidate against
the 7 criteria. Survivors are sorted by Neff's signature metric —
``(eps_growth + dividend_yield) / pe`` — descending.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

from .filters import (
    DEFAULT_MAX_GROWTH_PCT,
    DEFAULT_MIN_GROWTH_PCT,
    DEFAULT_MIN_ROE_PCT,
    DEFAULT_PE_MAX_FRAC_OF_MARKET,
    DEFAULT_PE_MIN_FRAC_OF_MARKET,
    DEFAULT_SALES_GROWTH_FLOOR_FRAC,
    DEFAULT_TR_PE_MARKET_MULTIPLE,
    DEFAULT_YIELD_PCT_OVER_MARKET,
    debt_to_equity,
    dividend_yield,
    median,
    pe_ratio,
    roe,
    total_return_to_pe,
    trailing_growth_pct,
)

logger = get_logger("agents.neff.ranking")


@dataclass(frozen=True)
class NeffScore:
    """A candidate that passed the 7-criterion screen."""

    ticker: str
    price: float
    market_cap: float
    pe: float
    eps_growth_pct: float
    sales_growth_pct: float
    dividend_yield_pct: float
    roe_pct: float
    total_return_pe: float  # signature metric
    debt_to_equity: float
    net_income: float
    # Per-criterion booleans for audit / decision logging
    pass_pe_window: bool
    pass_growth_window: bool
    pass_yield_premium: bool
    pass_tr_pe_multiple: bool
    pass_sales_drives_eps: bool
    pass_roe: bool


def _compute_market_avgs(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
) -> tuple[float | None, float | None]:
    """Return (median P/E, median dividend-yield-pct) across candidates.

    Medians are robust to outliers — Neff cared about how a stock
    compared to "the market", and using the median of the screening
    universe is the most honest available proxy.
    """
    pes: list[float] = []
    ylds: list[float] = []
    for fin, mcap, price in candidates:
        pe = pe_ratio(price, fin)
        if pe is not None and 0 < pe < 100:
            pes.append(pe)
        y = dividend_yield(mcap, fin)
        if y is not None:
            ylds.append(y * 100.0)  # to percentage points
    return median(pes), median(ylds)


def score_candidates(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
    *,
    as_of: date,
    edgar_cache: EdgarCache,
    min_growth_pct: float = DEFAULT_MIN_GROWTH_PCT,
    max_growth_pct: float = DEFAULT_MAX_GROWTH_PCT,
    min_roe_pct: float = DEFAULT_MIN_ROE_PCT,
    pe_min_frac: float = DEFAULT_PE_MIN_FRAC_OF_MARKET,
    pe_max_frac: float = DEFAULT_PE_MAX_FRAC_OF_MARKET,
    yield_pp_over_market: float = DEFAULT_YIELD_PCT_OVER_MARKET,
    tr_pe_market_multiple: float = DEFAULT_TR_PE_MARKET_MULTIPLE,
    sales_growth_floor_frac: float = DEFAULT_SALES_GROWTH_FLOOR_FRAC,
) -> list[NeffScore]:
    """Apply Neff's 7-criterion screen + Total-Return/PE ranking.

    Returns survivors sorted by ``total_return_pe`` descending.
    """
    if not candidates:
        return []

    market_pe, market_yield_pct = _compute_market_avgs(candidates)
    if market_pe is None or market_yield_pct is None:
        logger.warning(f"{as_of}: cannot compute market medians; skipping Neff scan")
        return []

    pe_low = market_pe * pe_min_frac
    pe_high = market_pe * pe_max_frac
    yield_floor_pct = market_yield_pct + yield_pp_over_market
    tr_pe_market = (market_yield_pct + 0.0) / market_pe  # placeholder
    # The market's TR/PE benchmark is a market-wide signal; honestly
    # we don't know its growth component without aggregating analyst
    # estimates. The cleanest, conservative proxy is to compute
    # market-wide trailing EPS growth as the median of survivors'
    # 4y CAGR — see two-pass logic below.

    # First pass: compute every candidate's growth & yield so we can
    # build a true market-median TR/PE benchmark.
    metrics: list[tuple[
        PointInTimeFinancials, float, float, float, float | None, float | None, float | None, float | None
    ]] = []
    for fin, mcap, price in candidates:
        pe = pe_ratio(price, fin)
        yld = dividend_yield(mcap, fin)
        eps_g = trailing_growth_pct(edgar_cache, fin.ticker, as_of, metric="eps")
        rev_g = trailing_growth_pct(
            edgar_cache, fin.ticker, as_of, metric="revenue"
        )
        r = roe(fin)
        metrics.append((fin, mcap, price, pe or float("nan"), yld, eps_g, rev_g, r))

    # Build market median TR/PE from survivors with all three inputs.
    tr_pe_market_values: list[float] = []
    for _, _, _, pe_, yld, eps_g, _, _ in metrics:
        if pe_ != pe_ and (pe_ is None):
            continue
        if yld is None or eps_g is None or pe_ is None or pe_ <= 0:
            continue
        tr_pe_market_values.append((eps_g + yld * 100.0) / pe_)
    market_tr_pe = median(tr_pe_market_values)
    if market_tr_pe is None or market_tr_pe <= 0:
        # Fallback if not enough data: use Neff's rule-of-thumb
        # historical benchmark of S&P 500 TR/PE ≈ 0.7.
        market_tr_pe = 0.7
    tr_pe_floor = market_tr_pe * tr_pe_market_multiple

    out: list[NeffScore] = []
    for fin, mcap, price, pe, yld, eps_g, rev_g, r in metrics:
        # Discard if any required metric is missing.
        if pe is None or pe != pe or pe <= 0 or yld is None or eps_g is None or r is None:
            continue
        de = debt_to_equity(fin) or 0.0
        ni = fin.net_income or 0.0
        yld_pct = yld * 100.0
        roe_pct = r * 100.0

        pass_pe_window = pe_low <= pe <= pe_high
        pass_growth_window = min_growth_pct <= eps_g <= max_growth_pct
        pass_yield_premium = yld_pct >= yield_floor_pct
        tr_pe = total_return_to_pe(eps_g, yld_pct, pe)
        if tr_pe is None:
            continue
        pass_tr_pe_multiple = tr_pe >= tr_pe_floor
        # Sales-growth-drives-EPS: only enforce if we have BOTH growth
        # numbers; if revenue history is missing we default to
        # "passes" rather than rejecting on missing data.
        if rev_g is None or eps_g <= 0:
            pass_sales_drives_eps = True
        else:
            pass_sales_drives_eps = rev_g >= sales_growth_floor_frac * eps_g
        pass_roe = roe_pct >= min_roe_pct

        all_pass = (
            pass_pe_window
            and pass_growth_window
            and pass_yield_premium
            and pass_tr_pe_multiple
            and pass_sales_drives_eps
            and pass_roe
        )
        if not all_pass:
            continue

        out.append(
            NeffScore(
                ticker=fin.ticker,
                price=price,
                market_cap=mcap,
                pe=pe,
                eps_growth_pct=eps_g,
                sales_growth_pct=rev_g if rev_g is not None else 0.0,
                dividend_yield_pct=yld_pct,
                roe_pct=roe_pct,
                total_return_pe=tr_pe,
                debt_to_equity=de,
                net_income=ni,
                pass_pe_window=pass_pe_window,
                pass_growth_window=pass_growth_window,
                pass_yield_premium=pass_yield_premium,
                pass_tr_pe_multiple=pass_tr_pe_multiple,
                pass_sales_drives_eps=pass_sales_drives_eps,
                pass_roe=pass_roe,
            )
        )

    # Rank by signature metric (TR/PE) descending.
    out.sort(key=lambda s: -s.total_return_pe)
    return out


def select_top_n(scores: list[NeffScore], n: int) -> list[NeffScore]:
    """Take top ``n`` by Total-Return/PE. Take all if fewer."""
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


__all__ = ["NeffScore", "score_candidates", "select_top_n"]
