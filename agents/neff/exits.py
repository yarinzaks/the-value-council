"""When Neff sells a name he holds — and when he keeps it.

The problem this fixes (audit SF-X1, item 13g)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Playbook §7: Neff sold "when the framework no longer applied" — trim
as the P/E climbs to 80% of the market's, out by 100% of it — and §6.2
expects a name to be held two to four years while the multiple mean-
reverts. None of it ran. ``JohnNeff.select`` ignored ``held``, and the
engine sold a Neff name only because it left the day's target list:
53 sales since 2026-08-05, median hold 33 days, none longer than a year.

What this module decides
~~~~~~~~~~~~~~~~~~~~~~~~

One :class:`~core.live.doctrine_exits.ExitVerdict` per held name.
Neff's exits run in shadow
(:data:`core.live.doctrine_exits.DOCTRINE_EXIT_MODES`): the runner
journals each verdict beside what the engine did; no weight changes.
The market is measured the way the entry screen measures it — the same
P/E, dividend-yield and growth helpers (:mod:`agents.neff.filters`) and
the same universe-wide medians (:class:`agents.neff.ranking.MarketBenchmarks`)
— so a name is judged by the doctrine that bought it.

Checked in this order:

* **Trigger 3 — fundamental deterioration**, first because it sells
  "at any price": revenue in the latest 10-K below the 10-K a year
  before ("sales growth turning negative").

* **Trigger 1 — P/E expanded to the market's.** At or past
  :data:`COMPLETE_EXIT_PCT` (100%) of the market P/E the cheap-multiple
  thesis is spent: sell. From :data:`TRIM_START_PCT` (80%) up to that
  line the playbook trims; a verdict is keep or sell, so the zone is a
  keep whose reason names the trim.

* **Trigger 2 — Total-Return/PE below 1× the market's.** The signature
  metric has fallen out of the qualifying range (a dividend cut, slower
  growth or a richer multiple): sell. Unmeasurable (no 4-year EPS
  growth or no yield) is unknown, never a breach.

* **Retention.** Anything else is kept — on or off today's list. A
  falling price only cheapens the multiple (§7.2: downturns "create
  more buying opportunities"), and a holding period of any length is
  never a trigger: two to four years is what §6.2 *expects* the
  multiple to need, not a deadline. Each keep says how long the name
  has been held against that expectation.

Not decided here:

* **The trim itself.** :class:`ExitVerdict` is keep or sell.
* **Trigger 3's other two tests.** "Earnings declining for 2+ quarters"
  needs a quarterly EPS series the cache does not carry (the entry
  screen's criterion 6 is a neutral 5 points for the same reason), and
  "ROE below industry average for 2+ consecutive years" needs an
  industry ROE history.
* **Trigger 4 — a better opportunity.** It weighs a held name against
  a candidate; a verdict is about one held name.
* **§6.3's "buy more on the way down".** An add, not an exit.

When a verdict cannot be reached honestly — no fundamentals, no price,
earnings no longer positive (P/E undefined), or no market P/E for the
day — and no deterioration fired on what *is* known, no verdict is
given for that name. A keep on no evidence would, once enforced, hold
the name indefinitely; silence leaves it to the engine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
)
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact
from core.live.doctrine_exits import ExitVerdict
from core.logger import get_logger

from .filters import (
    _REVENUE_CONCEPTS,
    dividend_yield,
    pe_ratio,
    total_return_to_pe,
    trailing_growth_pct,
)
from .ranking import MarketBenchmarks

logger = get_logger("agents.neff.exits")

#: §7.1 trigger 1: "Begin trimming at 80% of market average".
TRIM_START_PCT: float = 80.0

#: §7.1 trigger 1: "Full exit by 100% of market average".
COMPLETE_EXIT_PCT: float = 100.0

#: §7.1 trigger 2: "Total Return / PE drops below 1× market average".
MIN_TR_PE_MULTIPLE_OF_MARKET: float = 1.0

#: §6.2: "Default expected holding period: 2-4 years". Reported on a
#: keep; never a trigger in either direction.
EXPECTED_HOLD_YEARS: tuple[int, int] = (2, 4)

#: A full fiscal year, as :mod:`core.data.fundamentals_fetcher` reads
#: flow concepts: a 10-K can also tag a quarter.
_ANNUAL_DAYS: tuple[int, int] = (330, 400)

#: Two annual revenues compare as consecutive years only when their
#: period ends are about a year apart; a skipped year is not "turning".
_CONSECUTIVE_YEAR_DAYS: tuple[int, int] = (335, 395)

_PRECISION = 6

#: ``(ticker, fundamentals, price) -> market cap`` in the price's units.
MarketCap = Callable[[str, PointInTimeFinancials | None, float | None], float | None]


@dataclass(frozen=True)
class SalesChange:
    """Annual revenue in the latest 10-K against the 10-K a year before."""

    concept: str
    latest: XbrlFact
    prior: XbrlFact

    @property
    def growth_pct(self) -> float:
        return 100.0 * (self.latest.value / self.prior.value - 1.0)


def latest_sales_change(
    cache: EdgarCache, ticker: str, as_of: date
) -> SalesChange | None:
    """The newest full-year revenue known on ``as_of`` against the one
    before it, in the same concept and USD; None when either is missing,
    the prior is not positive, or the two are not consecutive years.

    The freshest concept in the chain wins, as in
    :mod:`core.data.fundamentals_fetcher`: a filer that moved from
    ``SalesRevenueNet`` to the ASC 606 tag is read on the tag it uses now.
    The prior year is the figure as originally filed — the 10-K before
    the latest one — not the latest 10-K's restated comparative.
    """
    best: tuple[str, XbrlFact] | None = None
    for concept in _REVENUE_CONCEPTS:
        fact = cache.latest_value_at(
            ticker,
            concept,
            as_of,
            forms=("10-K",),
            duration_days=_ANNUAL_DAYS,
            units=("USD",),
        )
        if fact is not None and (best is None or fact.period_end > best[1].period_end):
            best = (concept, fact)
    if best is None:
        return None
    concept, latest = best
    prior = cache.latest_value_at(
        ticker,
        concept,
        latest.filed - timedelta(days=1),
        forms=("10-K",),
        duration_days=_ANNUAL_DAYS,
        units=("USD",),
    )
    if prior is None or prior.value <= 0:
        return None
    gap = (latest.period_end - prior.period_end).days
    lo, hi = _CONSECUTIVE_YEAR_DAYS
    if not lo <= gap <= hi:
        return None
    return SalesChange(concept=concept, latest=latest, prior=prior)


def deterioration(
    ticker: str, *, cache: EdgarCache, as_of: date
) -> str | None:
    """Why the fundamentals broke (§7.1 trigger 3), or None."""
    sales = latest_sales_change(cache, ticker, as_of)
    if sales is not None and sales.latest.value < sales.prior.value:
        return (
            f"fundamental deterioration: sales growth turned negative, "
            f"revenue ${sales.latest.value:,.0f} in the year to "
            f"{sales.latest.period_end.isoformat()} against "
            f"${sales.prior.value:,.0f} the year before "
            f"({sales.growth_pct:+.1f}%)"
        )
    return None


def _held_for(position: HeldPosition, as_of: date) -> str:
    days = (as_of - position.entry_date).days
    lo, hi = EXPECTED_HOLD_YEARS
    return f"held {days} days of an expected {lo}-{hi} years"


def decide(
    position: HeldPosition,
    fin: PointInTimeFinancials | None,
    price: float | None,
    market_cap: float | None,
    market: MarketBenchmarks | None,
    *,
    cache: EdgarCache,
    as_of: date,
) -> ExitVerdict | None:
    """Keep or sell one held name, or None when it cannot be judged."""
    ticker = position.ticker
    # Trigger 3 reads only the cached 10-K revenue, so it runs before
    # the fundamentals/price guard: a missing quote must not hide it.
    broken = deterioration(ticker, cache=cache, as_of=as_of)
    if broken is not None:
        return ExitVerdict(ticker, sell=True, reason=broken)

    if fin is None or price is None or price <= 0:
        return None

    pe = pe_ratio(price, fin)
    if pe is None:
        logger.info(f"{ticker} — earnings not positive, P/E undefined; no exit verdict")
        return None
    if market is None or market.median_pe is None or market.median_pe <= 0:
        logger.info(f"{ticker} — no market P/E today; no exit verdict")
        return None

    pct = round(100.0 * pe / market.median_pe, _PRECISION)
    multiple = (
        f"P/E {pe:.2f} is {pct:.1f}% of the market's {market.median_pe:.2f} "
        f"(median of the day's screen)"
    )
    if pct >= COMPLETE_EXIT_PCT:
        return ExitVerdict(
            ticker,
            sell=True,
            reason=(
                f"P/E expanded to the market average: {multiple}, at or past "
                f"the {COMPLETE_EXIT_PCT:.0f}% line where the exit is complete"
            ),
        )

    yld = dividend_yield(market_cap, fin)
    eps_growth = trailing_growth_pct(cache, ticker, as_of, metric="eps")
    tr_pe = total_return_to_pe(
        eps_growth, yld * 100.0 if yld is not None else None, pe
    )
    market_tr_pe = market.median_tr_pe
    if tr_pe is not None and market_tr_pe is not None and market_tr_pe > 0:
        floor = market_tr_pe * MIN_TR_PE_MULTIPLE_OF_MARKET
        if round(tr_pe, _PRECISION) < round(floor, _PRECISION):
            return ExitVerdict(
                ticker,
                sell=True,
                reason=(
                    f"Total-Return/PE {tr_pe:.3f} fell below "
                    f"{MIN_TR_PE_MULTIPLE_OF_MARKET:g}x the market's "
                    f"{market_tr_pe:.3f} (EPS growth {eps_growth or 0.0:.1f}% "
                    f"+ yield {(yld or 0.0) * 100.0:.1f}%, over P/E {pe:.2f})"
                ),
            )
        signature = f"Total-Return/PE {tr_pe:.3f} against the market's {market_tr_pe:.3f}"
    else:
        signature = "Total-Return/PE not measurable"

    held = _held_for(position, as_of)
    if pct >= TRIM_START_PCT:
        return ExitVerdict(
            ticker,
            sell=False,
            reason=(
                f"trim zone: {multiple}, between the {TRIM_START_PCT:.0f}% "
                f"line where trimming begins and the "
                f"{COMPLETE_EXIT_PCT:.0f}% line where the exit is complete; "
                f"the trim is not executed (a verdict is keep or sell), the "
                f"position is kept; {signature}; {held}"
            ),
        )
    return ExitVerdict(
        ticker,
        sell=False,
        reason=(
            f"framework still applies: {multiple}, under the "
            f"{TRIM_START_PCT:.0f}% line where trimming begins; {signature}; "
            f"{held}"
        ),
    )


def verdicts(
    held: Mapping[str, HeldPosition] | None,
    prices: PriceLookup,
    fundamentals: FundamentalsLookup,
    market: MarketBenchmarks | None,
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
            market,
            cache=cache,
            as_of=as_of,
        )
        if verdict is None:
            logger.info(f"{as_of}: {ticker} held — no Neff exit verdict (data)")
            continue
        out.append(verdict)
    if out:
        sells = sum(1 for v in out if v.sell)
        logger.info(
            f"{as_of}: Neff exits on {len(held)} held: "
            f"{len(out) - sells} keep, {sells} sell, "
            f"{len(held) - len(out)} no verdict"
        )
    return out


__all__ = [
    "COMPLETE_EXIT_PCT",
    "EXPECTED_HOLD_YEARS",
    "MIN_TR_PE_MULTIPLE_OF_MARKET",
    "TRIM_START_PCT",
    "MarketCap",
    "SalesChange",
    "decide",
    "deterioration",
    "latest_sales_change",
    "verdicts",
]
