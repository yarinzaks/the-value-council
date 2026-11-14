"""When Graham sells a name he holds — and when he keeps it.

The problem this fixes (audit SF-X1, item 13b)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Playbook §7 is explicit: a stock has a value, and when the market
grants it, sell. None of that ran. ``BenjaminGraham.select`` ignored
``held``, and the engine sold a Graham name only because it left the
day's target list — so a Defensive holding that ran from P/E 13 to 16
was sold for failing the *entry* screen while still below its Graham
Number, and none of 823 exits ever cited a Graham Number or an NCAV.

What this module decides
~~~~~~~~~~~~~~~~~~~~~~~~

One :class:`~core.live.doctrine_exits.ExitVerdict` per held name, from
§7.1's triggers and §7.2's non-triggers. Graham's exits run in shadow
(:data:`core.live.doctrine_exits.DOCTRINE_EXIT_MODES`): the runner
journals each verdict beside what the engine did; no weight changes.

* **Which category the holding is.** :class:`HeldPosition` does not
  carry the cohort a name was bought in, and the live strategy is
  rebuilt every run. It is inferred the way the screen chose it: Net-Net
  runs first, so a name whose entry price was at or under ⅔ of its NCAV
  would have been bought as a Net-Net; anything else as Defensive. The
  NCAV is today's, not the purchase day's — an approximation the reason
  string names ("Net-Net:" / "Defensive:") so it can be checked.
* **Trigger 3 — fundamentals deteriorate**, checked first because the
  playbook says "regardless of price": trailing earnings no longer
  positive (both); for Defensive also book value no longer positive,
  current ratio below 1.5, or a dividend reported as zero. 1.5 is the
  playbook's own exit floor, below the 2.0 entry bar — the asymmetry is
  the point. An *unreported* figure is not a deterioration: absence of
  a tag is not evidence (audit FUND-4).
* **Trigger 1 — fair value.** Defensive: price at or above the Graham
  Number, sqrt(22.5 x EPS x BVPS), computed exactly as
  :func:`agents.graham.ranking.score_defensive_candidates` does.
  Net-Net: price at or above 100% of NCAV per share.
* **Trigger 2 — two years.** A Net-Net held :data:`NET_NET_MAX_HOLD_DAYS`
  without reaching NCAV is sold anyway. The playbook writes it for
  Net-Nets; Defensive names are not subject to it.
* **Retention.** A held name with none of the above is kept — whether
  or not it is still on today's list. Falling out of the top N, or
  failing the stricter entry screen (P/E above 15, current ratio
  between 1.5 and 2.0), is not a §7.1 trigger, and §7.2 rules out a
  price decline as one.

Not decided here: **trigger 4, "better opportunity."** It is rank
rotation by another name, the very behaviour retention exists to stop;
the engine's list exit already does it. Whether a stronger candidate
should displace a holding that still qualifies is the owner's call.

When a verdict cannot be reached honestly — no fundamentals, no fresh
price, a Graham Number or NCAV that cannot be computed — no verdict is
given for that name. A keep on no evidence would, once enforced, hold
the name indefinitely; silence leaves it to the engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Literal

from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
)
from core.live.doctrine_exits import ExitVerdict
from core.logger import get_logger
from core.scoring.graham_number import graham_number

from .filters import (
    DEFAULT_NCAV_DISCOUNT_FACTOR,
    current_ratio,
    ncav_per_share,
)

logger = get_logger("agents.graham.exits")

Category = Literal["Net-Net", "Defensive"]

#: §7.1 trigger 2: "If a Net-Net has not appreciated to fair value
#: within two years, sell anyway."
NET_NET_MAX_HOLD_DAYS: int = 730

#: §7.1 trigger 1 for a Net-Net: price at 100% of NCAV.
NET_NET_FAIR_P_NCAV: float = 1.0

#: §7.1 trigger 3: "current ratio drops below 1.5".
DETERIORATION_MIN_CURRENT_RATIO: float = 1.5


def _eps(fin: PointInTimeFinancials) -> float | None:
    """Diluted EPS, else basic — the choice the entry screen makes."""
    return fin.eps_diluted if fin.eps_diluted is not None else fin.eps_basic


def _book_per_share(fin: PointInTimeFinancials) -> float | None:
    if (
        fin.total_equity is None
        or fin.shares_outstanding is None
        or fin.shares_outstanding <= 0
    ):
        return None
    return fin.total_equity / fin.shares_outstanding


def category(
    position: HeldPosition,
    fin: PointInTimeFinancials,
    *,
    max_p_ncav: float = DEFAULT_NCAV_DISCOUNT_FACTOR,
) -> Category:
    """The cohort the screen would have bought this name under.

    Net-Net when the entry price was within ``max_p_ncav`` of a positive
    NCAV per share (the screen tries Net-Net first), else Defensive.
    """
    ncav = ncav_per_share(fin)
    if ncav is not None and ncav > 0 and position.entry_price <= max_p_ncav * ncav:
        return "Net-Net"
    return "Defensive"


def _deterioration(fin: PointInTimeFinancials, cat: Category) -> str | None:
    """Trigger 3's reason, or None while the qualifying traits hold."""
    if fin.net_income is not None and fin.net_income <= 0:
        return (
            f"earnings turned negative "
            f"(trailing net income ${fin.net_income:,.0f})"
        )
    if cat == "Net-Net":
        return None
    if fin.total_equity is not None and fin.total_equity <= 0:
        return "book value no longer positive"
    cr = current_ratio(fin)
    if cr is not None and cr < DETERIORATION_MIN_CURRENT_RATIO:
        return f"current ratio {cr:.2f} below {DETERIORATION_MIN_CURRENT_RATIO}"
    # SEC files dividends as a positive or a negative outflow; only the
    # magnitude carries information (same reading as the entry screen).
    if fin.dividends_paid is not None and abs(fin.dividends_paid) <= 0:
        return "dividend cut (none paid)"
    return None


