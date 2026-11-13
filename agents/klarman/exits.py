"""When Klarman sells a name he holds — and when he keeps it.

The problem this fixes (audit SF-X1, item 13e)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Playbook §7: Klarman "will ease out of [a position] as the price
rises" — scaling out from 80% of intrinsic value, out completely by
110% — and sells at any price when the original thesis breaks. §7.3
lists what is not a trigger: market downturns, earnings noise, analyst
downgrades, sector rotation. None of it ran. ``SethKlarman.select``
ignored ``held``, and the engine sold a Klarman name only because it
left the day's target list — 37 exits since 2026-08-05, every one for
rank, none for value or thesis.

What this module decides
~~~~~~~~~~~~~~~~~~~~~~~~

One :class:`~core.live.doctrine_exits.ExitVerdict` per held name.
Klarman's exits run in shadow
(:data:`core.live.doctrine_exits.DOCTRINE_EXIT_MODES`): the runner
journals each verdict beside what the engine did; no weight changes.
Value and thesis are measured the way the entry screen measures them —
the same conservative DCF (:func:`agents.klarman.valuation.intrinsic_value`)
and the same balance-sheet gates (:mod:`agents.klarman.filters`) — so a
name is judged by the doctrine that bought it.

Checked in this order:

* **Trigger 2 — the thesis is broken**, first because it sells "at any
  price" (§12.3). The entry thesis is a cash generator with a sound
  balance sheet bought below a conservative DCF value; it is broken
  when one of those is no longer true on the filings:

  - book equity reported zero or negative;
  - D/E above the entry screen's ceiling (``max_de``, 0.7) — §10.6,
    "leverage destroys margin of safety". Unreported debt is unknown,
    not a breach (audit FUND-4);
  - free cash flow the DCF can no longer project, on a record long
    enough to value (three 10-Ks): negative in the latest year,
    negative on average, or shrinking faster than
    :data:`~agents.klarman.valuation.DEFAULT_MIN_GROWTH_PCT` a year.
    These are the cases ``intrinsic_value`` refuses; for a name the
    screen once valued, the refusal is the thesis failing.

* **Trigger 1 — price approaches intrinsic value**, the primary exit.
  At or past :data:`COMPLETE_EXIT_PCT` (110%) of value the exit is
  complete: sell. From :data:`SCALE_OUT_START_PCT` (80%) up to that
  line the playbook trims 25-50% and holds the rest; a verdict is keep
  or sell, so the zone is a keep whose reason names the trim.

* **Retention.** Anything else is kept — on or off today's list. A
  price fall of any size only widens the margin (§7.3), and a loss
  year is earnings noise to a thesis built on cash flow.

Not decided here:

* **The 25-50% trim itself.** :class:`ExitVerdict` is keep or sell.
* **Trigger 3 — a better opportunity at a greater margin.** It weighs a
  held name against a candidate; a verdict is about one held name.
* **Trigger 4 — liquidity needs.** The book has no redemptions.
* **"Catalysts complete"** (§7.1) and thesis breaks read from text —
  catalysts that never came, management failure: the LLM memo's
  territory, not the filings'.

When a verdict cannot be reached honestly — no fundamentals, no price,
a financial (whose OCF - capex is not free cash flow), fewer than three
years of free cash flow, or no market cap — and no thesis break fired
on what *is* known, no verdict is given for that name. A keep on no
evidence would, once enforced, hold the name indefinitely; silence
leaves it to the engine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
)
from core.data.edgar_cache import EdgarCache
from core.live.doctrine_exits import ExitVerdict
from core.logger import get_logger

from .filters import DEFAULT_MAX_DE, debt_to_equity
from .valuation import (
    DEFAULT_MIN_GROWTH_PCT,
    average_fcf,
    historical_fcf,
    intrinsic_value,
    trailing_growth_pct,
)

logger = get_logger("agents.klarman.exits")

#: §7.2 trigger 1: "Begin scaling out at 80% of intrinsic value".
SCALE_OUT_START_PCT: float = 80.0

#: §7.2 trigger 1: "complete exit by 110% of intrinsic value".
COMPLETE_EXIT_PCT: float = 110.0

#: The fewest annual FCF records the DCF values (``intrinsic_value``).
MIN_FCF_YEARS: int = 3

_PRECISION = 6

#: ``(ticker, fundamentals, price) -> market cap`` in the price's units.
MarketCap = Callable[[str, PointInTimeFinancials | None, float | None], float | None]


def thesis_break(
    fin: PointInTimeFinancials,
    *,
    cache: EdgarCache,
    as_of: date,
    max_de: float = DEFAULT_MAX_DE,
) -> str | None:
    """Why the entry thesis no longer holds on the filings, or None."""
    if fin.total_equity is not None and fin.total_equity <= 0:
        return (
            f"thesis broken: book equity ${fin.total_equity:,.0f} "
            f"is no longer positive"
        )
    de = debt_to_equity(fin)
    # A millionth: 1.05bn / 1.5bn is 0.7000000000000001, not over 0.7.
    if de is not None and round(de, _PRECISION) > max_de:
        return (
            f"thesis broken: balance sheet deteriorated, D/E {de:.2f} above "
            f"the {max_de} the entry screen allows"
        )

    records = historical_fcf(cache, fin.ticker, as_of)
    if len(records) < MIN_FCF_YEARS:
        return None  # a financial, or too short a record: unknown
    latest = records[-1]
    if latest.fcf <= 0:
        return (
            f"thesis broken: free cash flow negative in FY{latest.fiscal_year} "
            f"(${latest.fcf:,.0f}); the conservative DCF can no longer value "
            f"the business"
        )
    avg = average_fcf(records)
    if avg is not None and avg <= 0:
        return (
            f"thesis broken: {len(records)}-year average free cash flow "
            f"${avg:,.0f} is not positive"
        )
    growth = trailing_growth_pct(records)
    if growth is not None and growth < DEFAULT_MIN_GROWTH_PCT:
        return (
            f"thesis broken: free cash flow shrinking {abs(growth):.1f}%/yr, "
            f"steeper than the {abs(DEFAULT_MIN_GROWTH_PCT):.0f}%/yr the "
            f"conservative DCF will project"
        )
    return None


def decide(
    position: HeldPosition,
    fin: PointInTimeFinancials | None,
    price: float | None,
    market_cap: float | None,
    *,
    cache: EdgarCache,
    as_of: date,
    max_de: float = DEFAULT_MAX_DE,
) -> ExitVerdict | None:
    """Keep or sell one held name, or None when it cannot be judged."""
    ticker = position.ticker
    if fin is None or price is None or price <= 0:
        return None

    broken = thesis_break(fin, cache=cache, as_of=as_of, max_de=max_de)
    if broken is not None:
        return ExitVerdict(ticker, sell=True, reason=broken)

    iv = intrinsic_value(cache, ticker, as_of)
    if iv is None or iv.intrinsic_value_usd <= 0 or market_cap is None or market_cap <= 0:
        logger.info(f"{ticker} — no intrinsic value or market cap; no exit verdict")
        return None

    pct = round(100.0 * market_cap / iv.intrinsic_value_usd, _PRECISION)
    per_share = iv.intrinsic_value_usd * price / market_cap
    if pct >= COMPLETE_EXIT_PCT:
        return ExitVerdict(
            ticker,
            sell=True,
            reason=(
                f"price approached intrinsic value: ${price:,.2f} is "
                f"{pct:.1f}% of ${per_share:,.2f}/share (conservative DCF), "
                f"at or past the {COMPLETE_EXIT_PCT:.0f}% line where the exit "
                f"is complete"
            ),
        )
    value = (
        f"price ${price:,.2f} is {pct:.1f}% of intrinsic value "
        f"${per_share:,.2f}/share"
    )
    if pct >= SCALE_OUT_START_PCT:
        return ExitVerdict(
            ticker,
            sell=False,
            reason=(
                f"scale-out zone: {value} (conservative DCF), between the "
                f"{SCALE_OUT_START_PCT:.0f}% line where scaling out begins and "
                f"the {COMPLETE_EXIT_PCT:.0f}% line where it is complete; the "
                f"playbook's 25-50% trim is not executed (a verdict is keep or "
                f"sell), the position is kept"
            ),
        )
    return ExitVerdict(
        ticker,
        sell=False,
        reason=(
            f"thesis intact; {value} (conservative DCF, margin of safety "
            f"{100.0 - pct:.1f}%), under the {SCALE_OUT_START_PCT:.0f}% line "
            f"where scaling out begins"
        ),
    )


def verdicts(
    held: Mapping[str, HeldPosition] | None,
    prices: PriceLookup,
    fundamentals: FundamentalsLookup,
    *,
    cache: EdgarCache,
    market_cap: MarketCap,
    as_of: date,
    max_de: float = DEFAULT_MAX_DE,
) -> list[ExitVerdict]:
    """A verdict for every held name that can be judged, by ticker order.

    Price and fundamentals come from the same lookups the screen reads,
    so a held name is judged on the day's quote, never on a stale mark.
    """
    if not held:
        return []
    out: list[ExitVerdict] = []
    for ticker in sorted(held):
        fin = fundamentals.get(ticker)
        price = prices.get(ticker)
        verdict = decide(
            held[ticker],
            fin,
            price,
            market_cap(ticker, fin, price),
            cache=cache,
            as_of=as_of,
            max_de=max_de,
        )
        if verdict is None:
            logger.info(f"{as_of}: {ticker} held — no Klarman exit verdict (data)")
            continue
        out.append(verdict)
    if out:
        sells = sum(1 for v in out if v.sell)
        logger.info(
            f"{as_of}: Klarman exits on {len(held)} held: "
            f"{len(out) - sells} keep, {sells} sell, "
            f"{len(held) - len(out)} no verdict"
        )
    return out


__all__ = [
    "COMPLETE_EXIT_PCT",
    "MIN_FCF_YEARS",
    "SCALE_OUT_START_PCT",
    "MarketCap",
    "decide",
    "thesis_break",
    "verdicts",
]
