"""The rank: which candidate gets bought first.

``COUNCIL_SELECTION.md`` section 3.

    V (value)    = mean of available: pct(EBIT/EV), pct(FCF/EV),
                                      pct(net cash / mcap)
    Q (quality)  = mean of available: pct(ROIC), F_score / 9
    M (momentum) = pct(total return from t-252 to t-21 trading days)

    composite    = 0.45 V + 0.35 Q + 0.20 M

Percentiles across the whole universe, not the passers
------------------------------------------------------

This is the detail that keeps the rank stable. If percentiles were
computed over the set that cleared the screen, a quarter in which only
eleven names passed would score its worst survivor at the same
percentile as a rich quarter's best — the scale would silently rescale
itself to whatever happened to be cheap that month. Ranking against the
whole universe means a composite of 0.8 means the same thing in every
regime.

Mean of available
-----------------

A component with no inputs is skipped rather than scored zero, so a cash
box with negative EBIT still ranks on net cash and free cash flow. The
one exception is V: a name with **no computable value component at all**
is not mechanically investable, and that is a feature. The statistical
sleeve buys cheapness it can measure; a company it cannot price belongs
to the Council's reading queue or to nobody.

The knife guard
---------------

The bottom decile of momentum is excluded from *buying* this quarter
regardless of composite. Cheap-and-collapsing is allowed to finish
collapsing; the rank will still be there next quarter. Momentum uses
the 12-1 window — twelve months to one month ago — because the most
recent month reverses.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.logger import get_logger

logger = get_logger("agents.council.rank")

#: Composite weights. They sum to 1.0 and are checked at import.
WEIGHT_VALUE: float = 0.45
WEIGHT_QUALITY: float = 0.35
WEIGHT_MOMENTUM: float = 0.20

#: Momentum percentile at or below which a name may not be bought.
KNIFE_GUARD_PERCENTILE: float = 0.10

#: Piotroski's scale, used to put F-score on the same 0-1 footing as a
#: percentile. It is an absolute score rather than a relative one, so it
#: is divided rather than ranked.
F_SCORE_MAX: int = 9

#: Section 4: the statistical sleeve holds twenty names.
DEFAULT_BASKET_SIZE: int = 20

#: Section 3's mechanical proxy for the doctrine's correlated-cluster
#: rule: at most this many of the held names per 2-digit SIC division.
DEFAULT_SECTOR_CAP: int = 5

assert abs(WEIGHT_VALUE + WEIGHT_QUALITY + WEIGHT_MOMENTUM - 1.0) < 1e-9


@dataclass(frozen=True)
class RankInputs:
    """One company's raw rank components, before percentiles.

    Ratios rather than levels, so the percentile step compares like with
    like. ``None`` means not computable and is skipped, never zeroed —
    a company with no reported ROIC is not a company with zero ROIC.
    """

    ticker: str
    ebit_to_ev: float | None = None
    fcf_to_ev: float | None = None
    net_cash_to_market_cap: float | None = None
    roic: float | None = None
    f_score: int | None = None
    momentum_12_1: float | None = None
    sic2: int | None = None
    #: Two or more insiders including a C-suite officer buying on the
    #: open market outside a 10b5-1 plan, in the last 90 days. Breaks an
    #: equal composite.
    insider_cluster: bool = False


@dataclass(frozen=True)
class Ranked:
    """A company's place in the rank, with the parts that made it."""

    ticker: str
    composite: float
    value: float | None
    quality: float | None
    momentum: float | None
    #: True when momentum sits in the bottom decile. Such a name keeps
    #: its rank — E8's exit buffer still reads it — but may not be
    #: bought this quarter.
    knife_guarded: bool
    sic2: int | None
    insider_cluster: bool


def percentiles(values: Sequence[float | None]) -> list[float | None]:
    """Rank each value in 0..1, higher = better, ``None`` preserved.

    Ties share a mid-rank so that a universe where half the names report
    the same figure does not hand one of them an advantage decided by
    list order. ``None`` stays ``None`` and is excluded from the
    denominator: a metric two thousand companies do not report should
    not compress the scale for the eight hundred that do.
    """
    known = sorted(v for v in values if v is not None)
    n = len(known)
    if n == 0:
        return [None] * len(values)
    if n == 1:
        return [None if v is None else 0.5 for v in values]

    import bisect

    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        below = bisect.bisect_left(known, v)
        equal = bisect.bisect_right(known, v) - below
        out.append((below + 0.5 * equal) / n)
    return out


