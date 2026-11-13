"""When Buffett sells a name he holds — and, far more often, when he keeps it.

The problem this fixes (audit SF-X1, item 13d)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Playbook §7: "favorite holding period is forever", with four
legitimate sell triggers and a list of things that never are one — a
price decline of any size, a bad quarter, reaching fair value. None of
it ran. ``WarrenBuffett.select`` ignored ``held``, and the engine sold a
Buffett name only because it left the day's target list: all 18 of the
book's distinct sales were held under a year, and SFM went on
2026-10-29 at -42.8% only to be bought back on 11-03.

What this module decides
~~~~~~~~~~~~~~~~~~~~~~~~

One :class:`~core.live.doctrine_exits.ExitVerdict` per held name.
Buffett's exits run in shadow
(:data:`core.live.doctrine_exits.DOCTRINE_EXIT_MODES`): the runner
journals each verdict beside what the engine did; no weight changes.
Each trigger is measured the way the entry screen measures the same
quality, so a name is judged by the doctrine that bought it.

Checked in this order:

* **Trigger 1 — moat erosion, the primary trigger.** The franchise
  record no longer shows a durable advantage:
  :func:`agents.buffett.moat.assess_franchise`, the entry screen's own
  moat test (ROE >= 15% in at least 80% of up to ten fiscal years),
  now fails. A short record (fewer than its five years, or a financial,
  where the test does not apply) is *unknown*, not eroded.
* **Trigger 2 — management failure.** The entry screen's proxy for
  capable management (§4.1 #4, :func:`agents.buffett.filters.has_consistent_ocf`)
  is operating cash flow positive every year: a business reporting
  profits while its cash flow is negative is a quality-of-earnings flag.
  A *reported* negative 10-K operating cash flow among the newest
  :data:`MANAGEMENT_OCF_YEARS` fiscal years sells. A missing year is
  not a negative one (audit FUND-4).
* **Trigger 4 — extreme overvaluation.** Market cap above
  :data:`EXTREME_OVERVALUATION_MULTIPLE` times the owner-earnings DCF
  intrinsic value (:func:`agents.buffett.owner_earnings.intrinsic_value`,
  the value the screen ranks by). "Exceeds" is strict: 1.5x is kept.
* **Retention.** Anything else is kept — on or off today's list.
  Reaching fair value is not a sell (§7.2, Buffett's departure from
  Graham); neither is a price fall of any size, nor slipping in rank.

Not decided here:

* **Trigger 3 — a much better opportunity.** It weighs a held name
  against a candidate's conviction; a verdict is about one held name.
* **Persistence of the overvaluation gap.** §7.1 asks that the gap
  "appears persistent"; one day's quote is all a verdict sees, so a
  single reading above the line is a sell verdict. The journal keeps
  each day's, which is how persistence can be read before this is
  enforced.
* **Moat and management judged from text** (a brand losing pricing
  power, dishonesty, a bad acquisition): the LLM memo's territory, not
  the filings'.

When a verdict cannot be reached honestly — no fundamentals, no fresh
price, no market cap, no intrinsic value, or too short a record to call
the moat either way — and no trigger fired on what *is* known, no
verdict is given for that name. A keep on no evidence would, once
enforced, hold the name indefinitely; silence leaves it to the engine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, timedelta

from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
)
from core.data.edgar_cache import EdgarCache
from core.live.doctrine_exits import ExitVerdict
from core.logger import get_logger

from .filters import _OCF_CONCEPTS, DEFAULT_OCF_HISTORY_YEARS
from .moat import DEFAULT_MIN_YEARS, assess_franchise
from .owner_earnings import intrinsic_value

logger = get_logger("agents.buffett.exits")

#: §7.1 trigger 4: "Price exceeds 1.5x intrinsic value".
EXTREME_OVERVALUATION_MULTIPLE: float = 1.5

#: Trigger 2's window: the same five years the entry screen's
#: management proxy reads (§4.1 #4).
MANAGEMENT_OCF_YEARS: int = DEFAULT_OCF_HISTORY_YEARS

_PRECISION = 6

#: ``(ticker, fundamentals, price) -> market cap`` in the price's units.
MarketCap = Callable[[str, PointInTimeFinancials | None, float | None], float | None]


def negative_ocf_year(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    years: int = MANAGEMENT_OCF_YEARS,
) -> tuple[int, float] | None:
    """``(fiscal_year, ocf)`` of the newest negative annual operating cash
    flow among the newest ``years`` fiscal years reported by ``as_of``.

    None when every reported year is positive — including when fewer
    than ``years`` are reported: an absent year is not a negative one.
    """
    seen: list[int] = []
    for i in range(years + 3):  # slack for years the cache is missing
        lookup = as_of - timedelta(days=int(365.25 * i))
        fact = None
        for concept in _OCF_CONCEPTS:
            fact = cache.latest_value_at(
                ticker, concept, lookup, forms=("10-K",), prefer_annual=True
            )
            if fact is not None:
                break
        if fact is None or fact.fiscal_year is None:
            continue
        fy = int(fact.fiscal_year)
        if fy in seen:
            continue
        if seen and fy <= seen[0] - years:
            break  # older than the window the newest year opens
        seen.append(fy)
        if fact.value < 0:
            return fy, float(fact.value)
        if len(seen) >= years:
            break
    return None


def decide(
    position: HeldPosition,
    fin: PointInTimeFinancials | None,
    price: float | None,
    market_cap: float | None,
    *,
    cache: EdgarCache,
    as_of: date,
) -> ExitVerdict | None:
    """Keep or sell one held name, or None when it cannot be judged."""
    ticker = position.ticker
    if fin is None or price is None or price <= 0:
        return None

    # Trigger 1 — moat erosion.
    franchise = assess_franchise(cache, ticker, as_of)
    moat_known = franchise.years_observed >= DEFAULT_MIN_YEARS
    if moat_known and not franchise.qualifies:
        return ExitVerdict(ticker, sell=True, reason=f"moat eroded: {franchise.reason}")

    # Trigger 2 — management failure, by the screen's own proxy.
    burn = negative_ocf_year(cache, ticker, as_of)
    if burn is not None:
        fy, ocf = burn
        return ExitVerdict(
            ticker,
            sell=True,
            reason=(
                f"management: operating cash flow negative in FY{fy} "
                f"(${ocf:,.0f}) while earnings were reported positive"
                if (fin.net_income or 0) > 0
                else f"management: operating cash flow negative in FY{fy} "
                f"(${ocf:,.0f})"
            ),
        )

    # Trigger 4 — extreme overvaluation.
    iv = intrinsic_value(cache, ticker, as_of)
    if iv is None or iv.intrinsic_value_usd <= 0 or market_cap is None or market_cap <= 0:
        logger.info(f"{ticker} — no intrinsic value or market cap; no exit verdict")
        return None
    # A millionth: 1.5x computed through floats is 1.5, not 1.5000000000000002.
    ratio = round(market_cap / iv.intrinsic_value_usd, _PRECISION)
    per_share = iv.intrinsic_value_usd * price / market_cap
    value = (
        f"price ${price:,.2f} is {ratio:.2f}x intrinsic value "
        f"${per_share:,.2f}/share"
    )
    if ratio > EXTREME_OVERVALUATION_MULTIPLE:
        return ExitVerdict(
            ticker,
            sell=True,
            reason=(
                f"extreme overvaluation: {value} (owner-earnings DCF), above "
                f"the {EXTREME_OVERVALUATION_MULTIPLE}x line"
            ),
        )

    if not moat_known:
        logger.info(
            f"{ticker} — moat not judgeable ({franchise.reason}); no exit verdict"
        )
        return None

    return ExitVerdict(
        ticker,
        sell=False,
        reason=(
            f"moat intact ({franchise.reason}); operating cash flow positive "
            f"in each of the last {MANAGEMENT_OCF_YEARS} reported years; "
            f"{value}, under the {EXTREME_OVERVALUATION_MULTIPLE}x line"
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
        )
        if verdict is None:
            logger.info(f"{as_of}: {ticker} held — no Buffett exit verdict (data)")
            continue
        out.append(verdict)
    if out:
        sells = sum(1 for v in out if v.sell)
        logger.info(
            f"{as_of}: Buffett exits on {len(held)} held: "
            f"{len(out) - sells} keep, {sells} sell, "
            f"{len(held) - len(out)} no verdict"
        )
    return out


__all__ = [
    "EXTREME_OVERVALUATION_MULTIPLE",
    "MANAGEMENT_OCF_YEARS",
    "MarketCap",
    "decide",
    "negative_ocf_year",
    "verdicts",
]
