"""Trailing twelve months, assembled from what filers actually report.

Why this exists
---------------

Every flow figure in this project came from a single annual fact. The
duration filter in :mod:`core.data.fundamentals_fetcher` keeps a 10-Q's
year-to-date number from masquerading as a year — which was a real bug —
but it pays for that by only ever seeing 10-K periods. A screen run in
August therefore reads a fiscal year that ended the previous December
and was filed in February, and :data:`MAX_FACT_AGE_DAYS` lets it keep
reading it for up to eighteen months.

Fifteen-month-old earnings against today's price is not a value screen.
It is the failure ``core/research/factors.py`` documents as having
produced two holdout results that looked plausible and meant nothing.

What it does
------------

Given a company's raw XBRL facts, assemble the twelve months ending at
the most recent period the company had **filed** on or before ``as_of``.
The algorithm is deliberately not "sum the last four quarters", because
a large minority of filers make that impossible:

* **Almost nobody files a Q4 10-Q.** The fourth quarter exists only as
  ``FY − 9M``, and only when both share a fiscal-year start date
  (``COUNCIL_DATA.md`` trap #5).
* **Many filers tag year-to-date rather than discrete quarters.** Their
  Q3 10-Q carries a nine-month period, not a three-month one, so there
  are no four quarters to add.
* **Fiscal years are 52 or 53 weeks.** 97-day quarters and 370-day years
  are real filings; bucketing on exactly 91 and 365 drops them.

So instead of assuming a shape, this builds a pool of every duration
period the filer reported, adds every period that can be *derived* by
subtracting one from another that shares its start, and then chains
non-overlapping consecutive periods backwards from the newest until
they span a year. Four quarters, nine months plus a derived quarter, a
bare fiscal year on its own — all fall out of the same search.

Point-in-time
-------------

Only facts with ``filed <= as_of`` are considered, never ``period_end``
(``COUNCIL_DATA.md`` trap #2: Apple's FY2025 ended 2025-09-27 and was
filed 2025-10-31, so keying on the period end grants 34 days of
hindsight). Where a period was restated, the version with the latest
``filed`` **that is still on or before as_of** wins — that is what a
reader actually knew that day.

Concept chains
--------------

There is no such thing as "the revenue tag" (trap #1), so callers pass
an ordered chain and resolution happens **per period**, not per company:
NVIDIA's ASC 606 tag covers only 2017-2022, and a per-company resolver
silently truncates its history there. Subtraction, however, never mixes
concepts — deriving Q4 from one company's ``Revenues`` minus its
``RevenueFromContractWithCustomerExcludingAssessedTax`` would subtract
two different definitions of revenue and report the difference as a
quarter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from core.data.edgar_facts import XbrlFact
from core.logger import get_logger

logger = get_logger("core.data.ttm")

#: Inclusive day-span that counts as "twelve months". Matches
#: ``ANNUAL_DURATION_DAYS`` in the fetcher: 52- and 53-week years run
#: 364 and 371 days, and period ends drift a few days either way.
TTM_SPAN_DAYS: tuple[int, int] = (330, 400)

#: How far apart two periods may be and still count as consecutive.
#: A clean hand-off is ``next.start == prev.end + 1 day``. Filers are
#: not uniform: some repeat the boundary date, some skip a weekend.
#: Four days absorbs that without letting a whole missing week pass as
#: contiguous.
GAP_TOLERANCE_DAYS: int = 4

#: Two periods "share a start" for subtraction purposes within this
#: many days. Fiscal-year starts wobble by a day or two across a filer's
#: own 10-K and 10-Q tagging.
START_TOLERANCE_DAYS: int = 4

#: Most segments a twelve-month window may be built from. Four quarters
#: is the honest maximum; six leaves room for a filer that splits a
#: 53-week year oddly. Beyond that the chain is almost certainly
#: stitching together periods that do not belong together.
MAX_SEGMENTS: int = 6

#: A derived segment shorter than this is discarded. Subtracting two
#: periods that differ by only a few days produces a stub with a
#: near-zero denominator, and such stubs are artefacts of boundary
#: drift, not real reporting periods.
MIN_SEGMENT_DAYS: int = 20


@dataclass(frozen=True)
class Segment:
    """One reporting period that can contribute to a twelve-month window."""

    start: date
    end: date
    value: float
    filed: date
    concept: str
    #: True when produced by subtracting one reported period from
    #: another. Kept because a derived Q4 is a weaker fact than a filed
    #: one and the journal should be able to say which it used.
    derived: bool = False

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class TtmResult:
    """Twelve months of a flow concept, with its provenance."""

    value: float
    period_start: date
    period_end: date
    #: The latest filing date among the components, which is the day
    #: the newest *version* of this window was published — not the day
    #: it first became knowable. Filers repeat prior periods as
    #: comparatives, so Apple's FY2025 figures carry the filing date of
    #: the following 10-Q that restated them into its own tables. Every
    #: component is still bounded by ``filed <= as_of``, so the window
    #: is what a reader knew that day; this field says how recently the
    #: filer last stood behind it.
    filed: date
    segments: tuple[Segment, ...]

    @property
    def days(self) -> int:
        return (self.period_end - self.period_start).days + 1

    @property
    def uses_derived(self) -> bool:
        return any(s.derived for s in self.segments)

    @property
    def concepts(self) -> tuple[str, ...]:
        seen: list[str] = []
        for s in self.segments:
            if s.concept not in seen:
                seen.append(s.concept)
        return tuple(seen)


def _known_periods(
    facts: Sequence[XbrlFact],
    *,
    as_of: date,
    concepts: Sequence[str],
    namespace: str,
    units: Sequence[str],
    forms: Sequence[str],
) -> list[Segment]:
    """Every duration period the filer had published by ``as_of``.

    One segment per (concept, start, end). Where the same period was
    filed more than once — a restatement, or the same figure repeated in
    a later filing — the latest version still on or before ``as_of``
    wins, because that is what a reader knew that day.
    """
    rank = {c: i for i, c in enumerate(concepts)}
    unit_set = set(units)
    form_set = set(forms)
    best: dict[tuple[str, date, date], XbrlFact] = {}

    for f in facts:
        if f.period_start is None:
            continue  # a balance-sheet instant, not a flow
        if f.concept not in rank or f.namespace != namespace:
            continue
        if f.unit not in unit_set or f.form not in form_set:
            continue
        if f.filed > as_of:
            continue  # the future is not knowable
        key = (f.concept, f.period_start, f.period_end)
        prior = best.get(key)
        if prior is None or f.filed > prior.filed:
            best[key] = f

    return [
        Segment(
            start=f.period_start,  # type: ignore[arg-type]  # filtered above
            end=f.period_end,
            value=f.value,
            filed=f.filed,
            concept=f.concept,
        )
        for f in best.values()
    ]


def _derive_by_subtraction(reported: list[Segment]) -> list[Segment]:
    """Periods a filer implies but never tags.

    The canonical case is the fourth quarter, which is ``FY − 9M`` and
    exists in no filing of its own. The same subtraction recovers a
    discrete Q3 from a nine-month and a six-month year-to-date, and so
    on down.

    Two rules keep this honest. Both periods must come from the **same
    concept**, or the result is the difference between two definitions
    rather than a quarter. And both must share a start date, or the
    subtraction is not a period at all.
    """
    by_concept: dict[str, list[Segment]] = {}
    for seg in reported:
        by_concept.setdefault(seg.concept, []).append(seg)

    derived: list[Segment] = []
    for concept, segs in by_concept.items():
        ordered = sorted(segs, key=lambda s: (s.start, s.end))
        for i, shorter in enumerate(ordered):
            for longer in ordered[i + 1 :]:
                if abs((longer.start - shorter.start).days) > START_TOLERANCE_DAYS:
                    continue
                if longer.end <= shorter.end:
                    continue
                start = shorter.end + timedelta(days=1)
                if (longer.end - start).days + 1 < MIN_SEGMENT_DAYS:
                    continue
                derived.append(
                    Segment(
                        start=start,
                        end=longer.end,
                        value=longer.value - shorter.value,
                        # Knowable only once BOTH halves were filed.
                        filed=max(longer.filed, shorter.filed),
                        concept=concept,
                        derived=True,
                    )
                )
    return derived


def _pick(candidates: list[Segment], rank: dict[str, int]) -> Segment:
    """Best segment for one period: preferred concept, then reported."""
    return min(
        candidates,
        key=lambda s: (rank.get(s.concept, len(rank)), s.derived, -s.filed.toordinal()),
    )


def _search(
    pool: list[Segment],
    cursor_end: date,
    accumulated: int,
    depth: int,
) -> list[Segment] | None:
    """Chain periods backwards from ``cursor_end`` until they span a year.

    Longest-first, so a fiscal year is preferred to four quarters that
    add to the same thing: fewer components means fewer boundary
    assumptions and fewer chances to stitch on a period that does not
    belong.
    """
    if depth > MAX_SEGMENTS:
        return None

    ending_here = [
        s
        for s in pool
        if abs((s.end - cursor_end).days) <= GAP_TOLERANCE_DAYS
        and s.days >= MIN_SEGMENT_DAYS
    ]
    for seg in sorted(ending_here, key=lambda s: -s.days):
        span = accumulated + seg.days
        if TTM_SPAN_DAYS[0] <= span <= TTM_SPAN_DAYS[1]:
            return [seg]
        if span >= TTM_SPAN_DAYS[1]:
            continue  # overshoots a year; a longer segment cannot help
        rest = _search(pool, seg.start - timedelta(days=1), span, depth + 1)
        if rest is not None:
            return [seg, *rest]
    return None


def trailing_twelve_months(
    facts: Sequence[XbrlFact],
    as_of: date,
    *,
    concepts: Sequence[str],
    namespace: str = "us-gaap",
    units: Sequence[str] = ("USD",),
    forms: Sequence[str] = ("10-K", "10-Q"),
) -> TtmResult | None:
    """Twelve months of a flow concept as of ``as_of``, or ``None``.

    Args:
        facts: The company's raw XBRL facts, as cached.
        as_of: The decision date. Nothing filed after it is used.
        concepts: Ordered chain, most preferred first. Resolved per
            period, not per company.
        namespace: XBRL namespace; ``us-gaap`` for everything here.
        units: Acceptable units. A foreign private issuer reporting in
            its home currency is rejected rather than silently mixed
            into a USD multiple.
        forms: Filing forms to read.

    Returns:
        A :class:`TtmResult`, or ``None`` when no twelve-month window
        can be assembled. ``None`` means "cannot be computed", which
        under the doctrine's screen is a failed gate — never a zero.
    """
    reported = _known_periods(
        facts,
        as_of=as_of,
        concepts=concepts,
        namespace=namespace,
        units=units,
        forms=forms,
    )
    if not reported:
        return None

    rank = {c: i for i, c in enumerate(concepts)}
    pool_all = reported + _derive_by_subtraction(reported)

    # One segment per (start, end): the preferred concept, and a
    # reported period ahead of a derived one covering the same span.
    grouped: dict[tuple[date, date], list[Segment]] = {}
    for seg in pool_all:
        grouped.setdefault((seg.start, seg.end), []).append(seg)
    pool = [_pick(cands, rank) for cands in grouped.values()]

    # Anchor on the newest period end that was actually filed by as_of,
    # then walk back. If no window ends there — a filer that publishes a
    # stub period, say — try the next end down rather than giving up,
    # because a screen that returns None for a company whose last two
    # quarters are perfectly readable is a bug wearing a null.
    for anchor in sorted({s.end for s in pool}, reverse=True):
        chain = _search(pool, anchor, 0, 1)
        if chain is None:
            continue
        segments = tuple(sorted(chain, key=lambda s: s.start))
        return TtmResult(
            value=sum(s.value for s in segments),
            period_start=segments[0].start,
            period_end=segments[-1].end,
            filed=max(s.filed for s in segments),
            segments=segments,
        )

    logger.debug(
        f"no twelve-month window for {concepts[0]} at {as_of} "
        f"from {len(pool)} candidate periods"
    )
    return None
