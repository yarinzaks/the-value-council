"""What just happened to something we own.

Why this exists, concretely
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Thermon Group was acquired for cash on 2026-06-01. Its ticker stopped
producing bars that day and the position sat in the book at a dead price
for seventy days, counted in NAV at a number that no longer referred to
anything. It was found by hand. ASGN had renamed to EFOR and sat stale
for fifty-three days for the same reason.

Both announced themselves in public filings on the day they happened.
Nothing was watching.

The doctrine already asks for this — the Accountant vetoes on an 8-K
item 4.02, the Close run walks the hunting grounds — but the mechanism
was never built. This is it, and it covers both jobs from one fetch.

Why the SEC's own feed rather than a news API
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

8-K item codes are a structured event feed, published by the company,
free, with no key. A headline says a company "explores strategic
alternatives"; item 2.01 says the acquisition closed. Only one of those
is a fact, and it is the free one.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Final

from core.logger import get_logger
from core.paths import DATA_ROOT, edgar_filings_db

logger = get_logger("agents.council.events")

SUBMISSIONS_URL: Final[str] = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

#: The SEC requires a User-Agent naming a contact, and returns 403
#: without one. This mirrors what core.config already sends.
USER_AGENT: Final[str] = "The-Value-Council research@example.com"

REQUEST_TIMEOUT_SECONDS: Final[int] = 30

#: How far back a run looks. Wide enough to survive a long weekend, a
#: holiday, and a failed run or two.
DEFAULT_LOOKBACK_DAYS: Final[int] = 10


class Severity(StrEnum):
    #: Stop. The Accountant's veto, or the security is ceasing to exist.
    CRITICAL = "critical"
    #: Read it before doing anything else with this position.
    INVESTIGATE = "investigate"
    #: Worth knowing, not worth acting on alone.
    NOTE = "note"


#: 8-K item code -> (severity, what it means). Only codes the doctrine
#: names, plus the two that would have caught THR.
ITEM_MEANINGS: Final[dict[str, tuple[Severity, str]]] = {
    "4.02": (
        Severity.CRITICAL,
        "non-reliance on previously issued financials — Accountant veto",
    ),
    "1.03": (Severity.CRITICAL, "bankruptcy or receivership"),
    "2.01": (
        Severity.CRITICAL,
        "completion of an acquisition or disposition — the security may be "
        "about to stop trading",
    ),
    "3.01": (
        Severity.CRITICAL,
        "delisting notice or failure to satisfy a listing rule",
    ),
    "4.01": (Severity.INVESTIGATE, "auditor change"),
    "2.06": (Severity.INVESTIGATE, "material impairment"),
    "1.01": (Severity.INVESTIGATE, "entry into a material agreement"),
    "5.02": (Severity.NOTE, "officer or director departure"),
    "2.02": (Severity.NOTE, "results of operations — PEAD window opens"),
}

#: Forms that mean the listing itself is ending. Form 25 is the exchange
#: striking the security; Form 15 is the issuer deregistering. Either one
#: means the price series is about to stop, which is precisely the state
#: that left THR frozen for seventy days.
TERMINAL_FORMS: Final[dict[str, str]] = {
    "25": "exchange filed to delist the security",
    "25-NSE": "exchange filed to delist the security",
    "15-12B": "issuer deregistering — reporting obligations ending",
    "15-12G": "issuer deregistering — reporting obligations ending",
}


@dataclass(frozen=True)
class Event:
    """One filing worth telling somebody about."""

    ticker: str
    filed: date
    form: str
    severity: Severity
    code: str
    meaning: str
    accession: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "filed": self.filed.isoformat(),
            "form": self.form,
            "severity": str(self.severity),
            "code": self.code,
            "meaning": self.meaning,
            "accession": self.accession,
        }


#: The SEC's own ticker -> CIK map. One request, no key, ~800 KB.
TICKER_MAP_URL: Final[str] = "https://www.sec.gov/files/company_tickers.json"

#: Cached locally because it changes rarely and every run needs it.
TICKER_MAP_CACHE: Final[Path] = DATA_ROOT / "cache" / "company_tickers.json"

#: How long a cached map is trusted before it is refetched.
TICKER_MAP_MAX_AGE_DAYS: Final[int] = 7


def _load_ticker_map() -> dict[str, int]:
    """``{TICKER: cik}``, from cache when fresh, else from the SEC.

    The local filings cache cannot do this job: 245,417 of its 245,934
    rows carry no CIK at all, so a lookup there answers "unknown" for
    99.8% of the market. The SEC's map answers for 10,391 issuers.

    Its documented limitation is that it lists *current* registrants, so
    a ticker that has already been delisted has dropped out of it. That
    is acceptable here and close to irrelevant: this watch exists to
    catch the filing on the day it is made, while the company is still
    listed. By the time a symbol leaves the map the event it was
    watching for has already happened.
    """
    cached = TICKER_MAP_CACHE
    if cached.exists():
        age = date.today() - date.fromtimestamp(cached.stat().st_mtime)
        if age.days <= TICKER_MAP_MAX_AGE_DAYS:
            try:
                raw = json.loads(cached.read_text())
                return {
                    str(v["ticker"]).upper(): int(v["cik_str"])
                    for v in raw.values()
                }
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logger.warning(f"cached ticker map unreadable — {exc}")

    request = urllib.request.Request(
        TICKER_MAP_URL, headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        # Chunked transfers of this file are flaky enough that a partial
        # read is a normal outcome, not an exception worth propagating.
        logger.warning(f"ticker map fetch failed — {exc}")
        return {}

    try:
        raw = json.loads(body)
        mapping = {
            str(v["ticker"]).upper(): int(v["cik_str"]) for v in raw.values()
        }
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning(f"ticker map unparseable — {exc}")
        return {}

    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(body)
    except OSError as exc:
        logger.warning(f"could not cache the ticker map — {exc}")
    return mapping


@lru_cache(maxsize=1)
def _ticker_map() -> dict[str, int]:
    return _load_ticker_map()


def cik_for(ticker: str) -> int | None:
    """CIK for a ticker. Everything joins on CIK; nothing joins on ticker.

    The SEC's map first, the local filings cache as a fallback for the
    handful of rows that do carry one.
    """
    found = _ticker_map().get(ticker.upper())
    if found is not None:
        return found

    try:
        with sqlite3.connect(edgar_filings_db()) as conn:
            row = conn.execute(
                "SELECT cik FROM filings "
                "WHERE ticker = ? AND cik IS NOT NULL AND cik != '' "
                "LIMIT 1",
                (ticker.upper(),),
            ).fetchone()
    except sqlite3.Error as exc:
        logger.warning(f"{ticker}: cik lookup failed — {exc}")
        return None
    if row is None or row[0] is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        logger.warning(f"{ticker}: cik {row[0]!r} is not an integer")
        return None


def fetch_submissions(cik: int) -> dict | None:
    """The SEC's submissions document for one filer."""
    request = urllib.request.Request(
        SUBMISSIONS_URL.format(cik=cik), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.warning(f"CIK {cik}: submissions fetch failed — {exc}")
        return None


def _events_from_submissions(
    ticker: str, doc: dict, *, since: date
) -> list[Event]:
    recent = doc.get("filings", {}).get("recent", {})
    forms: Sequence[str] = recent.get("form", [])
    dates: Sequence[str] = recent.get("filingDate", [])
    items: Sequence[str] = recent.get("items", [""] * len(forms))
    accessions: Sequence[str] = recent.get("accessionNumber", [""] * len(forms))

    out: list[Event] = []
    for i, form in enumerate(forms):
        try:
            filed = date.fromisoformat(dates[i])
        except (IndexError, ValueError):
            continue
        if filed < since:
            continue

        accession = accessions[i] if i < len(accessions) else ""

        if form in TERMINAL_FORMS:
            out.append(
                Event(
                    ticker=ticker,
                    filed=filed,
                    form=form,
                    severity=Severity.CRITICAL,
                    code=form,
                    meaning=TERMINAL_FORMS[form],
                    accession=accession,
                )
            )
            continue

        if not form.startswith("8-K"):
            continue
        raw = items[i] if i < len(items) else ""
        for code in (c.strip() for c in str(raw).split(",") if c.strip()):
            known = ITEM_MEANINGS.get(code)
            if known is None:
                continue
            severity, meaning = known
            out.append(
                Event(
                    ticker=ticker,
                    filed=filed,
                    form=form,
                    severity=severity,
                    code=code,
                    meaning=meaning,
                    accession=accession,
                )
            )
    return out


def events_for(
    ticker: str,
    *,
    since: date | None = None,
    fetch=fetch_submissions,
    resolve_cik=cik_for,
) -> list[Event]:
    """Filings worth flagging for one holding, newest first.

    Both the CIK lookup and the fetch are injectable. They have to be
    separately: the lookup reads a local database that the test suite
    deliberately cannot see, so a test that stubbed only the fetch would
    never reach it and would pass while asserting nothing.
    """
    cutoff = since or (date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS))
    cik = resolve_cik(ticker)
    if cik is None:
        logger.info(f"{ticker}: no CIK on file — cannot check filings")
        return []
    doc = fetch(cik)
    if doc is None:
        return []
    found = _events_from_submissions(ticker, doc, since=cutoff)
    return sorted(found, key=lambda e: (e.filed, e.code), reverse=True)


def scan(
    tickers: Iterable[str],
    *,
    since: date | None = None,
    fetch=fetch_submissions,
    resolve_cik=cik_for,
) -> list[Event]:
    """Every flagged filing across a book, most severe first."""
    order = {Severity.CRITICAL: 0, Severity.INVESTIGATE: 1, Severity.NOTE: 2}
    out: list[Event] = []
    for ticker in tickers:
        out.extend(
            events_for(ticker, since=since, fetch=fetch, resolve_cik=resolve_cik)
        )
    out.sort(key=lambda e: (order[e.severity], -e.filed.toordinal()))
    if out:
        critical = sum(1 for e in out if e.severity is Severity.CRITICAL)
        logger.info(f"{len(out)} filings flagged, {critical} critical")
    return out


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "ITEM_MEANINGS",
    "TERMINAL_FORMS",
    "Event",
    "Severity",
    "cik_for",
    "events_for",
    "fetch_submissions",
    "scan",
]
