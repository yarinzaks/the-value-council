"""Common-equity ticker filter.

US exchanges encode share-class metadata in the ticker symbol itself.
Our SEC EDGAR cache contains thousands of CIK → ticker mappings that
include preferred shares, depositary shares, baby bonds, ADRs, foreign
listings, mutual funds, and warrants — none of which a value-investing
agent should be trading.

The conventions we filter on:

NASDAQ 5-letter tickers
~~~~~~~~~~~~~~~~~~~~~~~
The 5th letter is a class indicator. The diagnostic ones for our use
case are:

* ``F`` — foreign issuer (e.g. RSMDF, ENBNF) — exclude.
* ``M``, ``N``, ``O``, ``P`` — preferred series (e.g. WFCNP, BHFAM) — exclude.
* ``Q`` — bankruptcy (deprecated but still seen) — exclude.
* ``R`` — rights — exclude.
* ``W`` — warrant — exclude.
* ``X`` — mutual fund — exclude.
* ``Y`` — ADR — exclude.

The 5th letter ``A``-``D``, ``H``-``L`` is normally a class indicator
that *is* common equity (e.g. ``GOOGL``, ``BRKB`` if hyphen-stripped).
We keep those.

NYSE 3- and 4-letter "baby bond" tickers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
NYSE issues some preferreds/baby bonds with bare alpha tickers (MGR,
MGRE, RZB, RZC, AFGE, SOJD, etc.) where the relationship to a parent
common-equity ticker isn't obvious from the symbol alone. There's no
public list we can rely on, so we rely on a maintained explicit deny
list inside this module — appending to it costs one line and the
rationale stays auditable.

Hyphens and dots
~~~~~~~~~~~~~~~~
``BRK.B``, ``ABC-A`` — alternate share classes; we exclude these as
the agents already pick up ``BRK`` (or its primary class) elsewhere.

Digits
~~~~~~
A US common-equity ticker never contains digits. Everything with a
digit is rejected.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from core.logger import get_logger
from core.paths import PROJECT_ROOT

logger = get_logger("core.data.ticker_filter")

# Anything matching this regex is *not* common equity.
# Stays simple — if we need the regex to grow, the deny list is the
# pressure-release valve.
_DIGIT_RE = re.compile(r"\d")
_NON_ALPHA_RE = re.compile(r"[^A-Z]")


# 5-letter NASDAQ class indicators that aren't common equity.
_NASDAQ_5TH_LETTER_REJECT: frozenset[str] = frozenset({
    "F",  # foreign
    "M",  # preferred (4th in a series)
    "N",  # preferred (3rd in a series)
    "O",  # preferred (2nd in a series)
    "P",  # preferred (1st in a series)
    "Q",  # bankruptcy
    "R",  # rights issue
    "W",  # warrant
    "X",  # mutual fund
    "Y",  # ADR
    "Z",  # 4th class — rare, treat as non-common
})

# 4-letter tickers where the 4th letter is a NASDAQ class indicator.
# We're stricter here because 4-letter tickers are common ("META",
# "INTC", "AAPL"), so we only reject the most diagnostic suffixes.
_NASDAQ_4TH_LETTER_REJECT: frozenset[str] = frozenset({
    "Q",  # bankruptcy
    "W",  # warrant
})

# Explicit deny list for NYSE preferreds/baby bonds whose alpha-only
# tickers don't follow a recognisable pattern. Sourced from observed
# false positives in our cache. Append freely.
_DENY_LIST: frozenset[str] = frozenset({
    # Magic Inc baby bonds (parent: MGM Resorts? not — these are
    # actually IGT-related but listed independently)
    "MGR",
    "MGRE",
    # American Financial Group preferreds
    "AFGB",
    "AFGC",
    "AFGD",
    "AFGE",
    "AFGH",
    # Reinsurance Group preferreds
    "RZB",
    "RZC",
    # Southern Company series of preferred subordinated notes
    "SOJC",
    "SOJD",
    "SOJE",
    # Duke Energy preferred
    "DTB",
    "DTG",
    "DTW",
    "DTY",
})


def is_common_equity(ticker: str) -> bool:
    """Return True if ``ticker`` looks like an ordinary common-stock symbol.

    Heuristic only — there will always be edge cases. The intent is to
    filter the bulk of preferreds, baby bonds, depositary shares, ADRs,
    funds, warrants, and rights from the universe before any agent
    sees them. Specific known offenders sit in :data:`_DENY_LIST`.
    """
    if not ticker:
        return False
    t = ticker.upper().strip()
    # Disallow digits (e.g. "BRK.B" already handled by non-alpha branch).
    if _DIGIT_RE.search(t):
        return False
    # Disallow anything other than uppercase A-Z (no dots, hyphens).
    if _NON_ALPHA_RE.search(t):
        return False
    # Length: 1 letter is too speculative (common stocks are 1-5 letters
    # in practice; 1-letter tickers are reserved on NYSE — keep them).
    if len(t) > 5 or len(t) < 1:
        return False
    # Explicit deny list.
    if t in _DENY_LIST:
        return False
    # 5-letter NASDAQ class indicator filter.
    if len(t) == 5 and t[-1] in _NASDAQ_5TH_LETTER_REJECT:
        return False
    # 4-letter NASDAQ secondary indicators.
    if len(t) == 4 and t[-1] in _NASDAQ_4TH_LETTER_REJECT:
        return False
    return True


def filter_common_equity(tickers: list[str]) -> list[str]:
    """Return only common-equity members of ``tickers``."""
    return [t for t in tickers if is_common_equity(t)]


# ---------------------------------------------------------------------------
# One listing per issuer
# ---------------------------------------------------------------------------
# The symbol-shape rules above are a heuristic, and a heuristic cannot see
# that ENBFF and ENB are the same company. Every ticker under one CIK
# resolves to that CIK's financial statements, so a $25-par note inherits
# the parent's revenue, equity and share count and is valued as though it
# were the common stock.
#
# Measured on the bundled SEC map (10,412 rows): 1,464 CIKs carry more
# than one ticker, and 418 of those still had more than one survive
# is_common_equity — 579 redundant symbols.
#
# Selection rule: lowest index in company_tickers.json. The SEC orders
# that file so the primary listing sits far above its derivatives, and it
# holds across every case inspected — ENB at 138 ahead of fifteen foreign
# classes, FMCC at 1,726 ahead of twelve preferreds, BMO at 129 ahead of
# twenty-six exchange-traded notes.
_TICKER_MAP_PATH = PROJECT_ROOT / "data_bundled" / "company_tickers.json"


@lru_cache(maxsize=1)
def primary_listings() -> frozenset[str]:
    """Tickers that are the primary common listing for their issuer.

    Returns an empty set when the bundled map is missing or unreadable,
    which makes :func:`is_primary_listing` fall open rather than empty
    the universe.
    """
    try:
        raw = json.loads(_TICKER_MAP_PATH.read_text())
    except (OSError, ValueError) as exc:
        logger.warning(f"ticker map unavailable at {_TICKER_MAP_PATH}: {exc}")
        return frozenset()

    best: dict[int, tuple[int, str]] = {}
    for index, row in enumerate(raw.values()):
        try:
            cik = int(row["cik_str"])
            ticker = str(row["ticker"]).upper().strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not is_common_equity(ticker):
            continue
        current = best.get(cik)
        if current is None or index < current[0]:
            best[cik] = (index, ticker)
    return frozenset(ticker for _, ticker in best.values())


def is_primary_listing(ticker: str) -> bool:
    """Return True if ``ticker`` is its issuer's primary listing.

    Falls open — returns True — when the bundled map has no opinion, so
    a ticker the SEC map does not cover is left to the other filters
    rather than being silently dropped.
    """
    known = primary_listings()
    if not known:
        return True
    t = ticker.upper().strip()
    return t in known or t not in _all_mapped_tickers()


@lru_cache(maxsize=1)
def _all_mapped_tickers() -> frozenset[str]:
    """Every ticker the bundled SEC map knows about, common or not."""
    try:
        raw = json.loads(_TICKER_MAP_PATH.read_text())
    except (OSError, ValueError):
        return frozenset()
    return frozenset(
        str(row["ticker"]).upper().strip() for row in raw.values() if "ticker" in row
    )


__all__ = [
    "filter_common_equity",
    "is_common_equity",
    "is_primary_listing",
    "primary_listings",
]
