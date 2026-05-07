"""PEG / PEGY ratio + growth rate sourcing — Lynch's signature math.

Per playbook §5:

    PEG  = P/E / Annual EPS Growth Rate (in %)
    PEGY = P/E / (Growth Rate + Dividend Yield, both in %)

Lynch's interpretation (§5.1):

    PEG < 0.5     → strong buy
    PEG 0.5–1.0   → buy
    PEG ≈ 1.0     → fair value
    PEG 1.0–2.0   → hold (don't initiate)
    PEG > 2.0     → sell / avoid

Growth rate sourcing (§5.3) — historical, not analyst projections:

    * 5-year EPS CAGR primary
    * 3-year CAGR cross-check (acceleration / deceleration signal)
    * Never rely on sell-side forward estimates (systematically optimistic)

For cyclicals: trough-to-trough, not peak-to-peak. The pure-math
helpers here don't enforce that — the strategy layer picks the right
window per category.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

logger = get_logger("agents.lynch.peg")


# ---- Buy-zone thresholds per playbook §5.1 / §11.2 -----------------------
PEG_STRONG_BUY: float = 0.5
PEG_BUY: float = 1.0
PEG_HOLD: float = 2.0
PEG_FLOOR: float = 0.0  # PEG must be positive (negative growth → not Lynch)


# ---- XBRL concept aliases -------------------------------------------------
_EPS_DILUTED_CONCEPT: str = "EarningsPerShareDiluted"
_EPS_BASIC_CONCEPT: str = "EarningsPerShareBasic"


@dataclass(frozen=True)
class PegResult:
    """Output of :func:`peg_for`."""

    pe: float
    growth_rate_pct: float
    dividend_yield_pct: float
    peg: float
    pegy: float


# ---- Pure math ------------------------------------------------------------
def peg_ratio(pe: float | None, growth_rate_pct: float | None) -> float | None:
    """PEG = P/E / growth%. None when inputs missing or growth ≤ 0."""
    if pe is None or growth_rate_pct is None:
        return None
    if pe <= 0 or growth_rate_pct <= 0:
        # Lynch's PEG requires positive growth — companies with negative
        # or zero growth are not Lynch candidates (other agents handle
        # them). Returning None is the honest answer.
        return None
    return pe / growth_rate_pct


def pegy_ratio(
    pe: float | None,
    growth_rate_pct: float | None,
    dividend_yield_pct: float | None,
) -> float | None:
    """PEGY = P/E / (growth% + yield%). None when inputs missing or
    the combined growth+yield denominator is non-positive.
    """
    if pe is None or growth_rate_pct is None:
        return None
    if pe <= 0:
        return None
    yld = dividend_yield_pct if dividend_yield_pct is not None else 0.0
    denom = growth_rate_pct + yld
    if denom <= 0:
        return None
    return pe / denom


def peg_buy_zone(peg: float | None) -> str:
    """String label for the PEG-zone bucket. Stable for use in audit
    logs and the dashboard."""
    if peg is None:
        return "n/a"
    if peg < PEG_STRONG_BUY:
        return "strong_buy"
    if peg <= PEG_BUY:
        return "buy"
    if peg <= PEG_HOLD:
        return "hold"
    return "avoid"


# ---- Growth-rate sourcing -------------------------------------------------
def _eps_at(
    cache: EdgarCache, ticker: str, as_of: date
) -> tuple[float, int] | None:
    """Most recent annual diluted (or basic) EPS at or before ``as_of``.

    Returns ``(eps, fiscal_year)`` or ``None``.
    """
    for concept in (_EPS_DILUTED_CONCEPT, _EPS_BASIC_CONCEPT):
        fact = cache.latest_value_at(
            ticker, concept, as_of, forms=("10-K",), prefer_annual=True
        )
        if fact is not None:
            return float(fact.value), int(fact.fiscal_year)
    return None


def trailing_eps_cagr_pct(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    years: int = 5,
) -> float | None:
    """EPS CAGR (in %) over the most recent ``years`` fiscal years.

    Walks backwards in 1-year steps from ``as_of`` to find a "now" EPS
    and a "then" EPS spaced ``years`` apart. Returns None when:

      * either anchor EPS is missing
      * "then" EPS ≤ 0 (CAGR undefined for non-positive base)
      * "now" EPS ≤ 0 (returns -100% to flag a collapse)

    Lynch was emphatic that growth must be HISTORICAL and DEMONSTRATED,
    not projected (playbook §5.3).
    """
    now = _eps_at(cache, ticker, as_of)
    then_date = as_of - timedelta(days=int(365.25 * years))
    then = _eps_at(cache, ticker, then_date)
    if now is None or then is None:
        return None
    now_eps, now_fy = now
    then_eps, then_fy = then
    n = now_fy - then_fy
    if n <= 0:
        return None
    if then_eps <= 0:
        return None
    if now_eps <= 0:
        return -100.0
    cagr = (now_eps / then_eps) ** (1.0 / n) - 1.0
    return cagr * 100.0


def acceleration_pct(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
) -> float | None:
    """3-yr CAGR minus 5-yr CAGR, in pp. Positive = accelerating.

    Lynch used this as a quality signal: a Fast Grower whose growth
    is accelerating is a stronger candidate than one decelerating.
    """
    cagr_3 = trailing_eps_cagr_pct(cache, ticker, as_of, years=3)
    cagr_5 = trailing_eps_cagr_pct(cache, ticker, as_of, years=5)
    if cagr_3 is None or cagr_5 is None:
        return None
    return cagr_3 - cagr_5


# ---- Composite ------------------------------------------------------------
def peg_for(
    *,
    pe: float | None,
    growth_rate_pct: float | None,
    dividend_yield_pct: float | None,
) -> PegResult | None:
    """Bundle PEG + PEGY into one record. Returns None when PEG can't
    be computed (the strategy layer rejects on None)."""
    if pe is None or growth_rate_pct is None:
        return None
    peg = peg_ratio(pe, growth_rate_pct)
    if peg is None:
        return None
    yld = dividend_yield_pct if dividend_yield_pct is not None else 0.0
    pegy = pegy_ratio(pe, growth_rate_pct, yld)
    return PegResult(
        pe=pe,
        growth_rate_pct=growth_rate_pct,
        dividend_yield_pct=yld,
        peg=peg,
        pegy=pegy if pegy is not None else peg,
    )


__all__ = [
    "PEG_BUY",
    "PEG_FLOOR",
    "PEG_HOLD",
    "PEG_STRONG_BUY",
    "PegResult",
    "acceleration_pct",
    "peg_buy_zone",
    "peg_for",
    "peg_ratio",
    "pegy_ratio",
    "trailing_eps_cagr_pct",
]
