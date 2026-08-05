"""Neff 7-criteria SCORING + ranking — INDUSTRY-RELATIVE.

Earlier iterations enforced Neff's 7 criteria as a strict AND of hard
filters. In modern markets that produced 0 trades on most rebalance
dates because the criteria are partially mutually exclusive (e.g. high
yield + high growth + high ROE + below-industry P/E rarely co-occur).

This version uses a SOFT SCORING approach. Each criterion produces a
0-10 continuous score; the total (max 70) ranks candidates. Only
candidates with ``total_score >= MIN_TOTAL_SCORE`` (default 35,
roughly "passes half the criteria to some degree") qualify, and the
top ``portfolio_size`` by total score are bought.

Industry-relative semantics are preserved: each criterion's reference
benchmark (median P/E, yield, ROE, TR/PE) is the candidate's SIC2
industry median when at least ``MIN_INDUSTRY_PEERS`` peers exist,
otherwise the universe median.

Per-criterion scoring rubric
============================
1. **P/E sweet spot vs industry**:
   * 10 pts when P/E in [40%, 60%] of industry median.
   * Linear ramp 10→0 as P/E rises from 60% to 100% of median.
   * Linear ramp 10→0 as P/E falls from 40% to 10% of median
     (very low P/E often signals distress, not value).
   * 0 pts when P/E ≥ median, P/E ≤ 0, or P/E < 10% of median.

2. **Dividend yield above industry**:
   * 10 pts when yield ≥ industry median + 2pp.
   * Linear ramp 0→10 as yield rises from median to median+2pp.
   * 0 pts at or below median.

3. **ROE above industry**:
   * 10 pts when ROE ≥ 1.5× industry median (and ≥ absolute floor).
   * Linear ramp 0→10 from median to 1.5× median.
   * 0 pts at or below industry median (or below absolute floor).

4. **Total-Return / P/E above industry** (the SIGNATURE metric):
   * 10 pts when TR/PE ≥ 2× industry median.
   * Linear ramp 0→10 from median to 2× median.
   * 0 pts at or below industry median.

5. **EPS growth in Neff's sweet spot [7%, 20%]**:
   * 10 pts when EPS growth ∈ [7%, 20%].
   * Linear ramp 0→10 as growth rises from 0% to 7%.
   * Linear ramp 10→0 as growth rises from 20% to 30%.
   * 0 pts when growth ≤ 0% or > 30% (too slow / too speculative).

6. **Sales growth drives EPS growth**:
   * 10 pts when sales_growth ≥ eps_growth (revenue-driven).
   * Linear ramp 0→10 as sales/eps ratio rises from 0 to 1.
   * Neutral 5 pts when EPS growth ≤ 0 (criterion not meaningful).

7. **Quarterly persistence**:
   * NOT IMPLEMENTED — we don't yet have quarterly EPS series in cache.
   * Awarded a NEUTRAL 5 pts to every candidate so the 70-pt total
     scale matches the user's spec. Will become real points once
     quarterly facts are wired in.

Total: max 70. Default qualifying threshold: 35 (50%).
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

#: Per-criterion max score and overall total max.
PER_CRITERION_MAX: float = 10.0
NUM_CRITERIA: int = 7
MAX_TOTAL_SCORE: float = PER_CRITERION_MAX * NUM_CRITERIA  # 70.0

#: Default minimum total score to qualify for selection. 35/70 = 50%.
DEFAULT_MIN_TOTAL_SCORE: float = 35.0

#: Hard ceiling on P/E as a fraction of the market median. Neff bought
#: at 40-60% of the market multiple; 1.0 is the loosest bar that is
#: still recognisably him — a low-P/E investor does not buy above the
#: market's own multiple, whatever else the candidate scores.
DEFAULT_MAX_PE_FRAC_OF_MARKET: float = 1.0

#: Persistence is deferred (no quarterly fact series yet). We award a
#: neutral 5 pts so the 70-pt scale still aligns with the user spec.
PERSISTENCE_NEUTRAL_SCORE: float = 5.0


@dataclass(frozen=True)
class NeffScore:
    """A scored candidate with per-criterion + total scores."""

    ticker: str
    price: float
    market_cap: float
    pe: float
    eps_growth_pct: float
    sales_growth_pct: float
    dividend_yield_pct: float
    roe_pct: float
    total_return_pe: float  # signature raw metric, kept for logging
    debt_to_equity: float
    net_income: float
    # Which industry-group benchmark we screened this candidate against.
    # ``None`` means "no SIC available — fell back to universe median".
    industry_sic2: int | None
    industry_peer_count: int

    # Per-criterion soft scores (each 0..10) — set by score_candidates.
    score_pe: float = 0.0
    score_yield: float = 0.0
    score_roe: float = 0.0
    score_tr_pe: float = 0.0
    score_growth: float = 0.0
    score_sales: float = 0.0
    score_persistence: float = 0.0
    total_score: float = 0.0

    # Boolean "passes hard" flags, kept for backward compat with the
    # old AND-of-7 logic + existing tests. Defined here as
    # "scored >= 8" — i.e. the criterion was strongly satisfied.
    pass_pe_window: bool = False
    pass_growth_window: bool = False
    pass_yield_premium: bool = False
    pass_tr_pe_multiple: bool = False
    pass_sales_drives_eps: bool = False
    pass_roe: bool = False


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


# ---- Per-criterion scoring functions ---------------------------------------
def _ramp(x: float, lo: float, hi: float) -> float:
    """Linear ramp 0→10 over [lo, hi]; clipped to [0, 10]."""
    if hi <= lo:
        return 0.0
    if x <= lo:
        return 0.0
    if x >= hi:
        return 10.0
    return 10.0 * (x - lo) / (hi - lo)


def _ramp_down(x: float, lo: float, hi: float) -> float:
    """Linear ramp 10→0 over [lo, hi]; clipped to [0, 10]."""
    if hi <= lo:
        return 0.0
    if x <= lo:
        return 10.0
    if x >= hi:
        return 0.0
    return 10.0 * (1.0 - (x - lo) / (hi - lo))


def _score_pe(pe: float, median_pe: float, *, pe_min_frac: float, pe_max_frac: float) -> float:
    """Sweet spot in [pe_min_frac, pe_max_frac] of industry median."""
    if pe <= 0 or median_pe <= 0:
        return 0.0
    pe_low = median_pe * pe_min_frac
    pe_high = median_pe * pe_max_frac
    if pe_low <= pe <= pe_high:
        return 10.0
    if pe < pe_low:
        # Very low P/E — could signal distress. Ramp up to sweet spot.
        very_low = median_pe * 0.10
        return _ramp(pe, very_low, pe_low)
    # P/E above sweet spot: ramp down to 0 at industry median.
    return _ramp_down(pe, pe_high, median_pe)


def _score_yield(yield_pct: float, median_yield_pct: float, *, premium_pp: float) -> float:
    """Yield premium above industry median."""
    return _ramp(yield_pct, median_yield_pct, median_yield_pct + premium_pp)


def _score_roe(roe_pct: float, median_roe_pct: float, *, abs_floor: float) -> float:
    """ROE above industry median, with absolute sanity floor."""
    floor = max(median_roe_pct, abs_floor)
    target = max(median_roe_pct * 1.5, abs_floor + 5.0)
    return _ramp(roe_pct, floor, target)


def _score_tr_pe(tr_pe: float, median_tr_pe: float, *, multiple: float) -> float:
    """Total-Return/PE above industry median (signature metric)."""
    if median_tr_pe <= 0:
        return 0.0
    return _ramp(tr_pe, median_tr_pe, median_tr_pe * multiple)


def _score_growth(eps_growth_pct: float, *, lo: float, hi: float) -> float:
    """EPS growth in Neff's sweet spot [lo, hi]."""
    if eps_growth_pct <= 0:
        return 0.0
    if lo <= eps_growth_pct <= hi:
        return 10.0
    if eps_growth_pct < lo:
        return _ramp(eps_growth_pct, 0.0, lo)
    # Above hi — ramp down. Use hi + 10 as the "too speculative" cliff.
    return _ramp_down(eps_growth_pct, hi, hi + 10.0)


