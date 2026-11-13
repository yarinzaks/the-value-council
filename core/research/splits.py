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

Not every jump is a split (audit FUND-8)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A merger paid in stock doubles a share count as cleanly as a
two-for-one: RYN's cover count went from 153.9M to 302.3M at its
2026-02-23 10-K, and the price did not halve. The price series cannot
tell the two apart here — it is already split-adjusted, so a real split
leaves no step in it either (NVDA 2024-06-07 → 06-10: 120.89 → 121.79).
The filer can: a split is restated retroactively into every comparative
the next filing reprints, issuance is not. :func:`split_adjusted_share_count`
reads a company's facts, drops counts too small to be real, carries a
dead cover series on with the weighted average, and keeps only the
jumps the filer's own reprints do not contradict.

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
from datetime import date, timedelta

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

#: Share-count concepts read point by point, best first. The ``dei``
#: cover-page fact is dated at the filing rather than at a period end,
#: so it is the freshest count a filing contains; the balance-sheet
#: count serves where a filing has no cover fact.
COVER_CONCEPTS: tuple[str, ...] = (
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding",
)

#: The fallback when the cover series has gone dark (audit FUND-8):
#: AOS's cover facts stop at a 2015 10-Q while its weighted averages run
#: to the latest 10-K, so the cover series alone served 75.7M shares in
#: 2026 against roughly 141M and never saw the 2016 two-for-one.
WEIGHTED_AVERAGE_CONCEPT = "WeightedAverageNumberOfSharesOutstandingBasic"

#: Concepts a filer reprints for earlier periods, and therefore restates
#: when a split happens (ASC 260 makes the restatement retroactive).
RESTATEMENT_CONCEPTS: tuple[str, ...] = (
    WEIGHTED_AVERAGE_CONCEPT,
    "CommonStockSharesOutstanding",
)

#: A filed share count below this is a tagging error, not a company.
#: FOXA's only cover fact is a 2019 10-Q reporting **one** share; read as
#: a count it made a $40 company, and between two real counts it reads
#: as a million-for-one split each way. No listed US company has fewer
#: than a thousand shares outstanding.
MIN_SHARE_COUNT = 1_000.0

#: The cover series counts as stale once the weighted averages run on
#: more than this many days past its last filing. A filer reports every
#: quarter; a year and a month without a cover count while it kept
#: filing financial statements means the series stopped, not that the
#: count stood still.
STALE_SERIES_DAYS = 400

#: How long after a jump the filer's restated comparatives are looked
#: for. The filing that shows the jump normally restates them itself;
#: a year also covers the next 10-K's reprinted prior years.
RESTATEMENT_WINDOW_DAYS = 365


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


# ---------------------------------------------------------------------------
# Reading a company's own facts (audit FUND-8)
# ---------------------------------------------------------------------------


def _as_dates(column: pd.Series) -> pd.Series:
    """A date column as ``datetime.date``, whatever it arrived as.

    ``EdgarCache.load_dataframe`` parses dates; a raw parquet read
    leaves them as ISO strings. Unparseable values become ``NaT`` and
    the rows holding them are dropped by the callers.
    """
    return pd.to_datetime(column, errors="coerce").dt.date


def _usable_rows(facts: pd.DataFrame, concepts: tuple[str, ...]) -> pd.DataFrame:
    """Rows of ``concepts`` with real dates and a count above the floor."""
    needed = {"concept", "value", "filed", "period_end"}
    if facts is None or len(facts) == 0 or not needed.issubset(facts.columns):
        return pd.DataFrame(columns=["concept", "value", "filed", "start", "end"])
    rows = facts[facts["concept"].isin(concepts)]
    start = (
        _as_dates(rows["period_start"])
        if "period_start" in rows.columns
        else pd.Series(pd.NaT, index=rows.index)
    )
    out = pd.DataFrame(
        {
            "concept": rows["concept"],
            "value": pd.to_numeric(rows["value"], errors="coerce"),
            "filed": _as_dates(rows["filed"]),
            "start": start,
            "end": _as_dates(rows["period_end"]),
        }
    )
    out = out.dropna(subset=["value", "filed", "end"])
    return out[out["value"] >= MIN_SHARE_COUNT]


def _cover_series(facts: pd.DataFrame) -> pd.Series:
    """One cover-page or balance-sheet count per filing date."""
    rows = _usable_rows(facts, COVER_CONCEPTS)
    if len(rows) == 0:
        return pd.Series(dtype="float64")
    # One count per filing date, and it has to be *that filing's* count:
    # a 10-K reprints the balance-sheet concept for every comparative
    # year, so the cover fact wins and otherwise the newest period_end.
    preference = {name: i for i, name in enumerate(COVER_CONCEPTS)}
    ordered = rows.assign(_rank=rows["concept"].map(preference)).sort_values(
        ["filed", "_rank", "end"], ascending=[True, False, True]
    )
    return ordered.groupby("filed")["value"].last().astype("float64")


