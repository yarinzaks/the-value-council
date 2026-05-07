"""Cycle-aware value ranking for Marks candidates.

After temperature assessment (:mod:`temperature`), this module ranks
the surviving universe with a composite cycle-adjusted value score:

  * **Earnings yield** (1/PE) — base value signal, higher is cheaper
  * **FCF yield** (FCF / market cap) — Marks's preferred quality cut
  * **Dividend yield** — capital-return signal
  * **Balance-sheet penalty** — D/E above 0.6 docks the score

The weighting tilts with cycle posture:

  * **Cold / Cool**: emphasize deep value (earnings yield + dividend
    yield); accept higher D/E because Marks deploys aggressively
    into distress when the pendulum is at fear.
  * **Hot / Warm**: emphasize quality + balance-sheet strength;
    reject any D/E > 0.6 (raise the bar).
  * **Neutral**: balanced.

Exit signature: a list of :class:`MarksScore` sorted by total score
descending. The strategy layer takes the top ``portfolio_size`` per
the posture profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

from .filters import debt_to_equity
from .temperature import Posture, TemperatureAssessment

logger = get_logger("agents.marks.ranking")


_OCF_CONCEPTS: tuple[str, ...] = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
_CAPEX_CONCEPTS: tuple[str, ...] = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
)


@dataclass(frozen=True)
class MarksScore:
    """A scored candidate with cycle-adjusted total score."""

    ticker: str
    price: float
    market_cap: float
    pe: float
    earnings_yield_pct: float
    fcf_yield_pct: float
    dividend_yield_pct: float
    debt_to_equity: float
    net_income: float
    posture_at_score: Posture
    total_score: float


# ---- Per-component helpers -----------------------------------------------
def _pe(price: float, fin: PointInTimeFinancials) -> float | None:
    eps = fin.eps_diluted if fin.eps_diluted is not None else fin.eps_basic
    if eps is None or eps <= 0 or price <= 0:
        return None
    return price / eps


def _earnings_yield_pct(pe: float) -> float:
    return 100.0 / pe


def _latest_fcf_yield_pct(
    cache: EdgarCache,
    ticker: str,
    market_cap: float,
    as_of: date,
) -> float | None:
    """Most recent annual FCF (OCF − Capex) divided by market cap.

    Returns None if either OCF or capex is missing. Returning None
    causes the caller to treat the FCF signal as "unknown" and fall
    back to a neutral contribution.
    """
    if market_cap <= 0:
        return None
    ocf_fact = None
    for c in _OCF_CONCEPTS:
        ocf_fact = cache.latest_value_at(
            ticker, c, as_of, forms=("10-K",), prefer_annual=True
        )
        if ocf_fact is not None:
            break
    if ocf_fact is None:
        return None
    capex_fact = None
    for c in _CAPEX_CONCEPTS:
        capex_fact = cache.latest_value_at(
            ticker, c, as_of, forms=("10-K",), prefer_annual=True
        )
        if capex_fact is not None:
            break
    if capex_fact is None:
        return None
    fcf = float(ocf_fact.value) - abs(float(capex_fact.value))
    return 100.0 * fcf / market_cap


def _dividend_yield_pct(
    market_cap: float, fin: PointInTimeFinancials
) -> float:
    if market_cap <= 0:
        return 0.0
    if fin.dividends_paid is None:
        return 0.0
    return 100.0 * abs(fin.dividends_paid) / market_cap


# ---- Cycle-aware weights -------------------------------------------------
@dataclass(frozen=True)
class _Weights:
    earnings_yield: float
    fcf_yield: float
    dividend_yield: float
    de_penalty_threshold: float  # D/E above this docks the score
    de_penalty_per_unit: float   # points docked per unit of D/E above threshold
    de_hard_reject_above: float | None  # if set, reject candidates above this
    quality_floor_eyld_pct: float  # min earnings yield to even rank


_WEIGHTS_BY_POSTURE: dict[Posture, _Weights] = {
    "Cold": _Weights(
        earnings_yield=1.0,
        fcf_yield=0.7,
        dividend_yield=0.5,
        de_penalty_threshold=0.8,
        de_penalty_per_unit=2.0,
        de_hard_reject_above=None,  # accept even leveraged in distress
        quality_floor_eyld_pct=4.0,
    ),
    "Cool": _Weights(
        earnings_yield=1.0,
        fcf_yield=0.8,
        dividend_yield=0.5,
        de_penalty_threshold=0.7,
        de_penalty_per_unit=2.5,
        de_hard_reject_above=None,
        quality_floor_eyld_pct=4.5,
    ),
    "Neutral": _Weights(
        earnings_yield=0.9,
        fcf_yield=1.0,
        dividend_yield=0.6,
        de_penalty_threshold=0.6,
        de_penalty_per_unit=3.0,
        de_hard_reject_above=0.9,
        quality_floor_eyld_pct=5.0,
    ),
    "Warm": _Weights(
        earnings_yield=0.8,
        fcf_yield=1.1,
        dividend_yield=0.7,
        de_penalty_threshold=0.5,
        de_penalty_per_unit=4.0,
        de_hard_reject_above=0.7,
        quality_floor_eyld_pct=5.5,
    ),
    "Hot": _Weights(
        earnings_yield=0.7,
        fcf_yield=1.2,
        dividend_yield=0.8,
        de_penalty_threshold=0.4,
        de_penalty_per_unit=5.0,
        de_hard_reject_above=0.6,
        quality_floor_eyld_pct=6.5,
    ),
}


def score_candidates(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
    *,
    as_of: date,
    edgar_cache: EdgarCache,
    temperature: TemperatureAssessment,
) -> list[MarksScore]:
    """Score each candidate with cycle-adjusted weights.

    Inputs are post-quality-gate. The function additionally enforces
    posture-specific D/E hard rejects and earnings-yield floors —
    Marks raises the bar in hot cycles.
    """
    if not candidates:
        return []

    weights = _WEIGHTS_BY_POSTURE[temperature.posture]
    out: list[MarksScore] = []

    for fin, mcap, price in candidates:
        pe = _pe(price, fin)
        if pe is None:
            continue
        eyld = _earnings_yield_pct(pe)
        if eyld < weights.quality_floor_eyld_pct:
            continue

        de = debt_to_equity(fin)
        if de is None:
            continue
        if (
            weights.de_hard_reject_above is not None
            and de > weights.de_hard_reject_above
        ):
            continue

        fcf_y = _latest_fcf_yield_pct(edgar_cache, fin.ticker, mcap, as_of)
        # FCF unknown → treat as 0 (neutral contribution).
        fcf_y_effective = fcf_y if fcf_y is not None else 0.0

        div_y = _dividend_yield_pct(mcap, fin)

        # D/E penalty: only docks when D/E > threshold.
        de_excess = max(0.0, de - weights.de_penalty_threshold)
        de_penalty = de_excess * weights.de_penalty_per_unit

        total = (
            weights.earnings_yield * eyld
            + weights.fcf_yield * fcf_y_effective
            + weights.dividend_yield * div_y
            - de_penalty
        )

        out.append(
            MarksScore(
                ticker=fin.ticker,
                price=price,
                market_cap=mcap,
                pe=pe,
                earnings_yield_pct=eyld,
                fcf_yield_pct=fcf_y_effective,
                dividend_yield_pct=div_y,
                debt_to_equity=de,
                net_income=fin.net_income or 0.0,
                posture_at_score=temperature.posture,
                total_score=total,
            )
        )

    out.sort(key=lambda s: -s.total_score)
    return out


def select_top_n(scores: list[MarksScore], n: int) -> list[MarksScore]:
    """Take the top ``n`` by total cycle-adjusted score."""
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


__all__ = [
    "MarksScore",
    "score_candidates",
    "select_top_n",
]