def decide(
    position: HeldPosition,
    fin: PointInTimeFinancials | None,
    price: float | None,
    *,
    as_of: date,
    max_p_ncav: float = DEFAULT_NCAV_DISCOUNT_FACTOR,
) -> ExitVerdict | None:
    """Keep or sell one held name, or None when it cannot be judged."""
    ticker = position.ticker
    if fin is None or price is None or price <= 0:
        return None

    ncav = ncav_per_share(fin)
    cat = category(position, fin, max_p_ncav=max_p_ncav)

    broke = _deterioration(fin, cat)
    if broke is not None:
        return ExitVerdict(ticker, sell=True, reason=f"{cat}: {broke}")

    # category() returns Net-Net only for a positive NCAV; the check
    # repeats it so the type is narrowed without an assert.
    if cat == "Net-Net" and ncav is not None and ncav > 0:
        p_ncav = price / ncav
        if p_ncav >= NET_NET_FAIR_P_NCAV:
            return ExitVerdict(
                ticker,
                sell=True,
                reason=(
                    f"Net-Net: price ${price:,.2f} reached NCAV "
                    f"${ncav:,.2f}/share (P/NCAV {p_ncav:.2f})"
                ),
            )
        days = position.days_held_at(as_of)
        if days >= NET_NET_MAX_HOLD_DAYS:
            return ExitVerdict(
                ticker,
                sell=True,
                reason=(
                    f"Net-Net: held {days} days without reaching NCAV "
                    f"${ncav:,.2f}/share (P/NCAV {p_ncav:.2f}) — the two-year rule"
                ),
            )
        return ExitVerdict(
            ticker,
            sell=False,
            reason=(
                f"Net-Net: P/NCAV {p_ncav:.2f}, below NCAV ${ncav:,.2f}/share; "
                f"held {days} of {NET_NET_MAX_HOLD_DAYS} days"
            ),
        )

    eps = _eps(fin)
    bvps = _book_per_share(fin)
    gn = graham_number(eps, bvps) if eps is not None and bvps is not None else None
    if gn is None:
        logger.info(
            f"{as_of}: {ticker} — Graham Number not computable "
            f"(EPS {eps}, book/share {bvps}); no exit verdict"
        )
        return None
    if price >= gn:
        return ExitVerdict(
            ticker,
            sell=True,
            reason=(
                f"Defensive: price ${price:,.2f} reached the Graham Number "
                f"${gn:,.2f}"
            ),
        )
    margin = (gn - price) / gn * 100.0
    return ExitVerdict(
        ticker,
        sell=False,
        reason=(
            f"Defensive: price ${price:,.2f} below the Graham Number "
            f"${gn:,.2f} ({margin:.0f}% margin); fundamentals intact"
        ),
    )


def verdicts(
    held: Mapping[str, HeldPosition] | None,
    prices: PriceLookup,
    fundamentals: FundamentalsLookup,
    *,
    as_of: date,
    max_p_ncav: float = DEFAULT_NCAV_DISCOUNT_FACTOR,
) -> list[ExitVerdict]:
    """A verdict for every held name that can be judged, by ticker order.

    Price and fundamentals come from the same lookups the screen reads,
    so a held name is judged on the day's quote, never on a stale mark.
    """
    if not held:
        return []
    out: list[ExitVerdict] = []
    for ticker in sorted(held):
        verdict = decide(
            held[ticker],
            fundamentals.get(ticker),
            prices.get(ticker),
            as_of=as_of,
            max_p_ncav=max_p_ncav,
        )
        if verdict is None:
            logger.info(f"{as_of}: {ticker} held — no Graham exit verdict (data)")
            continue
        out.append(verdict)
    sells = [v for v in out if v.sell]
    if out:
        logger.info(
            f"{as_of}: Graham exits on {len(held)} held: "
            f"{len(out) - len(sells)} keep, {len(sells)} sell, "
            f"{len(held) - len(out)} no verdict"
        )
    return out


__all__ = [
    "DETERIORATION_MIN_CURRENT_RATIO",
    "NET_NET_FAIR_P_NCAV",
    "NET_NET_MAX_HOLD_DAYS",
    "category",
    "decide",
    "verdicts",
]
