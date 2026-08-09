"""Reconstructing share counts in the price series' own units.

The problem
~~~~~~~~~~~

Market capitalisation is price times shares, and in this project the
two come from sources that count shares differently.

``prices.sqlite`` stores split-adjusted prices — verified directly:
NVDA closed at $120.99 on 2024-06-06 in the database, while the tape
that day said about $1,210. The 10-for-1 split of 2024-06-10 has been
pushed back through the earlier history. EDGAR, by contrast, reports
what the company actually filed: NVDA's 2020 10-K says roughly 617
million shares, because that is how many there were.

Multiply one by the other and the answer is wrong by exactly the
cumulative split factor between the date and today. NVDA at the end of
2020 comes out at $8bn instead of $323bn — forty times too small,
because two forward splits (4-for-1 in 2021, 10-for-1 in 2024) have
since divided its historical price by forty. Reverse splits push the
error the other way: GEVO's $150m becomes tens of billions.

That error is not noise. It is signed by what happened to the company
afterwards — winners split forward, failures split backward — so a
market-cap floor built on it systematically **excludes future winners
and admits future failures**. A screen meant to enforce investability
quietly becomes a bet on the future, and a losing one.

The fix
~~~~~~~

Restate the share count in the price series' units. A split shows up
in the reported share count as a discrete jump: 10-for-1 multiplies it
by ten between one filing and the next. Real dilution does not behave
that way — buybacks and issuance move a share count by low single
digits per quarter. So consecutive-filing ratios far from 1.0 are
splits, and everything else is ordinary corporate action.

Walking those jumps backwards gives a cumulative factor per date, and
``shares_reported x factor`` is the share count in today's terms —
the same terms the adjusted price is quoted in. Their product is the
market capitalisation that was true on the day.

Is that look-ahead?
~~~~~~~~~~~~~~~~~~~

No, and the distinction matters. The 2024 split is future information
relative to 2020, but it is being used only to undo an adjustment that
the price source already applied using that same future information.
The result is the market cap an investor could read off a screen in
2020. Nothing about a future *return* enters the calculation; a unit
conversion is restored, not a fact about what comes next.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from core.logger import get_logger

logger = get_logger("core.research.splits")

#: A consecutive-filing share-count ratio outside ``[1/RATIO, RATIO]``
#: is treated as a split rather than as issuance.
#:
#: The smallest split anyone actually declares is 5-for-4 (1.25x), and
#: the largest honest quarter-over-quarter dilution is far below that:
#: a company issuing 20% of itself in three months is doing something
#: that belongs in a filing, not in a share count. 1.4 sits between the
#: two with room on either side. Splits also cluster at clean multiples
#: — 2, 3, 4, 10 and their inverses — so the population being separated
#: is not close to the boundary.
SPLIT_RATIO_THRESHOLD = 1.4

#: Ratios are snapped to a clean fraction when they land near one, so a
#: share count that also moved a little for real reasons still yields
#: exactly 10.0 for a ten-for-one rather than 10.03.
SNAP_TOLERANCE = 0.08

#: Candidate split ratios to snap to, forward and reverse.
_CLEAN_RATIOS: tuple[float, ...] = (
    1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 15.0, 20.0, 30.0, 50.0,
)


@dataclass(frozen=True)
class SplitEvent:
    """One detected split, dated at the filing that first reported it."""

    observed_at: date
    ratio: float

    def __post_init__(self) -> None:
        if self.ratio <= 0:
            raise ValueError(f"split ratio must be positive; got {self.ratio}")


def _normalize(shares: pd.Series) -> pd.Series:
    """Sorted, positive, one entry per date, indexed by real ``date``.

    The share count arrives from a parquet column whose ``filed`` values
    are strings. ISO strings happen to sort like dates, so leaving them
    alone would work by accident and then fail the moment a caller
    passed real dates — comparing the two raises. Coercing once here
    means :class:`SplitEvent` really does hold what its annotation says.
    """
    clean = shares[shares > 0].dropna()
    if len(clean) == 0:
        return clean
    clean.index = pd.to_datetime(clean.index).date
    clean = clean[~pd.Index(clean.index).duplicated(keep="last")]
    return clean.sort_index()


def _snap(ratio: float) -> float:
    """Round a messy ratio to a clean split ratio when one is close."""
    for clean in _CLEAN_RATIOS:
        for candidate in (clean, 1.0 / clean):
            if abs(ratio - candidate) <= SNAP_TOLERANCE * candidate:
                return candidate
    return ratio


def detect_splits(shares: pd.Series) -> list[SplitEvent]:
    """Find splits in a reported share count indexed by filing date.

    ``shares`` must be sorted by date and hold the raw counts as filed.
    Duplicate or non-positive entries are dropped: a share count of
    zero is a parse failure, and dividing by it would manufacture an
    infinite split.
    """
    clean = _normalize(shares)
    if len(clean) < 2:
        return []

    ratios = clean / clean.shift(1)
    events: list[SplitEvent] = []
    for when, ratio in ratios.items():
        if not np.isfinite(ratio) or ratio <= 0:
            continue
        if ratio > SPLIT_RATIO_THRESHOLD or ratio < 1.0 / SPLIT_RATIO_THRESHOLD:
            events.append(SplitEvent(observed_at=when, ratio=_snap(float(ratio))))
    return events


def cumulative_factors(
    shares: pd.Series, splits: list[SplitEvent] | None = None
) -> pd.Series:
    """Factor per date that restates a share count in today's units.

    For a date ``t`` the factor is the product of every split ratio
    observed strictly after ``t``. Multiplying the reported count by it
    gives the count the adjusted price series is quoted against, so::

        market_cap(t) = adjusted_price(t) * shares(t) * factor(t)

    A ticker with no splits gets a factor of 1.0 everywhere, which is
    the identity — the common case costs nothing.
    """
    clean = _normalize(shares)
    if splits is None:
        splits = detect_splits(clean)
    index = pd.Index(clean.index)
    factors = pd.Series(1.0, index=clean.index, dtype="float64")
    if not splits:
        return factors

    for event in splits:
        # The jump is visible *at* observed_at, so every date strictly
        # before it is quoted in pre-split units and needs scaling.
        factors.loc[index < event.observed_at] *= event.ratio
    return factors


def adjusted_shares(shares: pd.Series) -> pd.Series:
    """Reported share count restated in the adjusted price's units."""
    clean = _normalize(shares)
    splits = detect_splits(clean)
    if splits:
        logger.debug(
            f"{len(splits)} split(s): "
            + ", ".join(f"{e.ratio:g}x @ {e.observed_at}" for e in splits)
        )
    return clean * cumulative_factors(clean, splits)


__all__ = [
    "SPLIT_RATIO_THRESHOLD",
    "SplitEvent",
    "adjusted_shares",
    "cumulative_factors",
    "detect_splits",
]
