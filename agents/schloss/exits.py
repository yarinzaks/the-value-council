"""When Schloss sells a name he holds — and when he keeps it.

The problem this fixes (audit SF-X1, item 13c)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Playbook §7 is mechanical: sell at a 40-60% gain after looking at book
again, sell outright above 1.2x book, sell at any price when the
balance sheet breaks — and never because the price fell. None of that
ran. ``WalterSchloss.select`` ignored ``held``, and the engine sold a
Schloss name only because it left the day's target list: BHF went on
2026-11-12 at -2.6% for "left today's target list", which is the one
reason §7.4 and §7.2 both rule out.

What this module decides
~~~~~~~~~~~~~~~~~~~~~~~~

One :class:`~core.live.doctrine_exits.ExitVerdict` per held name.
Schloss's exits run in shadow
(:data:`core.live.doctrine_exits.DOCTRINE_EXIT_MODES`): the runner
journals each verdict beside what the engine did; no weight changes.
Book is *tangible* book, as the entry screen measures it (§11.2,
:func:`agents.schloss.filters.price_to_book`).

Checked in this order:

* **§7.2 — fundamentals deteriorate, at any price.** Debt above 100% of
  equity (:data:`DETERIORATION_MAX_DE`), or tangible book no longer
  positive. The entry screen required a positive tangible book, so
  losing all of it is a decline of more than the playbook's 30%. A
  smaller decline in book cannot be measured: :class:`HeldPosition`
  does not carry the book value at entry. An *unreported* debt figure
  is not a deterioration (audit FUND-4).
* **§7.1 — fully exit above 1.2x book**, whatever the gain
  (:data:`FULL_EXIT_PRICE_TO_BOOK`).
* **§7.1 + §7.3 — the gain target, then reevaluate.** At
  :data:`GAIN_TARGET_PCT` or more above cost the playbook says sell —
  "before selling, try to reevaluate the company again and see where
  the stock sells in relation to its book value", and hold "if still
  significantly below book". "Significantly below" is read as the
  entry screen's own discount: at or under
  :data:`REEVALUATE_HOLD_MAX_PRICE_TO_BOOK` (§4.1 filter 2, a 20%
  discount) the name is kept; above it, sold.
* **Retention.** Anything else is kept — whether or not it is still on
  today's list. A price decline is never a sell (§7.2 "NOT for normal
  price decline alone", §7.4); neither is a loss year, which Schloss
  tolerated, nor slipping in the P/B ranking.

Not decided here:

* **The trim.** §7.1 trims 50-75% at the gain target and holds the rest
  while it is below intrinsic value. A verdict is keep or sell, so the
  gain target is a whole-position sell; a partial exit is the owner's
  call.
* **Business model breaks, accounting issues, fraud** (§7.2): nothing
  in the filings data says so.

When a verdict cannot be reached honestly — no fundamentals, no fresh
price, no share count to put book on a per-share basis, no usable cost
basis — no verdict is given for that name. A keep on no evidence would,
once enforced, hold the name indefinitely; silence leaves it to the
engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
)
from core.live.doctrine_exits import ExitVerdict
from core.logger import get_logger

from .filters import (
    debt_to_equity,
    tangible_book_value_per_share,
    tangible_common_equity,
)

logger = get_logger("agents.schloss.exits")

#: §7.1: "Primary sell target: 40-60% gain from cost basis". The
#: verdict turns at the bottom of the range.
GAIN_TARGET_PCT: float = 40.0

#: §7.1: "Fully exit if price exceeds 1.2x book value".
FULL_EXIT_PRICE_TO_BOOK: float = 1.2

#: §7.3: at the gain target, hold "if still significantly below book".
#: The entry screen's discount (§4.1 filter 2: P/B <= 0.8, "minimum 20%
#: discount") is the measure of "significantly".
REEVALUATE_HOLD_MAX_PRICE_TO_BOOK: float = 0.8

#: §7.2 and §6.4's hard stop: "debt rises above 100% of equity".
DETERIORATION_MAX_DE: float = 1.0

_PRECISION = 6


def _deterioration(fin: PointInTimeFinancials) -> str | None:
    """§7.2's reason, or None while the balance sheet holds."""
    tce = tangible_common_equity(fin)
    if tce is not None and tce <= 0:
        return f"tangible book value no longer positive (${tce:,.0f})"
    de = debt_to_equity(fin)
    if de is not None and de > DETERIORATION_MAX_DE:
        return f"debt rose above 100% of equity (D/E {de:.2f})"
    return None


