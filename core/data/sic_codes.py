"""Ticker → SIC code lookup.

The Neff strategy needs industry-relative medians. SIC codes are the
SEC's official industry taxonomy; the first 2 digits identify the
"major industry group" (e.g. 60 = depository institutions, 73 =
business services, 28 = chemicals).

The bundle at ``data_bundled/company_sic.json`` is built by
``scripts.prefetch_sic`` from the SEC submissions endpoint. Since
SIC codes change rarely (only when a company restructures its primary
business), the bundle is refreshed manually rather than on every run.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from core.logger import get_logger

logger = get_logger("core.data.sic_codes")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BUNDLE_PATH = PROJECT_ROOT / "data_bundled" / "company_sic.json"


@lru_cache(maxsize=1)
def load_sic_map() -> dict[str, int | None]:
    """Return the bundled ``ticker -> sic`` mapping (in-memory cached)."""
    if not BUNDLE_PATH.exists():
        logger.warning(
            f"company_sic.json bundle missing at {BUNDLE_PATH}; "
            f"industry-relative scoring will fall back to universe median."
        )
        return {}
    try:
        raw = json.loads(BUNDLE_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"failed to load {BUNDLE_PATH}: {exc}")
        return {}
    # Normalize keys to upper-case.
    return {str(k).upper(): (int(v) if v is not None else None) for k, v in raw.items()}


def sic_for(ticker: str) -> int | None:
    """Return the 4-digit SIC code for ``ticker``, or ``None``."""
    return load_sic_map().get(ticker.upper())


def industry_for(ticker: str) -> int | None:
    """Return the SIC2 (first 2 digits, major industry group), or ``None``."""
    sic = sic_for(ticker)
    if sic is None:
        return None
    return sic // 100


__all__ = ["industry_for", "load_sic_map", "sic_for"]
