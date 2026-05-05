"""Tel Aviv Stock Exchange (TASE) universe.

TASE-listed companies file with the **Israeli Maya system** (the
Israeli analog of SEC EDGAR), not with SEC EDGAR. Maya provides
XBRL filings since 2008, but:

* The Maya REST API is partially behind authentication.
* Filings are submitted in Hebrew with mixed-language XBRL tagging.
* The :mod:`core.data.tase_source` already has a stub that talks to
  TASE's OAuth-protected Open API, but its credentials must be
  configured separately.

For now this module provides:

1. A static list of TASE common-stock tickers (yfinance-format with
   the ``.TA`` suffix), loaded from a maintained JSON file at
   ``data/cache/tase_tickers.json``. The list is hand-curated to
   include TA-35, TA-90, and TA-125 constituents — covering ~125 of
   the largest ~500 TASE-listed names. This is the "starting set" the
   user can extend later.
2. An :class:`Universe` Protocol-compatible facade so the
   :class:`BacktestRunner` accepts it identically to the US universes.
3. Hooks for layering in Maya-sourced fundamentals once the
   plumbing is built.

**Status:** scaffold. Producing a useful Schloss/Greenblatt backtest
on TASE requires:

* A point-in-time Maya XBRL fundamentals cache (analog of
  :mod:`core.data.edgar_cache`) — TODO.
* A historical TASE constituent change log — TODO. Until that exists,
  the universe is the *current* TA-125 list applied to all dates,
  which has survivorship bias.

Use :class:`TASEUniverse` for prototyping. For production-grade
Israeli-market backtests, plumb in Maya fundamentals first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from core.exceptions import ValueCouncilError
from core.logger import get_logger

logger = get_logger("core.backtest.tase_universe")

from core.paths import PROJECT_ROOT, cache_dir as _cache_dir
DEFAULT_CACHE_DIR = _cache_dir()
DEFAULT_TICKERS_PATH = DEFAULT_CACHE_DIR / "tase_tickers.json"

# A starter set of TA-125 constituents (yfinance ``.TA`` suffix).
# This list is intentionally non-exhaustive — it is meant as a
# starting point that the user can replace by dropping a fuller
# JSON list at ``DEFAULT_TICKERS_PATH``. Hand-picked from publicly
# available TA-125 component lists.
STARTER_TA125_TICKERS: tuple[str, ...] = (
    # Banks
    "POLI.TA", "LUMI.TA", "DSCT.TA", "FIBI.TA", "MZTF.TA",
    # Insurance & financial services
    "MGDL.TA", "PHOE.TA", "HARL.TA", "MNRV.TA", "CLIS.TA",
    # Technology
    "NICE.TA", "WIX.TA", "MNDY.TA", "CYBR.TA", "CGEN.TA",
    "ELBIT.TA", "ESLT.TA",
    # Pharma & healthcare
    "TEVA.TA", "OPK.TA",
    # Real estate
    "AZRG.TA", "MGM.TA", "ALHE.TA", "MTRX.TA", "AMOT.TA",
    # Energy
    "DLEKG.TA", "NWMD.TA", "RATI.TA",
    # Industrials & holdings
    "ICL.TA", "ELCO.TA", "DELG.TA", "GZT.TA",
    # Consumer
    "STRS.TA", "OSEM.TA", "SHUF.TA",
    # Telecom
    "BEZQ.TA", "CEL.TA", "PTNR.TA",
)


class TASEUniverseError(ValueCouncilError):
    """Raised on TASE universe failures."""


@dataclass(frozen=True)
class TASEUniverse:
    """Static TASE universe — survivorship-aware once a Maya cache is built.

    Until a historical change log is wired in, ``constituents_at``
    returns the current ticker list for every date. This is **not**
    survivorship-bias-free; it is documented as a known limitation.

    Args:
        tickers: Optional explicit ticker list. If omitted, loads from
            ``data/cache/tase_tickers.json``, falling back to
            :data:`STARTER_TA125_TICKERS`.
        tickers_path: Override path to the JSON ticker file.
    """

    tickers: tuple[str, ...] = field(default_factory=lambda: tuple())
    tickers_path: Path = DEFAULT_TICKERS_PATH

    @classmethod
    def load(
        cls,
        tickers_path: Path | None = None,
        *,
        fall_back_to_starter: bool = True,
    ) -> "TASEUniverse":
        """Load a TASEUniverse from JSON, with starter fallback."""
        path = tickers_path or DEFAULT_TICKERS_PATH
        if path.exists():
            try:
                payload = json.loads(path.read_text())
                tickers = tuple(t.upper() for t in payload.get("tickers", []))
                if tickers:
                    logger.info(f"loaded {len(tickers)} TASE tickers from {path}")
                    return cls(tickers=tickers, tickers_path=path)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"failed to load {path}: {exc}")
        if not fall_back_to_starter:
            raise TASEUniverseError(
                f"no TASE tickers file at {path} and starter fallback disabled"
            )
        logger.info(
            f"using starter TA-125 list ({len(STARTER_TA125_TICKERS)} tickers); "
            f"drop a richer list at {path} to override"
        )
        return cls(tickers=STARTER_TA125_TICKERS, tickers_path=path)

    def save(self) -> None:
        """Persist the current ticker list."""
        self.tickers_path.parent.mkdir(parents=True, exist_ok=True)
        self.tickers_path.write_text(
            json.dumps({"tickers": list(self.tickers)}, indent=2)
        )
        logger.info(f"saved {len(self.tickers)} TASE tickers → {self.tickers_path}")

    # ------------------------------------------------------------------
    # Universe Protocol
    # ------------------------------------------------------------------
    def constituents_at(self, as_of: date | datetime) -> list[str]:
        """Return TASE constituents at ``as_of``.

        **Limitation:** without a historical change log, this returns
        the current ticker list for any date. Documented in module
        docstring.
        """
        # Keep ``as_of`` parameter for interface compliance even though
        # we don't yet use it.
        _ = as_of
        return sorted(self.tickers)

    def was_member_on(self, ticker: str, as_of: date | datetime) -> bool:
        return ticker.upper() in self.constituents_at(as_of)


__all__ = [
    "STARTER_TA125_TICKERS",
    "TASEUniverse",
    "TASEUniverseError",
]