def _weighted_average_series(facts: pd.DataFrame) -> pd.Series:
    """Each filing's own current-period basic weighted average.

    Per filing, the newest period_end and, among durations ending there,
    the shortest — the quarter rather than the year to date, which is the
    nearer thing to a count on the day. A filing whose newest period is
    no newer than one already taken (an 8-K recasting old years) adds
    nothing current and is skipped.
    """
    rows = _usable_rows(facts, (WEIGHTED_AVERAGE_CONCEPT,))
    if len(rows) == 0:
        return pd.Series(dtype="float64")
    rows = rows.assign(_start=rows["start"].fillna(date.min))
    latest = (
        rows.sort_values(["filed", "end", "_start"])
        .groupby("filed")
        .last()
    )
    values: dict[date, float] = {}
    newest_end: date | None = None
    for filed, row in latest.iterrows():
        if newest_end is not None and row["end"] <= newest_end:
            continue
        newest_end = row["end"]
        values[filed] = float(row["value"])
    return pd.Series(values, dtype="float64")


def share_count_by_filing(facts: pd.DataFrame) -> pd.Series:
    """The reported share count by filing date, as filed (audit FUND-8).

    The cover series (:data:`COVER_CONCEPTS`), with counts below
    :data:`MIN_SHARE_COUNT` ignored. When it is empty, or the weighted
    averages run on more than :data:`STALE_SERIES_DAYS` past its last
    filing, the weighted averages filed after that point carry it on.
    Their level differs from a cover count by days of buybacks, far
    inside the split threshold, so the junction is not read as a split.
    """
    return _joined(_cover_series(facts), _weighted_average_series(facts))


def _joined(cover: pd.Series, weighted: pd.Series) -> pd.Series:
    """:func:`share_count_by_filing` from its two already-read series."""
    if weighted.empty:
        return cover
    if cover.empty:
        return weighted
    last_cover = max(cover.index)
    last_weighted = max(weighted.index)
    if (last_weighted - last_cover).days <= STALE_SERIES_DAYS:
        return cover
    tail = weighted[pd.Index(weighted.index) > last_cover]
    return pd.concat([cover, tail]).astype("float64")


def _restatement_ratio(facts: pd.DataFrame, event: SplitEvent) -> float | None:
    """How the filer restated its earlier periods once ``event`` appeared.

    For every (concept, period) the filer had reported before
    ``event.observed_at`` and reprints afterwards, the reprinted value
    over the latest earlier one; the median of those ratios. ``None``
    when nothing was reprinted, which is not evidence either way.

    The witness is the filing that shows the jump, when it reprints
    anything: under ASC 260 that filing's comparatives are already
    restated. Only when it reprints nothing are later filings within
    :data:`RESTATEMENT_WINDOW_DAYS` consulted, and then each period's
    newest reprint — an 8-K recasting old years in their original units
    (CF, December 2015) must not outvote the 10-Q that restated them.
    """
    rows = _usable_rows(facts, RESTATEMENT_CONCEPTS)
    if len(rows) == 0:
        return None
    rows = rows.assign(_start=rows["start"].fillna(date.min))
    key = ["concept", "_start", "end"]
    before = (
        rows[rows["filed"] < event.observed_at]
        .sort_values("filed")
        .groupby(key)["value"]
        .last()
        .rename("before")
    )
    if before.empty:
        return None
    horizon = event.observed_at + timedelta(days=RESTATEMENT_WINDOW_DAYS)
    same_filing = rows[rows["filed"] == event.observed_at]
    later = rows[(rows["filed"] > event.observed_at) & (rows["filed"] < horizon)]
    for witness in (same_filing, later):
        after = (
            witness.sort_values("filed").groupby(key)["value"].last().rename("after")
        )
        both = pd.concat([before, after], axis=1).dropna()
        if not both.empty:
            return float((both["after"] / both["before"]).median())
    return None


def _contradicted(facts: pd.DataFrame, event: SplitEvent) -> bool:
    """True when the filer's own comparatives say no split happened.

    A split restates every earlier per-share and share-count figure the
    filer reprints; issuance does not. RYN's 2026-02-23 10-K doubled its
    cover count (a merger paid in stock) and reprinted its 2023 and 2024
    weighted averages unchanged — ratio 1.0, so not a split. NVDA's
    10-for-1, AAPL's 4-for-1 and AOS's 2-for-1 are each restated by
    their exact ratio. Reprints that moved by less than the split
    threshold either way are the contradiction; anything else, or no
    reprints at all, leaves the jump standing.
    """
    ratio = _restatement_ratio(facts, event)
    if ratio is None or ratio <= 0:
        return False
    return 1.0 / SPLIT_RATIO_THRESHOLD <= ratio <= SPLIT_RATIO_THRESHOLD


