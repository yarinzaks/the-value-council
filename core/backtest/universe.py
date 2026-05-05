"""Historical S&P 500 constituents — survivorship-bias-free universe.

Sources Wikipedia's two canonical pages:

1. https://en.wikipedia.org/wiki/List_of_S%26P_500_companies — current
   constituents and the change log table.
2. The change log records every addition/removal with date, ticker
   added, ticker removed.

Membership at any historical date is reconstructed by starting from
the current list and walking the change log backward in time.

**Survivorship bias:** because the change log records *removed*
tickers (Lehman, WaMu, Bear Stearns, etc.), they reappear in the
historical universe at the dates when they were members — exactly
what is needed for honest backtests.

Caches:
- ``data/cache/sp500_current.json`` — most recent constituent list
- ``data/cache/sp500_changes.json`` — full change log

Both are JSON files; refresh by calling :func:`refresh_universe_cache`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from core.exceptions import ValueCouncilError
from core.logger import get_logger

logger = get_logger("core.backtest.universe")

from core.paths import PROJECT_ROOT, cache_dir as _cache_dir
DEFAULT_CACHE_DIR = _cache_dir()

WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


class UniverseError(ValueCouncilError):
    """Raised when the universe module cannot satisfy a request."""


@dataclass(frozen=True)
class Change:
    """One addition/removal pair from the S&P 500 change log."""

    effective_date: date
    added_ticker: str | None
    removed_ticker: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "effective_date": self.effective_date.isoformat(),
            "added_ticker": self.added_ticker,
            "removed_ticker": self.removed_ticker,
        }

    @classmethod
    def from_dict(cls, d: dict[str, str | None]) -> "Change":
        return cls(
            effective_date=date.fromisoformat(str(d["effective_date"])),
            added_ticker=d.get("added_ticker") or None,
            removed_ticker=d.get("removed_ticker") or None,
        )


class HistoricalUniverse:
    """Survivorship-bias-free S&P 500 constituent reconstruction."""

    def __init__(
        self,
        current_constituents: list[str],
        change_log: list[Change],
        *,
        cache_dir: Path | None = None,
    ) -> None:
        self._current = sorted(set(current_constituents))
        # Sort changes ascending by date — we reverse-iterate when undoing.
        self._changes = sorted(change_log, key=lambda c: c.effective_date)
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._membership_cache: dict[date, frozenset[str]] = {}

    @property
    def current(self) -> list[str]:
        """Most recent S&P 500 constituent list."""
        return list(self._current)

    @property
    def changes(self) -> list[Change]:
        """Chronological change log (ascending by effective date)."""
        return list(self._changes)

    def constituents_at(self, as_of: date | datetime) -> list[str]:
        """Return the S&P 500 ticker list that was active on ``as_of``.

        Algorithm: start from the current list and walk the change log
        backward, undoing each change whose effective_date is strictly
        AFTER ``as_of``. The result is the membership snapshot as it
        existed on ``as_of``.
        """
        if isinstance(as_of, datetime):
            as_of = as_of.date()

        if as_of in self._membership_cache:
            return sorted(self._membership_cache[as_of])

        members: set[str] = set(self._current)
        # Walk changes in REVERSE chronological order, undoing those
        # that occurred after `as_of`.
        for change in reversed(self._changes):
            if change.effective_date <= as_of:
                # This change was already in effect by `as_of`; stop.
                break
            # Undo: removed_ticker comes back, added_ticker leaves.
            if change.added_ticker:
                members.discard(change.added_ticker)
            if change.removed_ticker:
                members.add(change.removed_ticker)

        self._membership_cache[as_of] = frozenset(members)
        return sorted(members)

    def was_member_on(self, ticker: str, as_of: date | datetime) -> bool:
        """Convenience: was ``ticker`` an S&P 500 member on ``as_of``?"""
        return ticker.upper() in self.constituents_at(as_of)

    def members_on_dates(
        self, dates: Iterable[date | datetime]
    ) -> dict[date, list[str]]:
        """Vectorized lookup over many dates."""
        result: dict[date, list[str]] = {}
        for d in dates:
            d_norm = d.date() if isinstance(d, datetime) else d
            result[d_norm] = self.constituents_at(d_norm)
        return result

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save(self, cache_dir: Path | None = None) -> None:
        """Persist the universe to JSON files in ``cache_dir``."""
        cdir = cache_dir or self._cache_dir
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "sp500_current.json").write_text(
            json.dumps({"constituents": self._current}, indent=2)
        )
        (cdir / "sp500_changes.json").write_text(
            json.dumps(
                {"changes": [c.to_dict() for c in self._changes]}, indent=2
            )
        )
        logger.info(
            f"saved universe: {len(self._current)} current, "
            f"{len(self._changes)} historical changes → {cdir}"
        )

    @classmethod
    def load(cls, cache_dir: Path | None = None) -> "HistoricalUniverse":
        """Load a previously persisted universe."""
        cdir = cache_dir or DEFAULT_CACHE_DIR
        cur_path = cdir / "sp500_current.json"
        chg_path = cdir / "sp500_changes.json"
        if not cur_path.exists() or not chg_path.exists():
            raise UniverseError(
                f"no universe cache at {cdir}. Call refresh_universe_cache()."
            )
        cur = json.loads(cur_path.read_text())["constituents"]
        chg_raw = json.loads(chg_path.read_text())["changes"]
        chg = [Change.from_dict(d) for d in chg_raw]
        return cls(cur, chg, cache_dir=cdir)


# ----------------------------------------------------------------------
# Wikipedia ingestion
# ----------------------------------------------------------------------
def _parse_wiki_change_date(value: str) -> date | None:
    """Parse a date string from Wikipedia's change-log table.

    Examples seen in the wild: ``"April 4, 2023"``, ``"2023-04-04"``,
    ``"April 2023"`` (month-only), ``""`` (blank).
    """
    if not value or pd.isna(value):
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%B %d, %Y", "%Y-%m-%d", "%b %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Last resort: try pandas
    try:
        ts = pd.to_datetime(s, errors="coerce")
        if pd.notna(ts):
            return ts.date()
    except (ValueError, TypeError):
        pass
    return None


def fetch_universe_from_wikipedia() -> HistoricalUniverse:
    """Build a :class:`HistoricalUniverse` by scraping Wikipedia.

    Uses ``pandas.read_html`` which reads all tables on the page. The
    page layout has historically been stable: table 0 is the current
    constituents, table 1 is the change log. We defensively scan all
    tables for the expected columns instead of relying on positions.
    """
    logger.info(f"fetching S&P 500 universe from {WIKI_SP500_URL}")
    try:
        # Wikipedia blocks the default urllib User-Agent — fetch with
        # requests using an identifying User-Agent (per their bot
        # policy: include contact info), then pass the HTML to
        # pandas.read_html.
        import requests

        response = requests.get(
            WIKI_SP500_URL,
            headers={
                "User-Agent": (
                    "TheValueCouncil/0.1 "
                    "(https://github.com/the-value-council; research)"
                )
            },
            timeout=30,
        )
        response.raise_for_status()
        from io import StringIO

        tables = pd.read_html(StringIO(response.text))
    except Exception as exc:  # noqa: BLE001 — many possible network errors
        raise UniverseError(f"failed to fetch Wikipedia tables: {exc}") from exc

    current_df: pd.DataFrame | None = None
    changes_df: pd.DataFrame | None = None

    for tbl in tables:
        cols_lower = [str(c).lower() for c in tbl.columns]
        # Current constituents table has columns like "Symbol", "Security".
        if any("symbol" in c for c in cols_lower) and current_df is None:
            current_df = tbl
            continue
        # Change log has multi-level columns: ("Date", ""), ("Added", "Ticker"), etc.
        # Flatten and detect.
        flat_cols = [
            " ".join(str(part).strip() for part in (c if isinstance(c, tuple) else (c,))).strip()
            for c in tbl.columns
        ]
        joined = " | ".join(flat_cols).lower()
        if "added" in joined and "removed" in joined and changes_df is None:
            changes_df = tbl.copy()
            changes_df.columns = flat_cols

    if current_df is None:
        raise UniverseError(
            "could not locate current-constituents table on Wikipedia page"
        )
    if changes_df is None:
        raise UniverseError(
            "could not locate change-log table on Wikipedia page"
        )

    # Extract current tickers
    sym_col = next(c for c in current_df.columns if "symbol" in str(c).lower())
    current = sorted(
        {str(t).strip().replace(".", "-").upper() for t in current_df[sym_col].dropna()}
    )

    # Extract changes — column names look like "Date", "Added Ticker",
    # "Removed Ticker" after flattening.
    date_col = next(c for c in changes_df.columns if "date" in c.lower())
    added_ticker_col = next(
        c for c in changes_df.columns if "added" in c.lower() and "ticker" in c.lower()
    )
    removed_ticker_col = next(
        c for c in changes_df.columns if "removed" in c.lower() and "ticker" in c.lower()
    )

    changes: list[Change] = []
    for _, row in changes_df.iterrows():
        d = _parse_wiki_change_date(row[date_col])
        if d is None:
            continue
        added = row[added_ticker_col]
        removed = row[removed_ticker_col]
        added_str = (
            None if pd.isna(added) or not str(added).strip() else str(added).strip().upper()
        )
        removed_str = (
            None if pd.isna(removed) or not str(removed).strip() else str(removed).strip().upper()
        )
        if not added_str and not removed_str:
            continue
        changes.append(
            Change(effective_date=d, added_ticker=added_str, removed_ticker=removed_str)
        )

    logger.info(
        f"fetched {len(current)} current constituents and {len(changes)} historical changes"
    )
    return HistoricalUniverse(current, changes)


def refresh_universe_cache(cache_dir: Path | None = None) -> HistoricalUniverse:
    """Re-fetch from Wikipedia and persist to ``cache_dir``."""
    universe = fetch_universe_from_wikipedia()
    universe.save(cache_dir)
    return universe


def load_universe(
    cache_dir: Path | None = None,
    *,
    refresh_if_missing: bool = True,
) -> HistoricalUniverse:
    """Load the universe from cache, refreshing from Wikipedia if absent."""
    try:
        return HistoricalUniverse.load(cache_dir)
    except UniverseError:
        if not refresh_if_missing:
            raise
        return refresh_universe_cache(cache_dir)


__all__ = [
    "Change",
    "HistoricalUniverse",
    "UniverseError",
    "fetch_universe_from_wikipedia",
    "load_universe",
    "refresh_universe_cache",
]