def _mean_of_available(parts: Sequence[float | None]) -> float | None:
    present = [p for p in parts if p is not None]
    if not present:
        return None
    return sum(present) / len(present)


def rank_universe(rows: Sequence[RankInputs]) -> list[Ranked]:
    """Score and order the whole universe, best first.

    Args:
        rows: Every name in the section-1 universe, not only the ones
            that cleared the screen. Passing only the passers is the one
            mistake that quietly changes what a composite means.

    Returns:
        :class:`Ranked` entries sorted by composite descending, then by
        insider cluster, then by ticker so the order is deterministic.
        Names with no computable value component are dropped — they are
        not mechanically investable and carrying them would let a pure
        momentum score float to the top of a value screen.
    """
    if not rows:
        return []

    ebit_pct = percentiles([r.ebit_to_ev for r in rows])
    fcf_pct = percentiles([r.fcf_to_ev for r in rows])
    cash_pct = percentiles([r.net_cash_to_market_cap for r in rows])
    roic_pct = percentiles([r.roic for r in rows])
    mom_pct = percentiles([r.momentum_12_1 for r in rows])

    ranked: list[Ranked] = []
    for i, row in enumerate(rows):
        value = _mean_of_available([ebit_pct[i], fcf_pct[i], cash_pct[i]])
        if value is None:
            continue
        f_component = (
            None if row.f_score is None else row.f_score / F_SCORE_MAX
        )
        quality = _mean_of_available([roic_pct[i], f_component])
        momentum = mom_pct[i]

        # A missing component drops out of the weighted mean and the
        # remaining weights are renormalised, so a name is not punished
        # for a metric nobody in its industry reports.
        weighted = [
            (value, WEIGHT_VALUE),
            (quality, WEIGHT_QUALITY),
            (momentum, WEIGHT_MOMENTUM),
        ]
        live = [(v, w) for v, w in weighted if v is not None]
        total_weight = sum(w for _, w in live)
        composite = sum(v * w for v, w in live) / total_weight

        # An unreadable momentum is guarded out rather than waved
        # through. It means a missing year of prices, and buying into
        # that blind is the one thing the guard exists to prevent.
        knife_guarded = momentum is None or momentum <= KNIFE_GUARD_PERCENTILE

        ranked.append(
            Ranked(
                ticker=row.ticker,
                composite=composite,
                value=value,
                quality=quality,
                momentum=momentum,
                knife_guarded=knife_guarded,
                sic2=row.sic2,
                insider_cluster=row.insider_cluster,
            )
        )

    ranked.sort(key=lambda r: (-r.composite, not r.insider_cluster, r.ticker))
    return ranked


def select_basket(
    ranked: Sequence[Ranked],
    *,
    eligible: Sequence[str] | None = None,
    size: int = DEFAULT_BASKET_SIZE,
    sector_cap: int = DEFAULT_SECTOR_CAP,
) -> list[Ranked]:
    """The names to hold, in rank order, subject to the sector cap.

    Args:
        ranked: Output of :func:`rank_universe`, whole universe.
        eligible: Tickers that cleared the screen. ``None`` treats every
            ranked name as eligible, which is only correct in tests —
            the rank is computed over the universe precisely so that the
            screen can be applied separately.
        size: How many names to hold. Fewer if fewer qualify: the system
            is allowed to say nothing is cheap enough and hold cash,
            and that is a normal outcome rather than a failure.
        sector_cap: Most names per 2-digit SIC division. A name with no
            SIC is not capped, because grouping every unclassified
            company together would invent a sector that does not exist —
            they are counted individually instead.

    Returns:
        Up to ``size`` names. Knife-guarded names are skipped.
    """
    allowed = None if eligible is None else {t.upper() for t in eligible}
    per_sector: dict[int, int] = {}
    basket: list[Ranked] = []
    skipped_by_sector = 0

    for entry in ranked:
        if len(basket) >= size:
            break
        if allowed is not None and entry.ticker.upper() not in allowed:
            continue
        if entry.knife_guarded:
            continue
        if entry.sic2 is not None:
            if per_sector.get(entry.sic2, 0) >= sector_cap:
                skipped_by_sector += 1
                continue
            per_sector[entry.sic2] = per_sector.get(entry.sic2, 0) + 1
        basket.append(entry)

    if len(basket) < size:
        # Never silent: a short basket is a legitimate answer, and the
        # run log has to say so rather than let a reader assume the
        # sleeve is full.
        logger.info(
            f"basket holds {len(basket)} of {size} "
            f"({skipped_by_sector} skipped by the sector cap)"
        )
    return basket