def _score_sales(sales_growth_pct: float | None, eps_growth_pct: float) -> float:
    """Sales growth drives EPS growth."""
    if eps_growth_pct <= 0:
        # Criterion not meaningful when EPS is shrinking — neutral 5.
        return 5.0
    if sales_growth_pct is None:
        return 5.0
    ratio = sales_growth_pct / eps_growth_pct
    if ratio >= 1.0:
        return 10.0
    return max(0.0, 10.0 * ratio)


# ---- Aggregation helpers ---------------------------------------------------
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
    """Compute per-industry stats + the universe-wide fallback bucket."""
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
    sales_growth_floor_frac: float = DEFAULT_SALES_GROWTH_FLOOR_FRAC,  # noqa: ARG001
    use_industry_medians: bool = True,
    min_industry_peers: int = MIN_INDUSTRY_PEERS,
    min_total_score: float = DEFAULT_MIN_TOTAL_SCORE,
    require_dividend: bool = True,
    max_pe_frac_of_market: float = DEFAULT_MAX_PE_FRAC_OF_MARKET,
) -> list[NeffScore]:
    """Soft-score Neff's 7 criteria; return survivors above
    ``min_total_score`` sorted by total score descending.

    Industry-relative semantics: each candidate is scored against its
    own SIC2 industry's medians when ≥ ``min_industry_peers`` peers
    exist; else against the universe median.
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
        or universe_stats.median_roe_pct is None
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
    rejected_no_dividend = 0
    rejected_expensive = 0
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

        # ---- Hard gates, ahead of the soft score ----------------------
        # The soft-scoring refactor dissolved all seven criteria into a
        # single 35/70 threshold, so no individual criterion could
        # reject any more. A candidate strong on growth and ROE could
        # carry a zero yield and a market-multiple P/E through the gate
        # on the strength of the others — which is how the live book
        # ended up with three zero-dividend holdings and a name bought
        # at a P/E of 39.6.
        #
        # These two are not preferences Neff traded off. He called the
        # dividend a free part of total return, and his whole method is
        # buying at 40-60% of the market multiple. Scoring them is
        # right; letting them be outvoted is not.
        if require_dividend and m.yield_pct <= 0:
            rejected_no_dividend += 1
            continue
        if m.pe >= universe_stats.median_pe * max_pe_frac_of_market:
            rejected_expensive += 1
            continue

        # Per-criterion soft scores.
        s_pe = _score_pe(
            m.pe,
            stats.median_pe,
            pe_min_frac=pe_min_frac,
            pe_max_frac=pe_max_frac,
        )
        s_yield = _score_yield(
            m.yield_pct,
            stats.median_yield_pct,
            premium_pp=yield_pp_over_market,
        )
        s_roe = _score_roe(
            m.roe_pct,
            stats.median_roe_pct,
            abs_floor=min_roe_pct,
        )
        s_tr_pe = _score_tr_pe(
            m.tr_pe,
            stats.median_tr_pe,
            multiple=tr_pe_market_multiple,
        )
        s_growth = _score_growth(
            m.eps_growth_pct,
            lo=min_growth_pct,
            hi=max_growth_pct,
        )
        s_sales = _score_sales(m.sales_growth_pct, m.eps_growth_pct)
        s_persistence = PERSISTENCE_NEUTRAL_SCORE

        total = (
            s_pe + s_yield + s_roe + s_tr_pe + s_growth + s_sales + s_persistence
        )

        if total < min_total_score:
            continue

        # Backward-compat hard-pass flags ("strongly satisfied" = score ≥ 8).
        STRONG = 8.0
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
                score_pe=s_pe,
                score_yield=s_yield,
                score_roe=s_roe,
                score_tr_pe=s_tr_pe,
                score_growth=s_growth,
                score_sales=s_sales,
                score_persistence=s_persistence,
                total_score=total,
                pass_pe_window=s_pe >= STRONG,
                pass_growth_window=s_growth >= STRONG,
                pass_yield_premium=s_yield >= STRONG,
                pass_tr_pe_multiple=s_tr_pe >= STRONG,
                pass_sales_drives_eps=s_sales >= STRONG,
                pass_roe=s_roe >= STRONG,
            )
        )

    if rejected_no_dividend or rejected_expensive:
        logger.info(
            f"{as_of}: hard gates rejected {rejected_no_dividend} non-payer(s) "
            f"and {rejected_expensive} name(s) at or above "
            f"{max_pe_frac_of_market:.0%} of the market P/E "
            f"({universe_stats.median_pe:.1f})"
        )

    out.sort(key=lambda s: -s.total_score)
    return out


def select_top_n(scores: list[NeffScore], n: int) -> list[NeffScore]:
    """Take top ``n`` by total Neff score. Take all if fewer."""
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


__all__ = [
    "DEFAULT_MIN_TOTAL_SCORE",
    "MAX_TOTAL_SCORE",
    "MIN_INDUSTRY_PEERS",
    "NeffScore",
    "score_candidates",
    "select_top_n",
]
