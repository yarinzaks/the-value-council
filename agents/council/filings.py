"""Gate D: the filings that disqualify a company outright.

``COUNCIL_SELECTION.md`` section 2, Gate D. Four checks, any one of
which ends it:

* an 8-K item 4.02 — non-reliance on previously issued financials —
  within 24 months
* going-concern language in the latest 10-K
* a material weakness disclosed in the latest 10-K
* a late filing, NT 10-K or NT 10-Q, within 12 months

Two sources, and why the split matters
--------------------------------------

The audit-opinion phrases live in the *text* of a 10-K, which is not in
the XBRL API. Fetching two thousand annual reports to grep them is not a
screen, it is a crawl. So they come from EDGAR full-text search instead,
which inverts the problem: one query per phrase returns every company
that used it, and 714 companies used the going-concern phrasing in the
last year — a set small enough to hold in memory and test membership
against.

The 8-K item codes and the NT forms have no equivalent inversion; item
codes are not a full-text facet. Those need one submissions fetch per
company, so :func:`gate_d_flags` is written to be run **last, on the
names that already cleared gates A to C** — tens of companies rather
than thousands. Running an expensive gate on survivors is not an
optimisation, it is the only order in which this gate is affordable.

Missing is not clean
--------------------

Every check answers ``True``, ``False`` or ``None``, and ``None`` fails
Gate D. A phrase index that could not be fetched must not read as "no
company has going-concern doubt" — that is the single substitution that
would turn this gate into decoration.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final

from agents.council.events import USER_AGENT, cik_for, fetch_submissions
from agents.council.screen import FilingFlags
from core.logger import get_logger

logger = get_logger("agents.council.filings")

#: ``(phrase, forms, start, end, offset) -> the endpoint's JSON``.
PageFetcher = Callable[[str, str, date, date, int], dict[str, Any]]

#: ``(cik) -> the submissions JSON, or None``.
SubmissionsFetcher = Callable[[int], dict[str, Any] | None]

FULL_TEXT_URL: Final[str] = "https://efts.sec.gov/LATEST/search-index"

REQUEST_TIMEOUT_SECONDS: Final[int] = 30

#: EDGAR full-text search pages its results. This is the stride; the
#: endpoint caps a single response well below it and returns what it
#: has, so the loop stops when a page comes back short.
PAGE_SIZE: Final[int] = 10

#: The endpoint refuses to page past this many results. A phrase that
#: matches more than this is too common to be a disqualifier anyway,
#: and the code says so rather than silently truncating.
MAX_RESULTS: Final[int] = 10_000

#: Lookback windows, from Gate D.
RESTATEMENT_LOOKBACK_DAYS: Final[int] = 730
LATE_FILING_LOOKBACK_DAYS: Final[int] = 365
#: Audit-opinion phrases are read from the *latest* 10-K, so the window
#: matches U3's staleness bound rather than a calendar year.
OPINION_LOOKBACK_DAYS: Final[int] = 400

#: The standard wording. ASC 205-40 fixed the phrasing closely enough
#: that an exact-phrase search is reliable, which is why these are
#: searched verbatim rather than as loose keywords -- "going concern"
#: alone also matches every filing that says there is no such doubt.
GOING_CONCERN_PHRASE: Final[str] = (
    "substantial doubt about its ability to continue as a going concern"
)
GOING_CONCERN_ALTERNATE: Final[str] = (
    "substantial doubt about our ability to continue as a going concern"
)
MATERIAL_WEAKNESS_PHRASE: Final[str] = (
    "material weakness in our internal control over financial reporting"
)

#: The forms that mean a filer missed its deadline.
LATE_FORMS: Final[frozenset[str]] = frozenset({"NT 10-K", "NT 10-Q"})

#: 8-K item 4.02: non-reliance on previously issued financial
#: statements. The Accountant's hard veto.
RESTATEMENT_ITEM: Final[str] = "4.02"


class FullTextUnavailableError(RuntimeError):
    """The phrase index could not be built, so Gate D cannot be run."""


def _fetch_page(
    phrase: str, forms: str, start: date, end: date, offset: int
) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "q": f'"{phrase}"',
            "forms": forms,
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
            "from": offset,
        }
    )
    request = urllib.request.Request(
        f"{FULL_TEXT_URL}?{query}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(
        request, timeout=REQUEST_TIMEOUT_SECONDS
    ) as response:
        return dict(json.loads(response.read()))


def ciks_using_phrase(
    phrase: str,
    *,
    start: date,
    end: date,
    forms: str = "10-K",
    fetch: PageFetcher = _fetch_page,
) -> set[str]:
    """Every CIK whose filing of ``forms`` used ``phrase`` in the window.

    Returns:
        Zero-padded ten-digit CIK strings, as EDGAR spells them.

    Raises:
        FullTextUnavailableError: If the index could not be read at all,
            or matched more results than the endpoint will page through.
            Both raise rather than returning a short set, because a
            partial answer here reads as "these companies are clean".
    """
    found: set[str] = set()
    offset = 0
    total: int | None = None

    while True:
        try:
            payload = fetch(phrase, forms, start, end, offset)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise FullTextUnavailableError(
                f"full-text search failed for {phrase!r}: {exc}"
            ) from exc

        hits = payload.get("hits", {})
        if total is None:
            total = int(hits.get("total", {}).get("value", 0))
            if total > MAX_RESULTS:
                raise FullTextUnavailableError(
                    f"{phrase!r} matched {total} filings, past the "
                    f"{MAX_RESULTS} the endpoint will page through — "
                    "too common to be a disqualifier"
                )

        rows: Sequence[dict[str, Any]] = hits.get("hits", [])
        if not rows:
            break
        for row in rows:
            for cik in row.get("_source", {}).get("ciks", []):
                found.add(str(cik).zfill(10))
        offset += len(rows)
        if offset >= total:
            break

    logger.info(
        f"full-text: {len(found)} CIKs used {phrase[:40]!r}... "
        f"in {forms} between {start} and {end}"
    )
    return found


@dataclass
class OpinionIndex:
    """The two audit-opinion phrase sets, fetched once per run.

    Built by :meth:`build`, which is the only expensive part of Gate D
    that does not scale with the number of candidates.
    """

    going_concern: set[str]
    material_weakness: set[str]

    @classmethod
    def build(
        cls,
        as_of: date,
        *,
        lookback_days: int = OPINION_LOOKBACK_DAYS,
        fetch: PageFetcher = _fetch_page,
    ) -> OpinionIndex:
        start = as_of - timedelta(days=lookback_days)
        concern = ciks_using_phrase(
            GOING_CONCERN_PHRASE, start=start, end=as_of, fetch=fetch
        ) | ciks_using_phrase(
            GOING_CONCERN_ALTERNATE, start=start, end=as_of, fetch=fetch
        )
        weakness = ciks_using_phrase(
            MATERIAL_WEAKNESS_PHRASE, start=start, end=as_of, fetch=fetch
        )
        return cls(going_concern=concern, material_weakness=weakness)


def _submission_flags(doc: dict[str, Any], as_of: date) -> tuple[bool, bool]:
    """``(restatement_within_24m, late_filing_within_12m)``."""
    recent = doc.get("filings", {}).get("recent", {})
    forms: Sequence[str] = recent.get("form", [])
    dates: Sequence[str] = recent.get("filingDate", [])
    items: Sequence[str] = recent.get("items", [""] * len(forms))

    restatement_since = as_of - timedelta(days=RESTATEMENT_LOOKBACK_DAYS)
    late_since = as_of - timedelta(days=LATE_FILING_LOOKBACK_DAYS)
    restated = False
    late = False

    for i, form in enumerate(forms):
        try:
            filed = date.fromisoformat(dates[i])
        except (IndexError, ValueError):
            continue
        if filed > as_of:
            continue  # nothing filed after the decision date is knowable
        if form in LATE_FORMS and filed >= late_since:
            late = True
        if form.startswith("8-K") and filed >= restatement_since:
            raw = str(items[i]) if i < len(items) else ""
            if RESTATEMENT_ITEM in {c.strip() for c in raw.split(",")}:
                restated = True
    return restated, late


def gate_d_flags(
    tickers: Iterable[str],
    as_of: date,
    *,
    opinions: OpinionIndex | None = None,
    fetch: SubmissionsFetcher = fetch_submissions,
    resolve_cik: Callable[[str], int | None] = cik_for,
) -> dict[str, FilingFlags]:
    """Gate D's four inputs for each candidate.

    Run this **after** gates A to C, on the names that survived them.
    Each ticker costs one submissions fetch, so the cost is set by how
    many candidates there are rather than by the size of the universe.

    Args:
        tickers: The survivors of gates A-C.
        as_of: The decision date. Nothing filed after it is read.
        opinions: A prebuilt phrase index. Built here if omitted, which
            costs the same regardless of how many tickers are passed.
        fetch: ``(cik) -> submissions dict | None``. Injected for tests.
        resolve_cik: ``(ticker) -> int | None``. Injected separately
            because it reads a local database the test suite cannot see.

    Returns:
        Ticker -> :class:`FilingFlags`. A company whose CIK or
        submissions could not be read gets ``None`` on the two
        submission-derived checks, which fails Gate D — an unreadable
        filer is not a clean one.
    """
    index = opinions if opinions is not None else OpinionIndex.build(as_of)

    out: dict[str, FilingFlags] = {}
    for ticker in tickers:
        cik = resolve_cik(ticker)
        if cik is None:
            logger.info(f"{ticker}: no CIK — Gate D cannot be evaluated")
            out[ticker] = FilingFlags(ticker=ticker)
            continue

        padded = f"{cik:010d}"
        doc = fetch(cik)
        if doc is None:
            out[ticker] = FilingFlags(
                ticker=ticker,
                going_concern=padded in index.going_concern,
                material_weakness=padded in index.material_weakness,
            )
            continue

        restated, late = _submission_flags(doc, as_of)
        out[ticker] = FilingFlags(
            ticker=ticker,
            restatement_8k_402=restated,
            going_concern=padded in index.going_concern,
            material_weakness=padded in index.material_weakness,
            late_filing=late,
        )
    return out


__all__ = [
    "GOING_CONCERN_PHRASE",
    "MATERIAL_WEAKNESS_PHRASE",
    "FullTextUnavailableError",
    "OpinionIndex",
    "ciks_using_phrase",
    "gate_d_flags",
]