def _reverted(events: list[SplitEvent], i: int) -> bool:
    """True when a neighbouring jump undoes ``events[i]``.

    A jump out and a jump back whose product is inside the split
    threshold bracket a run of rows filed in the wrong unit — RMD's
    cover count tagged in thousands, 145,681 for 145.7M, for two
    filings. Neither jump is a split, but keeping the pair rescales the
    run into its neighbours' units and leaves every other date alone,
    which dropping both would not.
    """
    ratio = events[i].ratio
    for j in (i - 1, i + 1):
        if 0 <= j < len(events):
            product = ratio * events[j].ratio
            if 1.0 / SPLIT_RATIO_THRESHOLD <= product <= SPLIT_RATIO_THRESHOLD:
                return True
    return False


def _kept_splits(
    facts: pd.DataFrame, shares: pd.Series
) -> tuple[list[SplitEvent], list[SplitEvent]]:
    """``(kept, standing)`` jumps in ``shares``.

    ``kept`` are the jumps the filer's restated comparatives allow, plus
    the pairs that bracket a run of rows filed in the wrong unit
    (:func:`_reverted`). ``standing`` are the kept jumps that are splits
    on their own evidence. The bracketing pairs rescale the cover
    series' own mis-unit rows and nothing else, so a count from another
    concept is restated by ``standing`` only.
    """
    candidates = detect_splits(shares)
    kept: list[SplitEvent] = []
    standing: list[SplitEvent] = []
    for i, event in enumerate(candidates):
        if not _contradicted(facts, event):
            kept.append(event)
            standing.append(event)
            continue
        if _reverted(candidates, i):
            kept.append(event)
            continue
        logger.debug(
            f"{event.ratio:g}x @ {event.observed_at} rejected: the filer's "
            f"reprinted comparatives did not move"
        )
    return kept, standing


def split_adjusted_counts(facts: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Share count and basic weighted average by filing date, both restated.

    The first series is :func:`split_adjusted_share_count`. The second
    is each filing's own current-period basic weighted average restated
    by the splits that stand on their own evidence, so the two are in
    the same units and a caller can take whichever was filed later. The
    weighted average is the one count a multi-class filer tags for every
    class together (review of audit FUND-5: MIR's balance-sheet count is
    one class, 18.75M of about 230M, and its 10-K tags no cover count at
    all).

    The weighted average is not restated by a pair of jumps bracketing
    a run of cover rows in the wrong unit: that pair corrects those rows
    only. VRNO's 2024 10-K reprinted a 100,000-share count from 2020;
    the pair around it is right for that row and would multiply the
    weighted averages filed in between by 3,632. Both series are empty
    when no usable count is on file.
    """
    raw_weighted = _weighted_average_series(facts)
    shares = _normalize(_joined(_cover_series(facts), raw_weighted))
    if shares.empty:
        return shares, pd.Series(dtype="float64")
    kept, standing = _kept_splits(facts, shares)
    weighted = _normalize(raw_weighted)
    return (
        shares * cumulative_factors(shares, kept),
        weighted * cumulative_factors(weighted, standing),
    )


def split_adjusted_share_count(facts: pd.DataFrame) -> pd.Series:
    """A ticker's share count by filing date, in the adjusted price's units.

    :func:`share_count_by_filing`, then :func:`detect_splits`, keeping
    only the jumps the filer's restated comparatives do not contradict,
    then :func:`cumulative_factors`. Empty when no usable count is on
    file, which a caller must read as "cannot tell".
    """
    shares = _normalize(share_count_by_filing(facts))
    if shares.empty:
        return shares
    kept, _standing = _kept_splits(facts, shares)
    return shares * cumulative_factors(shares, kept)


__all__ = [
    "COVER_CONCEPTS",
    "MIN_SHARE_COUNT",
    "RESTATEMENT_CONCEPTS",
    "SPLIT_RATIO_THRESHOLD",
    "STALE_SERIES_DAYS",
    "WEIGHTED_AVERAGE_CONCEPT",
    "SplitEvent",
    "adjusted_shares",
    "cumulative_factors",
    "detect_splits",
    "share_count_by_filing",
    "split_adjusted_counts",
    "split_adjusted_share_count",
]
