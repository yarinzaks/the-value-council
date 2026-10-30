"""Companies Graham's Defensive screen would buy at a lower price.

How this stays honest
~~~~~~~~~~~~~~~~~~~~~

It does not restate Graham's criteria. It runs
:func:`~agents.graham.filters.passes_defensive_filters` — the same
function the buy path runs — with only the two price thresholds opened
up. A name that passes *that* has already satisfied every criterion a
falling price cannot change: the current ratio, long-term debt against
working capital, debt to equity, positive earnings, the dividend, the
size floor. The quotation is the only thing left in the way.

That matters more than it looks. A watch list assembled from a second
copy of the criteria would drift from the screen on the first edit to
either, and would then promise "Graham will buy this at $Y" about a
company he would reject at any price. Reusing the filter makes that
disagreement impossible rather than unlikely.

Which criteria a price can fix
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two, out of the screen's eight. P/E ≤ 15 and P/B ≤ 1.5 both scale with
the quotation. The current ratio, the working-capital cover, debt to
equity, positive net income and the dividend do not move when the price
does, so a company failing one of those is not waiting for a price and
must not be listed as though it were. The size floor moves the wrong
way — a falling price shrinks the market cap — so it counts with the
rest.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date

from agents.graham.filters import (
    DEFAULT_DEFENSIVE_MAX_PB,
    DEFAULT_DEFENSIVE_MAX_PE,
    DEFAULT_DEFENSIVE_MIN_CURRENT_RATIO,
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    current_ratio,
    debt_to_equity,
    passes_defensive_filters,
    pb_ratio,
    pe_ratio,
)
from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger
from core.watchlist import DEFAULT_WATCH_SLOTS, WatchCandidate

logger = get_logger("agents.graham.watch")

Candidate = tuple[PointInTimeFinancials | None, float | None, float | None]


def defensive_watch_candidates(
    candidates: Iterable[Candidate],
    *,
    as_of: date,
    max_pe: float = DEFAULT_DEFENSIVE_MAX_PE,
    max_pb: float = DEFAULT_DEFENSIVE_MAX_PB,
    min_current_ratio: float = DEFAULT_DEFENSIVE_MIN_CURRENT_RATIO,
    max_de: float = DEFAULT_MAX_DE,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
    require_working_capital_cover: bool = True,
    require_dividend: bool = True,
    limit: int = DEFAULT_WATCH_SLOTS,
) -> list[WatchCandidate]:
    """The near misses, closest first, capped at ``limit``.

    Ranked by how far the price has to fall rather than by cheapness:
    this list answers "what might Graham buy next", and the name needing
    a 3% fall is a likelier next purchase than the one needing 30%, even
    if the second is the better business.
    """
    out: list[WatchCandidate] = []

    for fin, market_cap, price in candidates:
        verdict = passes_defensive_filters(
            fin,
            market_cap,
            price,
            as_of=as_of,
            # The two price tests, opened up. Everything else is the
            # screen's own, at the screen's own thresholds.
            max_pe=math.inf,
            max_pb=math.inf,
            min_current_ratio=min_current_ratio,
            max_de=max_de,
            min_market_cap=min_market_cap,
            require_working_capital_cover=require_working_capital_cover,
            require_dividend=require_dividend,
        )
        if not verdict.passed:
            continue
        if fin is None or market_cap is None or price is None:
            continue

        pe = pe_ratio(price, fin)
        pb = pb_ratio(market_cap, fin)
        if pe is None or pb is None or pe <= 0 or pb <= 0:
            # Unreachable through the filter, which rejects both as
            # undefined, but the narrowing is needed and free.
            continue

        # The price each test passes at, as a scale on today's
        # quotation: P/E ≤ max_pe holds at price × (max_pe / pe), and
        # P/B ≤ max_pb at price × (max_pb / pb). Both must hold, so the
        # binding one is the smaller.
        #
        # Scaling the quotation rather than rebuilding the ratios from
        # EPS and book value is deliberate. It keeps the arithmetic on
        # exactly the figures the screen used, so the trigger cannot
        # disagree with the test it inverts — and it sidesteps the
        # market-cap-versus-share-count mismatch that makes the
        # algebraic form wrong on most of this repository's rows.
        scale = min(max_pe / pe, max_pb / pb)
        if scale >= 1.0:
            # Already buyable. The screen will take it today; a watch
            # list that listed it would be describing a holding.
            continue

        # The market cap falls with the price, and the size floor is the
        # one non-price criterion that moves when the quotation does —
        # the wrong way. A company that would drop under the floor on
        # the way to its own trigger is not waiting for a price Graham
        # would buy at, so "he will buy at $Y" would be false of it.
        if market_cap * scale < min_market_cap:
            logger.debug(
                f"{fin.ticker}: trigger price falls below the size floor; "
                "not a watch candidate"
            )
            continue

        blocking = []
        if pe > max_pe:
            blocking.append(f"P/E={pe:.2f} (> {max_pe})")
        if pb > max_pb:
            blocking.append(f"P/B={pb:.2f} (> {max_pb})")

        # Phrased as the decision journal phrases the same facts, so a
        # watch notification and a buy notification read alike.
        passing = []
        if pe <= max_pe:
            passing.append(f"P/E={pe:.2f} (≤ {max_pe})")
        if pb <= max_pb:
            passing.append(f"P/B={pb:.2f} (≤ {max_pb})")
        cr = current_ratio(fin)
        if cr is not None:
            passing.append(f"current ratio={cr:.2f} (≥ {min_current_ratio})")
        de = debt_to_equity(fin)
        if de is not None:
            passing.append(f"D/E={de:.2f} (≤ {max_de})")
        passing.append("positive trailing net income")
        if require_dividend:
            passing.append("pays a dividend")

        out.append(
            WatchCandidate(
                ticker=fin.ticker,
                price=price,
                trigger_price=price * scale,
                blocking=tuple(blocking),
                passing=tuple(passing),
            )
        )

    out.sort(key=lambda c: (c.required_fall_pct, c.ticker))
    if len(out) > limit:
        logger.info(
            f"{as_of}: {len(out)} defensive near misses, reporting the "
            f"closest {limit}"
        )
    return out[:limit]


__all__ = ["defensive_watch_candidates"]
