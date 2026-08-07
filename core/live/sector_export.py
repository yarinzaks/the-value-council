"""Publish the sector of every held ticker, for the dashboard.

Why
~~~

A list of positions says what an agent owns; it does not say what it is
*doing*. Ten holdings all in banks and ten spread across manufacturing,
utilities and retail are the same list length and completely different
strategies, and the difference is the most legible thing about a value
investor's stance.

Dreman's Rule 18 already made this concrete on the quant side — the
book was 25 holdings in 11 industries with insurance at 28.5%. That
number existed only in a log line. This puts the same fact in front of
a reader for every agent.

How the sector is decided
~~~~~~~~~~~~~~~~~~~~~~~~~

SIC division, the SEC's own top-level grouping, derived from the
four-digit code that arrives with every filing. Divisions are coarse by
design — "Manufacturing" spans chemicals and semiconductors — which is
right here: the question is whether an agent is concentrated in
financials, not whether two chemical companies differ.

:mod:`agents.dreman.diversification` uses the finer two-digit major
group for its Rule 18 cap, because a cap needs to distinguish banks
from insurers. Both are correct for their own question; they are not
the same question.

A ticker with no SIC is reported as unknown rather than bucketed
somewhere plausible. The dashboard shows that slice as its own, because
an agent whose book is 30% unclassifiable is telling you something.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.data.sic_codes import sic_for
from core.live.portfolio import LivePortfolio
from core.logger import get_logger
from core.paths import DATA_ROOT

logger = get_logger("core.live.sector_export")

SECTORS_PATH: Path = DATA_ROOT / "sectors.json"

#: SIC divisions, as the SEC defines them. ``(low, high, key)`` with the
#: bounds inclusive on the leading two digits.
_DIVISIONS: tuple[tuple[int, int, str], ...] = (
    (1, 9, "agriculture"),
    (10, 14, "mining"),
    (15, 17, "construction"),
    (20, 39, "manufacturing"),
    (40, 49, "transport_utilities"),
    (50, 51, "wholesale"),
    (52, 59, "retail"),
    (60, 67, "finance"),
    (70, 89, "services"),
    (91, 99, "public_admin"),
)

UNKNOWN = "unknown"


def sector_of(ticker: str) -> str:
    """SIC division key for ``ticker``, or ``"unknown"``.

    Unknown is a real answer, not a bucket of last resort: filing it
    under "services" because that division is large would hide the fact
    that the agent is holding something nobody has classified.
    """
    sic = sic_for(ticker)
    if sic is None:
        return UNKNOWN
    major = int(str(sic).zfill(4)[:2])
    for low, high, key in _DIVISIONS:
        if low <= major <= high:
            return key
    return UNKNOWN


def export_sectors(
    portfolios: list[LivePortfolio],
    *,
    path: Path = SECTORS_PATH,
) -> int:
    """Write ``{ticker: sector}`` for every held ticker.

    Returns the number of tickers written. Keyed by ticker rather than
    per agent so the file stays small and the dashboard can weight the
    slices itself from whichever portfolio it is showing.
    """
    tickers = sorted(
        {p.ticker for portfolio in portfolios for p in portfolio.positions}
    )
    mapping = {t: sector_of(t) for t in tickers}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, indent=0, sort_keys=True))

    unknown = sum(1 for v in mapping.values() if v == UNKNOWN)
    logger.info(
        f"exported sectors for {len(mapping)} held tickers "
        f"({unknown} unclassified)"
    )
    return len(mapping)


__all__ = ["SECTORS_PATH", "UNKNOWN", "export_sectors", "sector_of"]
