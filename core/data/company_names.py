"""Ticker → company name resolver.

The dashboard needs to display "AAPL · Apple Inc." style rows. The
EDGAR cache stores XBRL facts but not the human-readable company name.
We hit ``https://www.sec.gov/files/company_tickers.json`` once (it's
~1MB) and persist the result locally in JSON for fast lookups.

Refresh policy: re-fetch if the cached file is older than 30 days,
otherwise use the cached copy. The mapping rarely changes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from core.logger import get_logger
from core.paths import cache_dir

logger = get_logger("core.data.company_names")

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DEFAULT_CACHE_PATH: Path = cache_dir() / "company_names.json"
USER_AGENT = "The-Value-Council research@example.com"
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


def fetch_from_sec() -> dict[str, str]:
    """Download the SEC ticker file and return a {TICKER: name} map."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    resp = requests.get(SEC_TICKERS_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    raw = resp.json()
    out: dict[str, str] = {}
    # SEC schema: {"0": {"cik_str": ..., "ticker": "AAPL", "title": "Apple Inc."}, ...}
    for entry in raw.values():
        ticker = str(entry.get("ticker", "")).upper()
        title = str(entry.get("title", "")).strip()
        if ticker and title:
            out[ticker] = title
    return out


def load_or_fetch(*, path: Path = DEFAULT_CACHE_PATH) -> dict[str, str]:
    """Return the company-name map, refreshing the cache if stale."""
    if path.exists():
        age = time.time() - path.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            try:
                return json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"failed to read {path}, re-fetching: {exc}")
    try:
        names = fetch_from_sec()
    except Exception as exc:
        logger.warning(f"SEC tickers fetch failed: {exc}")
        # Fall back to whatever we had on disk, or empty.
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        return {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(names, indent=2, ensure_ascii=False))
    logger.info(f"cached {len(names)} company names to {path}")
    return names


def company_name(
    ticker: str, *, cache: dict[str, str] | None = None
) -> str | None:
    """Look up the human-readable name for ``ticker``."""
    if cache is None:
        cache = load_or_fetch()
    return cache.get(ticker.upper())


__all__ = [
    "DEFAULT_CACHE_PATH",
    "company_name",
    "fetch_from_sec",
    "load_or_fetch",
]
