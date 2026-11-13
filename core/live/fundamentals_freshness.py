"""How old are the filings the live run is reading? (audit FUND-2)

Every live decision from about May 2026 to the audit read fundamentals
frozen at 2026-04-28. On 2026-11-13 the newest filing any of 3,726
universe members was read from was 199 days old; UVV was bought on its
10-K for the year to March 2025 while a 10-K a year newer sat in the
parquet the same run had extracted. Nothing in the log, the books or the
dashboard said so.

This measures what the decisions saw — the ``source_filing`` of each
:class:`PointInTimeFinancials` the scan loaded — rather than the age of
the parquet on disk. The two came apart in exactly that incident: the
release asset was current and the filing list read from it was not, so
a check on the files would have passed.

It reports and never halts. The cache was stale on the day this shipped,
and a hard stop would have frozen every book that day; whether a stale
corpus should ever stop trading is an owner decision, not this module's.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials

#: A corpus whose newest filing is older than this is stale. Each listed
#: company files a 10-Q within 40-45 days of a quarter end and quarters
#: are 91 days apart, so across thousands of filers the newest filing in
#: a current corpus is days old. 120 days means a whole reporting season
#: has passed without one landing.
MAX_FUNDAMENTALS_AGE_DAYS: int = 120


@dataclass(frozen=True)
class FundamentalsFreshness:
    """The newest filing the run read, and whether that is too old."""

    #: Filing date of the newest ``source_filing`` across the universe;
    #: None when the run read no fundamentals at all.
    newest_filed: date | None
    #: Days from ``newest_filed`` to the run's ``as_of``; None with it.
    age_days: int | None
    max_age_days: int

    @property
    def stale(self) -> bool:
        """True past the limit, and when nothing was read at all.

        Reading nothing is the worst case, not an absence of evidence.
        """
        return self.age_days is None or self.age_days > self.max_age_days


def assess_freshness(
    fundamentals: Mapping[str, PointInTimeFinancials | None],
    as_of: date,
    *,
    max_age_days: int = MAX_FUNDAMENTALS_AGE_DAYS,
) -> FundamentalsFreshness:
    """Measure the newest filing in ``fundamentals`` against ``as_of``."""
    filed = [
        fin.source_filing.filing_date
        for fin in fundamentals.values()
        if fin is not None
    ]
    if not filed:
        return FundamentalsFreshness(
            newest_filed=None, age_days=None, max_age_days=max_age_days
        )
    newest = max(filed)
    return FundamentalsFreshness(
        newest_filed=newest,
        age_days=(as_of - newest).days,
        max_age_days=max_age_days,
    )


__all__ = [
    "MAX_FUNDAMENTALS_AGE_DAYS",
    "FundamentalsFreshness",
    "assess_freshness",
]
