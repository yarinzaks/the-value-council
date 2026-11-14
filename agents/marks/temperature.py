"""Market Temperature assessment — Marks's cycle-positioning core.

Per playbook §4.1, before evaluating any individual security, Marks
asks: "Where is the pendulum?" The result is one of five buckets:

  Cold | Cool | Neutral | Warm | Hot

This module derives a temperature score from observable universe-wide
data at a point in time. It does NOT predict the future — Marks
explicitly rejects forecasting (§3.1 principle 5). It only describes
where the market currently sits on observable axes.

Signal sources (all computable from PIT data + the EDGAR cache):

  1. Universe median trailing P/E (positive-EPS subset).
     Cold when LOW (≤ 14); Hot when HIGH (≥ 22) — playbook §4.4.
  2. Fraction of universe with non-positive trailing net income.
     Cold when HIGH (distress widespread, ≥ 30%); Hot when LOW (≤ 10%).
  3. Universe median D/E ratio.
     Cold when LOW (deleveraged, ≤ 0.4); Hot when HIGH (≥ 0.7).
  4. Fraction of universe with D/E > 1.0 ("excess-leverage cohort").
     Cold when LOW (≤ 15%); Hot when HIGH (≥ 35%).
  5. Universe median dividend yield.
     Cold when HIGH (capital scarce, ≥ 3.5%); Hot when LOW (≤ 1.5%).

Each signal contributes one vote on a -2..+2 scale (-2 = strongly
Cold, +2 = strongly Hot). The composite score determines the posture.

Documented heuristic — Marks's own checklist (§4.1) is qualitative;
this is the closest faithful quantification we can build from EDGAR
data alone. Signals NOT available from XBRL (deferred to LLM
second-level analysis in live mode): VIX, IPO volume, credit
spreads, default rates, SLO survey, sentiment indices.

What the caller must pass
~~~~~~~~~~~~~~~~~~~~~~~~~

The **whole measurable universe**, before any quality gate. Marks's
question is where the market sits; a screened subset answers a
different question and answers it backwards.

:class:`agents.marks.cycle_value.MarksCycleValue` used to call this on
its post-gate survivors, and two of the five signals inverted as a
result. At 2026-04-01, 46% of the 537 measurable tickers had negative
net income — widespread distress, a strongly Cold reading — while 0% of
the 107 survivors did, because the gates exclude them. The signal
designed to detect distress was being shown a population with the
distress removed, and voted strongly Hot. The high-leverage fraction
inverted the same way against ``max_de``. Composite -1.0 (Neutral)
instead of -2.0 (Cool), which is the difference between deploying 80%
across 18 names and 90% across 22.

Dropping rows with missing data is fine and necessary. Dropping rows
for being *bad* is what breaks it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger
from core.scoring.leverage import debt_to_equity as _shared_debt_to_equity

logger = get_logger("agents.marks.temperature")


Posture = Literal["Cold", "Cool", "Neutral", "Warm", "Hot"]


# ---- Posture thresholds (composite score) ---------------------------------
#: Sum-of-signals score → posture bucket. With 5 signals each voting
#: -2..+2, raw range is -10..+10.
def _posture_for(score: float) -> Posture:
    if score <= -4.0:
        return "Cold"
    if score <= -1.5:
        return "Cool"
    if score < 1.5:
        return "Neutral"
    if score < 4.0:
        return "Warm"
    return "Hot"


def posture_for_score(score: float) -> Posture:
    """The posture a composite score reads as on its own, with no history."""
    return _posture_for(score)


# ---- Per-signal voting functions ------------------------------------------
# Each function returns -2..+2 where -2 = strongly Cold, +2 = strongly Hot.
# Direction-of-coldness varies per metric — coded explicitly here so we
# never fight a generic band's assumed ordering.

def _vote_pe(median_pe: float) -> int:
    """Low PE = Cold (cheap), high PE = Hot (expensive)."""
    if median_pe <= 12.0:
        return -2
    if median_pe <= 14.0:
        return -1
    if median_pe >= 26.0:
        return 2
    if median_pe >= 22.0:
        return 1
    return 0


def _vote_neg_ni_frac(frac: float) -> int:
    """High distress fraction = Cold; low = Hot (euphoria)."""
    if frac >= 0.40:
        return -2
    if frac >= 0.30:
        return -1
    if frac <= 0.05:
        return 2
    if frac <= 0.10:
        return 1
    return 0


def _vote_de(median_de: float) -> int:
    """Low D/E = Cold (deleveraged); high = Hot (loose lending)."""
    if median_de <= 0.30:
        return -2
    if median_de <= 0.40:
        return -1
    if median_de >= 0.90:
        return 2
    if median_de >= 0.70:
        return 1
    return 0


def _vote_high_de_frac(frac: float) -> int:
    """Low fraction = Cold; high fraction = Hot (excess leverage)."""
    if frac <= 0.10:
        return -2
    if frac <= 0.15:
        return -1
    if frac >= 0.45:
        return 2
    if frac >= 0.35:
        return 1
    return 0


def _vote_yield(median_yield_pct: float) -> int:
    """High yield = Cold (capital expensive); low = Hot (compressed)."""
    if median_yield_pct >= 4.5:
        return -2
    if median_yield_pct >= 3.5:
        return -1
    if median_yield_pct <= 1.0:
        return 2
    if median_yield_pct <= 1.5:
        return 1
    return 0


@dataclass(frozen=True)
class TemperatureSignals:
    """Per-signal raw values for transparency / audit logs."""

    universe_size: int
    median_pe: float | None
    frac_negative_ni: float
    median_de: float | None
    frac_high_de: float
    median_yield_pct: float


@dataclass(frozen=True)
class TemperatureAssessment:
    """Output of :func:`assess_market_temperature`."""

    as_of: date
    score: float
    posture: Posture
    signals: TemperatureSignals
    votes: dict[str, int]
    #: What ``score`` reads as on its own, when :func:`settle_posture`
    #: kept a different ``posture``; None when the two are the same
    #: reading (no history was consulted, or it agreed).
    measured_posture: Posture | None = None


# ---- Universe-stat helpers -----------------------------------------------
def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _pe(price: float, fin: PointInTimeFinancials) -> float | None:
    eps = fin.eps_diluted if fin.eps_diluted is not None else fin.eps_basic
    if eps is None or eps <= 0:
        return None
    if price <= 0:
        return None
    return price / eps


def _de(fin: PointInTimeFinancials) -> float | None:
    """D/E via the shared helper, which refuses to call an untagged
    balance sheet debt-free.

    This used to return 0.0 whenever no debt concept was tagged — the
    best possible reading on both leverage signals, awarded on no
    evidence. At 2026-04-01, 182 of 537 measurable tickers read exactly
    0.000, and for 60 of them the shared helper declines to judge at
    all. Sixty companies about which nothing was known were voting for
    a deleveraged market, dragging the median down and the posture
    colder than the evidence supports.

    See :mod:`core.scoring.leverage` for what separates a genuinely
    debt-free filer from a sparse one.
    """
    return _shared_debt_to_equity(fin)


def _div_yield_pct(market_cap: float, fin: PointInTimeFinancials) -> float | None:
    if market_cap <= 0:
        return None
    if fin.dividends_paid is None:
        return 0.0
    return 100.0 * abs(fin.dividends_paid) / market_cap


def _signals_from(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
) -> TemperatureSignals:
    """Compute the five universe-wide signals."""
    pes: list[float] = []
    des: list[float] = []
    yields: list[float] = []
    neg_ni = 0
    high_de = 0
    n_total = 0
    n_de_ok = 0

    for fin, mcap, price in candidates:
        n_total += 1
        if fin.net_income is not None and fin.net_income <= 0:
            neg_ni += 1
        pe = _pe(price, fin)
        if pe is not None and 0 < pe < 100:
            pes.append(pe)
        de = _de(fin)
        if de is not None:
            n_de_ok += 1
            des.append(de)
            if de > 1.0:
                high_de += 1
        y = _div_yield_pct(mcap, fin)
        if y is not None:
            yields.append(y)

    return TemperatureSignals(
        universe_size=n_total,
        median_pe=_median(pes),
        frac_negative_ni=(neg_ni / n_total) if n_total else 0.0,
        median_de=_median(des),
        frac_high_de=(high_de / n_de_ok) if n_de_ok else 0.0,
        median_yield_pct=_median(yields) or 0.0,
    )


# ---- Main entry point -----------------------------------------------------
def assess_market_temperature(
    candidates: list[tuple[PointInTimeFinancials, float, float]],
    *,
    as_of: date,
) -> TemperatureAssessment:
    """Score the market temperature from the universe at ``as_of``.

    Inputs are ``(fin, market_cap, price)`` triples for every viable
    candidate. The function does NOT filter; it only describes the
    universe.

    Posture buckets:

        score ≤ -4.0       → Cold        (aggressive deployment)
        -4.0  < score ≤ -1.5 → Cool      (active, lean toward buying)
        -1.5  < score < 1.5  → Neutral   (selective)
         1.5 ≤ score < 4.0   → Warm      (defensive, raise quality bar)
         4.0 ≤ score         → Hot       (maximum cash, await dislocation)
    """
    if not candidates:
        return TemperatureAssessment(
            as_of=as_of,
            score=0.0,
            posture="Neutral",
            signals=TemperatureSignals(
                universe_size=0,
                median_pe=None,
                frac_negative_ni=0.0,
                median_de=None,
                frac_high_de=0.0,
                median_yield_pct=0.0,
            ),
            votes={},
        )

    s = _signals_from(candidates)
    votes: dict[str, int] = {}
    score = 0.0

    if s.median_pe is not None:
        v = _vote_pe(s.median_pe)
        votes["median_pe"] = v
        score += v
    if s.median_de is not None:
        v = _vote_de(s.median_de)
        votes["median_de"] = v
        score += v
    v = _vote_neg_ni_frac(s.frac_negative_ni)
    votes["frac_negative_ni"] = v
    score += v
    v = _vote_high_de_frac(s.frac_high_de)
    votes["frac_high_de"] = v
    score += v
    v = _vote_yield(s.median_yield_pct)
    votes["median_yield_pct"] = v
    score += v

    posture = _posture_for(score)

    logger.info(
        f"{as_of}: market temperature score={score:+.1f} → {posture} "
        f"(median PE={s.median_pe}, neg-NI={s.frac_negative_ni:.2%}, "
        f"median D/E={s.median_de}, hi-D/E={s.frac_high_de:.2%}, "
        f"median yield={s.median_yield_pct:.2f}%)"
    )

    return TemperatureAssessment(
        as_of=as_of,
        score=score,
        posture=posture,
        signals=s,
        votes=votes,
    )


# ---- Posture persistence (audit item 21) ----------------------------------
#: Sessions a newly measured posture must hold, today included, before
#: the agent acts on it.
#:
#: The composite is a sum of five integer votes, and the Cool/Neutral
#: edge sits at -1.5 — so one signal moving one notch is enough to cross
#: it. From 2026-08-05 to 2026-11-13 the live score alternated between
#: -1 and -2 and the posture flipped Cool<->Neutral 8 times, each flip
#: resizing the book between 22 names at 90% and 18 at 80%, with the
#: 30-day floor keeping the previous posture's names on top.
#:
#: A score band cannot fix that: the scores are whole numbers, so a band
#: narrower than one vote changes nothing and one a full vote wide
#: swallows both -1 and -2 and never leaves whichever posture it started
#: in. Persistence does what the playbook asks of the pendulum — read
#: where it is, not where one day's filings nudged it. Three sessions is
#: the shortest run that absorbs every one- and two-session excursion in
#: that series (8 flips become 3) while still following the three-
#: session moves; the playbook gives no number.
POSTURE_PERSISTENCE_SESSIONS = 3

_POSTURE_ORDER: dict[Posture, int] = {
    "Cold": 0,
    "Cool": 1,
    "Neutral": 2,
    "Warm": 3,
    "Hot": 4,
}


@dataclass(frozen=True)
class PostureReading:
    """One past session: the raw composite and the posture acted on."""

    as_of: date
    score: float
    posture: Posture


def settle_posture(
    measured: Posture,
    as_of: date,
    history: Sequence[PostureReading],
    *,
    sessions: int = POSTURE_PERSISTENCE_SESSIONS,
) -> Posture:
    """The posture to act on at ``as_of``, given what was held before.

    ``history`` is the agent's past sessions; readings on or after
    ``as_of`` are ignored (a second run on the same day must not count
    itself), and of several readings on one day the last is kept.

    With no earlier session the measurement stands. Otherwise the held
    posture (the latest session's) stands until the measured posture —
    each past session's re-read from its score, plus today's — has sat
    on the same side of it for ``sessions`` sessions running. The move
    then goes only as far as every one of those sessions agrees: held
    Neutral and measured Cool, Cold, Cold settles on Cool.
    """
    if sessions < 1:
        raise ValueError(f"sessions must be at least 1; got {sessions}")
    by_day: dict[date, PostureReading] = {}
    for reading in history:
        if reading.as_of < as_of:
            by_day[reading.as_of] = reading
    if not by_day:
        return measured
    prior = [by_day[d] for d in sorted(by_day)]
    held = prior[-1].posture
    if measured == held:
        return measured
    needed = sessions - 1
    recent = prior[len(prior) - needed:] if needed else []
    if len(recent) < needed:
        return held
    run = [_posture_for(r.score) for r in recent] + [measured]
    anchor = _POSTURE_ORDER[held]
    colder = _POSTURE_ORDER[measured] < anchor
    for p in run:
        rank = _POSTURE_ORDER[p]
        if rank == anchor or (rank < anchor) != colder:
            return held
    return min(run, key=lambda p: abs(_POSTURE_ORDER[p] - anchor))


# ---- Posture → deployment intensity ---------------------------------------
@dataclass(frozen=True)
class PostureProfile:
    """Per-posture deployment profile (playbook §6.2).

    ``portfolio_size``          — number of positions to hold
    ``deployed_fraction``       — total weight (1.0 = fully invested);
                                  residual is held as cash
    ``max_single_position_pct`` — per-name cap, in %
    """

    posture: Posture
    portfolio_size: int
    deployed_fraction: float
    max_single_position_pct: float


_PROFILES: dict[Posture, PostureProfile] = {
    "Cold": PostureProfile("Cold", 25, 0.95, 7.0),
    "Cool": PostureProfile("Cool", 22, 0.90, 6.0),
    "Neutral": PostureProfile("Neutral", 18, 0.80, 5.0),
    "Warm": PostureProfile("Warm", 14, 0.65, 5.0),
    "Hot": PostureProfile("Hot", 10, 0.50, 5.0),
}


def profile_for(posture: Posture) -> PostureProfile:
    """Return the deployment profile for a given posture."""
    return _PROFILES[posture]


__all__ = [
    "POSTURE_PERSISTENCE_SESSIONS",
    "Posture",
    "PostureProfile",
    "PostureReading",
    "TemperatureAssessment",
    "TemperatureSignals",
    "assess_market_temperature",
    "posture_for_score",
    "profile_for",
    "settle_posture",
]
