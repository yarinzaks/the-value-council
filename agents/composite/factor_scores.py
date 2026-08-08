"""The three measurements the composite ranks on, and the rank itself.

Why these three
~~~~~~~~~~~~~~~

The council already holds ten value investors. A eleventh that is also
a value investor adds a correlated opinion, not information. What the
evidence says is missing is momentum — and that the two together beat
either alone, because they fail at different times.

* **Value: EBIT / EV.** One metric rather than a blend of four. A blend
  needs weights, and weights are where fitting enters. EBIT/EV is the
  best-documented single cheapness measure and the one Greenblatt built
  the Magic Formula on. Enterprise value, not market cap, so a company
  is priced including the debt a buyer would assume.

* **Quality: operating income / total assets.** Operating profitability
  in the Fama-French (2015) five-factor construction. Scaled by assets
  rather than equity so leverage cannot manufacture the score.

* **Momentum: the trailing twelve-month return, skipping the most
  recent month.** The oldest surviving anomaly in the cross-section
  (Jegadeesh & Titman, 1993), reproduced back to 1927 and across
  forty-odd markets. The skipped month is not optional: short-horizon
  returns reverse, so including it folds a signal of the opposite sign
  into the score.

Why ranks, not z-scores
~~~~~~~~~~~~~~~~~~~~~~~

Every one of these is a ratio, and ratios explode when the denominator
approaches zero. One company with an enterprise value near zero
produces an earnings yield of 4,000% and, under a z-score, drags the
mean and standard deviation far enough to flatten everyone else into
noise. A percentile rank is immune: it only asks who is ahead of whom.

Why a missing leg disqualifies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A name scored on two legs is not comparable to a name scored on three,
and averaging whatever happens to be present quietly rewards companies
with sparse filings. Imputing the median is worse: it invents a
measurement. So a name missing any leg is dropped, and the count of
drops is reported rather than swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.backtest.point_in_time import PointInTimeFinancials

#: The three legs, in the order they are reported.
LEGS: tuple[str, ...] = ("value", "quality", "momentum")


@dataclass(frozen=True)
class FactorScores:
    """One candidate's three raw measurements, before ranking."""

    ticker: str
    value: float
    quality: float
    momentum: float


def enterprise_value(
    fin: PointInTimeFinancials, market_cap: float
) -> float | None:
    """Market cap plus debt, less cash. None when it is not positive.

    A non-positive EV means net cash exceeds the equity value, which
    makes the earnings yield meaningless rather than infinite — the
    ratio changes sign and a screen sorting on it would put the most
    distressed names at the top.
    """
    if market_cap <= 0:
        return None
    debt = fin.total_debt if fin.total_debt is not None else fin.long_term_debt
    if debt is None or fin.cash_and_equivalents is None:
        return None
    ev = market_cap + debt - fin.cash_and_equivalents
    return ev if ev > 0 else None


def earnings_yield(
    fin: PointInTimeFinancials, market_cap: float
) -> float | None:
    """EBIT / EV as a percentage, or None when either side is unusable."""
    if fin.operating_income is None:
        return None
    ev = enterprise_value(fin, market_cap)
    if ev is None:
        return None
    return fin.operating_income / ev * 100.0


def operating_profitability(fin: PointInTimeFinancials) -> float | None:
    """Operating income / total assets as a percentage.

    Scaled by assets, not equity: a company can lift return-on-equity by
    borrowing, and this is meant to measure the business rather than the
    balance sheet's gearing.
    """
    if fin.operating_income is None or fin.total_assets is None:
        return None
    if fin.total_assets <= 0:
        return None
    return fin.operating_income / fin.total_assets * 100.0


def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """Map each ticker to its percentile, 0.0 worst to 1.0 best.

    Ties share the average of the positions they span, so a field of
    identical values scores every member 0.5 rather than handing an
    arbitrary winner the top slot on sort order alone.
    """
    if not values:
        return {}
    if len(values) == 1:
        return {next(iter(values)): 0.5}

    ordered = sorted(values.items(), key=lambda kv: kv[1])
    n = len(ordered)
    ranks: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        # Average position of the tied block, normalised to [0, 1].
        avg_pos = (i + j) / 2.0
        score = avg_pos / (n - 1)
        for k in range(i, j + 1):
            ranks[ordered[k][0]] = score
        i = j + 1
    return ranks


def composite_ranks(scores: list[FactorScores]) -> dict[str, float]:
    """Average the three percentile ranks, equally weighted.

    Equal weights are a choice about what is knowable, not a shortcut.
    Any other split is a free parameter, and the only data available to
    set it is the window the result will be judged on — which is how a
    backtest becomes a description of its own sample.
    """
    if not scores:
        return {}
    by_leg = {
        leg: percentile_ranks({s.ticker: getattr(s, leg) for s in scores})
        for leg in LEGS
    }
    return {
        s.ticker: sum(by_leg[leg][s.ticker] for leg in LEGS) / len(LEGS)
        for s in scores
    }


__all__ = [
    "LEGS",
    "FactorScores",
    "composite_ranks",
    "earnings_yield",
    "enterprise_value",
    "operating_profitability",
    "percentile_ranks",
]
