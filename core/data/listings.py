"""Where a ticker is listed, and whether the index already owns it.

Two universe rules in ``COUNCIL_SELECTION.md`` section 1 had no data
source in this project at all:

* **U1** — US common stock on NYSE, NASDAQ or AMEX. ``company_tickers.json``
  carries only ``cik_str``, ``ticker`` and ``title``; there is no
  exchange anywhere in the repo. :mod:`core.data.ticker_filter` gives
  good share-class heuristics and a hand-maintained baby-bond deny list,
  but a heuristic cannot tell a NASDAQ listing from an OTC one, and
  **2,514 of the SEC's 10,398 tickers are OTC** — nearly a quarter of
  the roster, and precisely the end of it where a $1 price floor and a
  $500k volume floor are not enough protection.

* **U8** — not an S&P 500 member. The doctrine wants "three or fewer
  analysts"; no free source carries coverage counts, so index exclusion
  plus the $5bn cap ceiling is the closest computable proxy for
  neglect. Membership is also the only free fix for survivorship bias
  that ``COUNCIL_DATA.md`` names.

Both bundles are built by ``scripts.prefetch_listings`` and refreshed
manually. Exchange listings change rarely; index membership changes a
few dozen times a year and the historical file is append-only.

Point-in-time membership
------------------------

:func:`in_sp500_on` walks a dated snapshot file, so a backtest asking
about 2013 gets the 2013 index rather than today's wearing a 2013 date.
That distinction is the whole reason the historical file is carried:
the tickers that *left* the index are disproportionately the ones that
failed, and a universe rebuilt from today's membership has quietly
deleted them.
"""

from __future__ import annotations

import csv
import gzip
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TextIO

from core.logger import get_logger

logger = get_logger("core.data.listings")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EXCHANGE_BUNDLE = PROJECT_ROOT / "data_bundled" / "company_exchange.json"
#: Gzipped because consecutive daily snapshots are nearly identical:
#: 5.5MB of text compresses to 44KB, a 125x ratio. Uncompressed it
#: would be the largest file in the repository by an order of magnitude
#: and would grow by a snapshot every trading day.
SP500_HISTORY_BUNDLE = PROJECT_ROOT / "data_bundled" / "sp500_membership.csv.gz"

#: Exchanges U1 accepts. The SEC file spells NYSE American's listings
#: as "NYSE"; "AMEX" is carried anyway so a future change in their
#: labelling does not silently empty the universe.
#:
#: CBOE is deliberately absent. It is a US national exchange and 28
#: tickers sit on it, but U1 names three exchanges and section 2's rule
#: is that a gate which cannot be computed FAILS. Twenty-eight names is
#: not worth loosening a universe rule by interpretation.
MAJOR_US_EXCHANGES: frozenset[str] = frozenset({"NYSE", "NASDAQ", "AMEX"})


@lru_cache(maxsize=1)
def load_exchange_map() -> dict[str, str]:
    """Bundled ``ticker -> exchange``, upper-cased on both sides.

    An empty map on a missing or unreadable bundle. Callers must treat
    an absent exchange as UNKNOWN and fail the gate, never as a pass —
    which is what :func:`is_major_us_listing` returning ``None`` is for.
    """
    if not EXCHANGE_BUNDLE.exists():
        logger.warning(
            f"company_exchange.json bundle missing at {EXCHANGE_BUNDLE}; "
            "U1 cannot be evaluated and every name will read UNKNOWN. "
            "Run scripts.prefetch_listings."
        )
        return {}
    try:
        raw = json.loads(EXCHANGE_BUNDLE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"failed to load {EXCHANGE_BUNDLE}: {exc}")
        return {}
    return {
        str(k).upper(): str(v).upper()
        for k, v in raw.items()
        if v is not None and str(v).strip()
    }


def exchange_for(ticker: str) -> str | None:
    """Upper-cased exchange for ``ticker``, or ``None`` if unknown."""
    return load_exchange_map().get(ticker.upper())


def is_major_us_listing(ticker: str) -> bool | None:
    """U1: does this trade on NYSE, NASDAQ or AMEX?

    Returns:
        ``True`` on a major listing, ``False`` for OTC and anything else
        the SEC names, and ``None`` when the exchange is unknown.
        ``None`` is a distinct answer on purpose: 197 tickers in the SEC
        file carry no exchange at all, and collapsing them into
        ``False`` would report a data gap as a fact about the company.
    """
    ex = exchange_for(ticker)
    if ex is None:
        return None
    return ex in MAJOR_US_EXCHANGES


@contextmanager
def _open_membership(path: Path) -> Iterator[TextIO]:
    """Read the membership file whether or not it is compressed.

    The bundle ships gzipped, but a developer who unpacks it to look at
    it should not have to re-compress it to make the reader work.
    """
    if path.suffix == ".gz":
        with gzip.open(path, "rt", newline="") as fh:
            yield fh
    else:
        with path.open(newline="") as fh:
            yield fh


@lru_cache(maxsize=1)
def load_sp500_history() -> tuple[tuple[date, frozenset[str]], ...]:
    """Dated membership snapshots, oldest first.

    Each row is ``(date, {tickers})``. Kept as a sorted tuple so
    :func:`in_sp500_on` can walk backwards to the last snapshot on or
    before a query date without re-sorting.
    """
    if not SP500_HISTORY_BUNDLE.exists():
        logger.warning(
            f"sp500_membership.csv missing at {SP500_HISTORY_BUNDLE}; "
            "U8 cannot be evaluated. Run scripts.prefetch_listings."
        )
        return ()
    snapshots: list[tuple[date, frozenset[str]]] = []
    try:
        with _open_membership(SP500_HISTORY_BUNDLE) as fh:
            for row in csv.DictReader(fh):
                raw_date = (row.get("date") or "").strip()
                raw_tickers = (row.get("tickers") or "").strip()
                if not raw_date or not raw_tickers:
                    continue
                try:
                    when = date.fromisoformat(raw_date)
                except ValueError:
                    continue
                members = frozenset(
                    t.strip().upper() for t in raw_tickers.split(",") if t.strip()
                )
                if members:
                    snapshots.append((when, members))
    except OSError as exc:
        logger.warning(f"failed to load {SP500_HISTORY_BUNDLE}: {exc}")
        return ()
    snapshots.sort(key=lambda s: s[0])
    return tuple(snapshots)


def in_sp500_on(ticker: str, on: date) -> bool | None:
    """Was ``ticker`` in the index on ``on``?

    Returns ``None`` when the query predates the earliest snapshot or no
    history is bundled — "we do not know" rather than "it was not a
    member", which would quietly widen the universe by exactly the names
    U8 exists to exclude.
    """
    history = load_sp500_history()
    if not history:
        return None
    if on < history[0][0]:
        return None
    symbol = ticker.upper()
    for when, members in reversed(history):
        if when <= on:
            return symbol in members
    return None


__all__ = [
    "MAJOR_US_EXCHANGES",
    "exchange_for",
    "in_sp500_on",
    "is_major_us_listing",
    "load_exchange_map",
    "load_sp500_history",
]