def decide(
    position: HeldPosition,
    fin: PointInTimeFinancials | None,
    price: float | None,
) -> ExitVerdict | None:
    """Keep or sell one held name, or None when it cannot be judged."""
    ticker = position.ticker
    if fin is None or price is None or price <= 0:
        return None

    broke = _deterioration(fin)
    if broke is not None:
        return ExitVerdict(ticker, sell=True, reason=f"fundamentals: {broke}")

    tbvps = tangible_book_value_per_share(fin)
    if tbvps is None:
        logger.info(
            f"{ticker} — tangible book per share not computable "
            f"(shares {fin.shares_outstanding}); no exit verdict"
        )
        return None
    # Ratios are compared to a millionth: $9.10 on a $6.50 cost is a
    # 40% gain, not the 39.99999999999999 binary floats make of it.
    pb = round(price / tbvps, _PRECISION)
    book = f"{pb:.2f}x tangible book ${tbvps:,.2f}/share"

    if pb > FULL_EXIT_PRICE_TO_BOOK:
        return ExitVerdict(
            ticker,
            sell=True,
            reason=(
                f"price ${price:,.2f} is {book}, above the "
                f"{FULL_EXIT_PRICE_TO_BOOK}x full-exit line"
            ),
        )

    if position.entry_price <= 0:
        logger.info(f"{ticker} — cost basis {position.entry_price}; no exit verdict")
        return None
    gain = round((price / position.entry_price - 1.0) * 100.0, _PRECISION)
    move = (
        f"up {gain:.0f}%" if gain >= 0 else f"down {abs(gain):.0f}%"
    ) + f" from cost ${position.entry_price:,.2f}"

    if gain >= GAIN_TARGET_PCT:
        if pb <= REEVALUATE_HOLD_MAX_PRICE_TO_BOOK:
            return ExitVerdict(
                ticker,
                sell=False,
                reason=(
                    f"{move}, past the {GAIN_TARGET_PCT:.0f}% target, but still "
                    f"{book} — reevaluated, still well below book"
                ),
            )
        return ExitVerdict(
            ticker,
            sell=True,
            reason=(
                f"{move}, past the {GAIN_TARGET_PCT:.0f}% target, and at "
                f"{book} no longer {REEVALUATE_HOLD_MAX_PRICE_TO_BOOK}x or under"
            ),
        )

    return ExitVerdict(
        ticker,
        sell=False,
        reason=f"{move}; {book}; balance sheet intact",
    )


def verdicts(
    held: Mapping[str, HeldPosition] | None,
    prices: PriceLookup,
    fundamentals: FundamentalsLookup,
    *,
    as_of: date,
) -> list[ExitVerdict]:
    """A verdict for every held name that can be judged, by ticker order.

    Price and fundamentals come from the same lookups the screen reads,
    so a held name is judged on the day's quote, never on a stale mark.
    """
    if not held:
        return []
    out: list[ExitVerdict] = []
    for ticker in sorted(held):
        verdict = decide(held[ticker], fundamentals.get(ticker), prices.get(ticker))
        if verdict is None:
            logger.info(f"{as_of}: {ticker} held — no Schloss exit verdict (data)")
            continue
        out.append(verdict)
    if out:
        sells = sum(1 for v in out if v.sell)
        logger.info(
            f"{as_of}: Schloss exits on {len(held)} held: "
            f"{len(out) - sells} keep, {sells} sell, "
            f"{len(held) - len(out)} no verdict"
        )
    return out


__all__ = [
    "DETERIORATION_MAX_DE",
    "FULL_EXIT_PRICE_TO_BOOK",
    "GAIN_TARGET_PCT",
    "REEVALUATE_HOLD_MAX_PRICE_TO_BOOK",
    "decide",
    "verdicts",
]
