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
import re
from functools import lru_cache
from pathlib import Path

from core.logger import get_logger
from core.text import titlecase

logger = get_logger("core.data.sic_codes")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BUNDLE_PATH = PROJECT_ROOT / "data_bundled" / "company_sic.json"
#: Code → the SEC's own industry title. Built by
#: ``scripts.fetch_sic_titles``; see that module for why it is committed.
TITLES_PATH = PROJECT_ROOT / "data_bundled" / "sic_titles.json"


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


@lru_cache(maxsize=1)
def load_sic_titles() -> dict[str, str]:
    """Return the bundled ``code -> title`` mapping (in-memory cached)."""
    if not TITLES_PATH.exists():
        logger.warning(
            f"sic_titles.json bundle missing at {TITLES_PATH}; "
            f"industry labels will be unavailable."
        )
        return {}
    try:
        raw = json.loads(TITLES_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"failed to load {TITLES_PATH}: {exc}")
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def sic_title(sic: int | str | None) -> str | None:
    """The SEC's title for a SIC code, verbatim, or ``None``.

    Verbatim means shouting and the SEC's own abbreviations — 2300 is
    "APPAREL & OTHER FINISHD PRODS OF FABRICS & SIMILAR MATL". Use
    :func:`industry_title` for something to show a reader.
    """
    if sic is None:
        return None
    return load_sic_titles().get(str(sic))


def industry_title(ticker: str) -> str | None:
    """A readable industry label for ``ticker``, or ``None``.

    What the company does, in the SEC's classification of its own
    primary business — the nearest thing to a description that exists
    without a profile feed. Sector buckets are too coarse to be worth
    showing: Harley-Davidson and Ingredion are both "manufacturing",
    while their SIC titles are "Motorcycles, Bicycles & Parts" and
    "Grain Mill Products".

    The SEC's abbreviations survive title-casing ("Finishd Prods"). They
    are left as published rather than expanded by guesswork, since this
    is a quotation of a classification and not prose.
    """
    title = sic_title(sic_for(ticker))
    if not title:
        return None
    return titlecase(title)


__all__ = [
    "industry_for",
    "industry_title",
    "load_sic_map",
    "load_sic_titles",
    "sic_for",
    "sic_title",
]
