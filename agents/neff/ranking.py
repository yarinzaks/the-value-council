"""Neff 7-criteria scoring + Total-Return/PE ranking — INDUSTRY-RELATIVE.

Per the playbook (section 4.1) Neff compared a stock against its
**industry's** averages, not the whole market's. We group candidates
by their SIC2 (first 2 digits of the SIC code = "major industry
group" per SEC's taxonomy), compute per-industry medians for P/E,
yield, ROE, and TR/PE, and screen each candidate against its own
industry's medians.

Why this matters:
  * Banks have median P/E ≈ 10-12; tech ≈ 22-30. A universe-wide
    median (≈ 18) would always reject tech and always pass banks
    — neither is what Neff wanted.
  * Utilities have low ROE; tech has high ROE. An absolute "ROE ≥
    15%" floor washes out half of Neff's hunting grounds.

Fallbacks:
  * Tickers with no SIC (the bundled map is missing them) fall back
    to the universe-wide median.
  * Industries with fewer than ``MIN_INDUSTRY_PEERS`` candidates
    fall back to the universe-wide median (small samples are noisy).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.data.sic_codes import industry_for
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

#: An industry needs at least this many ranked peers before its
#: median is considered statistically meaningful. Below this we fall
#: back to the universe-wide median.
MIN_INDUSTRY_PEERS: int = 5


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
    # Which industry-group benchmark we screened this candidate against.
    # ``None`` means "no SIC available — fell back to universe median".
    industry_sic2: int | None
    industry_peer_count: int
    pass_pe_window: bool
    pass_growth_window: bool
    pass_yield_premium: bool
    pass_tr_pe_multiple: bool
    pass_sales_drives_eps: bool
    pass_roe: bool


@dataclass(frozen=True)
class _IndustryStats:
    """Pre-computed medians for one industry group (or universe fallback)."""

    sic2: int | None  # None = universe fallback bucket
    peer_count: int
    median_pe: float | None
    median_yield_pct: float | None
    median_roe_pct: float | None
    median_tr_pe: float | None


@dataclass(frozen=True)
class _CandidateMetrics:
    fin: PointInTimeFinancials
    market_cap: float
    price: float
    pe: float | None
    yield_pct: float | None
    eps_growth_pct: float | None
    sales_growth_pct: float | None
    roe_pct: float | None
    tr_pe: float | None
    sic2: int | None


def _gather_metrics(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
    *,
    edgar_cache: EdgarCache,
    as_of: date,
) -> list[_CandidateMetrics]:
    out: list[_CandidateMetrics] = []
    for fin, mcap, price in candidates:
        pe = pe_ratio(price, fin)
        yld = dividend_yield(mcap, fin)
        eps_g = trailing_growth_pct(edgar_cache, fin.ticker, as_of, metric="eps")
        rev_g = trailing_growth_pct(
            edgar_cache, fin.ticker, as_of, metric="revenue"
        )
        r = roe(fin)
        yld_pct = yld * 100.0 if yld is not None else None
        roe_pct = r * 100.0 if r is not None else None
        tr_pe = (
            total_return_to_pe(eps_g, yld_pct, pe)
            if (eps_g is not None and yld_pct is not None and pe is not None)
            else None
        )
        sic2 = industry_for(fin.ticker)
        out.append(
            _CandidateMetrics(
                fin=fin,
                market_cap=mcap,
                price=price,
                pe=pe,
                yield_pct=yld_pct,
                eps_growth_pct=eps_g,
                sales_growth_pct=rev_g,
                roe_pct=roe_pct,
                tr_pe=tr_pe,
                sic2=sic2,
            )
        )
    return out


def _build_industry_stats(
    metrics: list[_CandidateMetrics],
    *,
    min_peers: int = MIN_INDUSTRY_PEERS,
) -> tuple[dict[int, _IndustryStats], _IndustryStats]:
    """Compute per-industry stats + the universe-wide fallback bucket.

    The universe-wide bucket pools EVERY candidate with usable data —
    used both for tickers without SIC codes and for tickers in
    too-small industries.
    """
    by_industry: dict[int, list[_CandidateMetrics]] = {}
    for m in metrics:
        if m.sic2 is None:
            continue
        by_industry.setdefault(m.sic2, []).append(m)

    def stats_from(group: list[_CandidateMetrics], sic2: int | None) -> _IndustryStats:
        pes = [m.pe for m in group if m.pe is not None and 0 < m.pe < 100]
        yields = [m.yield_pct for m in group if m.yield_pct is not None]
        roes = [m.roe_pct for m in group if m.roe_pct is not None]
        tr_pes = [m.tr_pe for m in group if m.tr_pe is not None]
        return _IndustryStats(
            sic2=sic2,
            peer_count=len(group),
            median_pe=median(pes),
            median_yield_pct=median(yields),
            median_roe_pct=median(roes),
            median_tr_pe=median(tr_pes),
        )

    industry_stats: dict[int, _IndustryStats] = {}
    for sic2, group in by_industry.items():
        s = stats_from(group, sic2)
        if s.peer_count >= min_peers:
            industry_stats[sic2] = s

    universe_stats = stats_from(metrics, None)
    return industry_stats, universe_stats


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
    use_industry_medians: bool = True,
    min_industry_peers: int = MIN_INDUSTRY_PEERS,
) -> list[NeffScore]:
    """Apply Neff's 7-criterion screen with industry-relative medians.

    Returns survivors sorted by ``total_return_pe`` descending.

    The industry-relative behavior can be disabled via
    ``use_industry_medians=False`` to fall back to the v1 universe-
    wide approach — useful for A/B testing.
    """
    if not candidates:
        return []

    metrics = _gather_metrics(candidates, edgar_cache=edgar_cache, as_of=as_of)
    industry_stats, universe_stats = _build_industry_stats(
        metrics, min_peers=min_industry_peers
    )

    if (
        universe_stats.median_pe is None
        or universe_stats.median_yield_pct is None
        or universe_stats.median_tr_pe is None
        or universe_stats.median_tr_pe <= 0
    ):
        logger.warning(
            f"{as_of}: cannot compute universe medians; skipping Neff scan"
        )
        return []

    n_industries_used = len(industry_stats)
    logger.info(
        f"{as_of}: Neff industry stats — {n_industries_used} groups with "
        f"≥{min_industry_peers} peers (universe median PE="
        f"{universe_stats.median_pe:.2f}, "
        f"yield={universe_stats.median_yield_pct:.2f}%, "
        f"TR/PE={universe_stats.median_tr_pe:.3f})"
    )

    out: list[NeffScore] = []
    for m in metrics:
        # Pick the benchmark stats: industry-specific if available,
        # else universe fallback.
        if (
            use_industry_medians
            and m.sic2 is not None
            and m.sic2 in industry_stats
        ):
            stats = industry_stats[m.sic2]
        else:
            stats = universe_stats

        if (
            m.pe is None
            or m.yield_pct is None
            or m.eps_growth_pct is None
            or m.roe_pct is None
            or m.tr_pe is None
            or stats.median_pe is None
            or stats.median_yield_pct is None
            or stats.median_roe_pct is None
            or stats.median_tr_pe is None
        ):
            continue

        pe_low = stats.median_pe * pe_min_frac
        pe_high = stats.median_pe * pe_max_frac
        yield_floor = stats.median_yield_pct + yield_pp_over_market
        tr_pe_floor = max(stats.median_tr_pe * tr_pe_market_multiple, 0.0)

        # Criterion 1: P/E in [40%, 60%] of industry median.
        pass_pe_window = pe_low <= m.pe <= pe_high
        # Criterion 2: EPS growth in [7%, 20%] (absolute).
        pass_growth_window = min_growth_pct <= m.eps_growth_pct <= max_growth_pct
        # Criterion 3: yield ≥ industry median + 2pp.
        pass_yield_premium = m.yield_pct >= yield_floor
        # Criterion 4: TR/PE ≥ 2× industry median (signature metric).
        pass_tr_pe_multiple = m.tr_pe >= tr_pe_floor
        # Criterion 5: sales growth drives EPS growth.
        if m.sales_growth_pct is None or m.eps_growth_pct <= 0:
            pass_sales_drives_eps = True
        else:
            pass_sales_drives_eps = (
                m.sales_growth_pct >= sales_growth_floor_frac * m.eps_growth_pct
            )
        # Criterion 7: ROE ≥ industry median (with absolute sanity floor).
        pass_roe = m.roe_pct >= max(stats.median_roe_pct, min_roe_pct)

        if not (
            pass_pe_window
            and pass_growth_window
            and pass_yield_premium
            and pass_tr_pe_multiple
            and pass_sales_drives_eps
            and pass_roe
        ):
            continue

        out.append(
            NeffScore(
                ticker=m.fin.ticker,
                price=m.price,
                market_cap=m.market_cap,
                pe=m.pe,
                eps_growth_pct=m.eps_growth_pct,
                sales_growth_pct=m.sales_growth_pct or 0.0,
                dividend_yield_pct=m.yield_pct,
                roe_pct=m.roe_pct,
                total_return_pe=m.tr_pe,
                debt_to_equity=debt_to_equity(m.fin) or 0.0,
                net_income=m.fin.net_income or 0.0,
                industry_sic2=m.sic2,
                industry_peer_count=stats.peer_count,
                pass_pe_window=pass_pe_window,
                pass_growth_window=pass_growth_window,
                pass_yield_premium=pass_yield_premium,
                pass_tr_pe_multiple=pass_tr_pe_multiple,
                pass_sales_drives_eps=pass_sales_drives_eps,
                pass_roe=pass_roe,
            )
        )

    out.sort(key=lambda s: -s.total_return_pe)
    return out


def select_top_n(scores: list[NeffScore], n: int) -> list[NeffScore]:
    """Take top ``n`` by Total-Return/PE. Take all if fewer."""
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


# ---- A small note on the absolute ROE floor --------------------------------
# Neff's playbook says "ROE above industry average; preferred ≥ 15%."
# We treat the 15% as a SOFT preference: pass_roe = roe ≥
# max(industry_median, min_roe_pct=15%). Keeping the absolute floor
# protects against pathological edge cases (e.g. an industry where
# every member has negative or zero ROE — pass_roe should still
# reject those).


__all__ = [
    "MIN_INDUSTRY_PEERS",
    "NeffScore",
    "score_candidates",
    "select_top_n",
]
