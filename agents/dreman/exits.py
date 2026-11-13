"""When Dreman sells a name he holds — and when he keeps it.

The problem this fixes (audit SF-X1, item 13h)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Playbook §7.1: the contrarian thesis *is* the gap between a bottom-
quintile multiple and the market's, so it is complete when the gap
closes — begin trimming at 80% of the market P/E, out by 100% of it.
§6.5 adds a hard stop: if the fundamental tests break, exit
immediately. None of it ran. ``DavidDreman.select`` ignored ``held``,
and the engine sold a Dreman name only because it left the day's target
list: 53 sales since 2026-08-05, every one "left today's target list",
none citing a P/E.

What this module decides
~~~~~~~~~~~~~~~~~~~~~~~~

One :class:`~core.live.doctrine_exits.ExitVerdict` per held name.
Dreman's exits run in shadow
(:data:`core.live.doctrine_exits.DOCTRINE_EXIT_MODES`): the runner
journals each verdict beside what the engine did; no weight changes.
The market is the one the day's screen measured — the median P/E of the
population the quintile cuts are taken from
(:class:`agents.dreman.ranking.MarketPE`) — and the held name's P/E is
computed by the screen's own :func:`~agents.dreman.filters.pe_ratio`,
so a name is judged by the doctrine that bought it.

Checked in this order:

* **Trigger 2 — fundamental deterioration (§6.5's hard stop)**, first
  because it fires "regardless of price — exit at any cost". The tests
  §7.1 lists, each on its own (one break is enough):

  - current ratio below 1.0 (:func:`agents.dreman.strength.current_ratio`);
  - D/E above the strategy's maximum (test 4's "maximum acceptable",
    the entry gate's ``max_de``, 1.0 by default). §7.1 says "above
    industry average"; no industry D/E is computed, and a D/E past the
    absolute maximum breaks test 4 under any reading;
  - ROE down two years running: three consecutive fiscal years from
    the 10-Ks as filed, each year's net income over that year-end's
    book (:func:`annual_roe_history`);
  - pre-tax margin collapsing: more than the strength battery's
    :data:`~agents.dreman.strength.DEFAULT_MAX_MARGIN_EROSION_PP` points
    lost over its five-year window
    (:func:`agents.dreman.strength.margin_erosion_pp`) — the playbook
    gives "collapsing" no number, and this is the only one the agent
    has for a margin trend.

  A test that cannot be computed is unjudged, never broken: absence of
  a tag is not evidence (audit FUND-4). Each keep says how many of the
  four were judged.

* **Trigger 1 — the multiple reverted.** At or past
  :data:`COMPLETE_EXIT_PCT` (100%) of the market P/E the gap is closed:
  sell. From :data:`TRIM_START_PCT` (80%) up to that line the playbook
  trims; a verdict is keep or sell, so the zone is a keep whose reason
  names the trim.

* **Retention.** Anything else is kept — on or off today's list. A
  falling price only widens the gap (§7.2: downturns "create more
  contrarian opportunities"; §7.3: "do not capitulate at extremes"),
  and dropping out of the bottom quintile while still under 80% of the
  market is not a §7.1 trigger.

Not decided here:

* **The trim itself.** :class:`ExitVerdict` is keep or sell.
* **Accounting irregularities** (trigger 2's last item) — no data.
* **Trigger 3 — a better contrarian candidate** (one more qualifying
  metric). It weighs a candidate against a holding, and on a 25-name
  book a 3-of-4 candidate outside the book exists nearly every day; it
  is the rank rotation retention exists to stop. The owner's call.
* **Trigger 4 — acquired or going private** — no deal data.
* **§6.5's add of 25% after a 15% drop** — an add, not an exit.

When no hard-stop test broke and a verdict still cannot be reached
honestly — no fundamentals, no price, earnings no longer positive (P/E
undefined), or no market P/E for the day — no verdict is given for that
name. A keep on no evidence would, once enforced, hold the name
indefinitely; silence leaves it to the engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

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

from .filters import DEFAULT_MAX_DE, debt_to_equity, pe_ratio
from .ranking import MarketPE
from .strength import (
    DEFAULT_MAX_MARGIN_EROSION_PP,
    DEFAULT_TREND_YEARS,
    current_ratio,
    margin_erosion_pp,
)

logger = get_logger("agents.dreman.exits")

#: §7.1 trigger 1: "Begin trimming at 80% of market average P/E".
TRIM_START_PCT: float = 80.0

#: §7.1 trigger 1: "Full exit by 100% of market average P/E".
COMPLETE_EXIT_PCT: float = 100.0

#: §7.1 trigger 2: "Current ratio drops below 1.0".
HARD_STOP_MIN_CURRENT_RATIO: float = 1.0

#: §7.1 trigger 2: "ROE declines for 2+ consecutive years" — two
#: declines need three consecutive fiscal years.
ROE_DECLINE_YEARS: int = 2

#: A full fiscal year, as :mod:`core.data.fundamentals_fetcher` reads
#: flow concepts: a 10-K can also tag a quarter.
_ANNUAL_DAYS: tuple[int, int] = (330, 400)

#: Two fiscal years are consecutive only when their period ends are
#: about a year apart; a skipped year is not "consecutive".
_CONSECUTIVE_YEAR_DAYS: tuple[int, int] = (335, 395)

_NET_INCOME_CONCEPTS: tuple[str, ...] = ("NetIncomeLoss", "ProfitLoss")
_EQUITY_CONCEPTS: tuple[str, ...] = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)

_PRECISION = 6


@dataclass(frozen=True)
class AnnualRoe:
    """One fiscal year's net income over that year-end's book, as filed."""

    net_income: XbrlFact
    equity: XbrlFact

    @property
    def roe_pct(self) -> float:
        return 100.0 * self.net_income.value / self.equity.value

    @property
    def period_end(self) -> date:
        return self.net_income.period_end


def _annual_net_income(
    cache: EdgarCache, ticker: str, concept: str, as_of: date
) -> XbrlFact | None:
    return cache.latest_value_at(
        ticker,
        concept,
        as_of,
        forms=("10-K",),
        duration_days=_ANNUAL_DAYS,
        units=("USD",),
    )


def _year_end_equity(
    cache: EdgarCache, ticker: str, income: XbrlFact
) -> XbrlFact | None:
    """The book at the close of ``income``'s fiscal year, from a 10-K
    known when that income was filed; None unless the period ends match."""
    for concept in _EQUITY_CONCEPTS:
        fact = cache.latest_value_at(
            ticker, concept, income.filed, forms=("10-K",), units=("USD",)
        )
        if fact is not None and fact.period_end == income.period_end:
            return fact
    return None


def annual_roe_history(
    cache: EdgarCache,
    ticker: str,
    as_of: date,
    *,
    years: int = ROE_DECLINE_YEARS + 1,
) -> list[AnnualRoe] | None:
    """The newest ``years`` consecutive fiscal-year ROEs known on
    ``as_of``, oldest first; None when any is missing, a book is not
    positive, or the years are not consecutive.

    The freshest net-income concept in the chain is chosen on the latest
    10-K and kept for the earlier years. Each earlier year is read as
    originally filed — from the 10-K before the one after it — not from
    a later 10-K's restated comparative, the same reading as
    :func:`agents.neff.exits.latest_sales_change`.
    """
    latest: tuple[str, XbrlFact] | None = None
    for concept in _NET_INCOME_CONCEPTS:
        fact = _annual_net_income(cache, ticker, concept, as_of)
        if fact is not None and (
            latest is None or fact.period_end > latest[1].period_end
        ):
            latest = (concept, fact)
    if latest is None:
        return None
    concept, income = latest

    out: list[AnnualRoe] = []
    lo, hi = _CONSECUTIVE_YEAR_DAYS
    while True:
        equity = _year_end_equity(cache, ticker, income)
        if equity is None or equity.value <= 0:
            return None
        out.append(AnnualRoe(net_income=income, equity=equity))
        if len(out) == years:
            break
        prior = _annual_net_income(
            cache, ticker, concept, income.filed - timedelta(days=1)
        )
        if prior is None:
            return None
        if not lo <= (income.period_end - prior.period_end).days <= hi:
            return None
        income = prior
    out.reverse()
    return out


def _roe_declining(history: list[AnnualRoe]) -> bool:
    rounded = [round(r.roe_pct, _PRECISION) for r in history]
    return all(later < earlier for earlier, later in pairwise(rounded))


@dataclass(frozen=True)
class HardStop:
    """§7.1 trigger 2 over one held name: the first test that broke, or
    None, and how many of the four could be judged."""

    broke: str | None
    judged: int


def hard_stop(
    fin: PointInTimeFinancials,
    *,
    cache: EdgarCache | None,
    as_of: date,
    max_de: float = DEFAULT_MAX_DE,
) -> HardStop:
    """Run the four computable hard-stop tests; report the first break."""
    ticker = fin.ticker
    judged = 0
    broke: str | None = None

    def _note(reason: str) -> None:
        nonlocal broke
        if broke is None:
            broke = reason

    cr = current_ratio(fin)
    if cr is not None:
        judged += 1
        if round(cr, _PRECISION) < HARD_STOP_MIN_CURRENT_RATIO:
            _note(f"current ratio {cr:.2f} below {HARD_STOP_MIN_CURRENT_RATIO}")

    de = debt_to_equity(fin)
    if de is not None:
        judged += 1
        if round(de, _PRECISION) > max_de:
            _note(f"D/E {de:.2f} above the {max_de} maximum")

    if cache is not None:
        history = annual_roe_history(cache, ticker, as_of)
        if history is not None:
            judged += 1
            if _roe_declining(history):
                steps = " -> ".join(
                    f"{r.roe_pct:.1f}% (FY to {r.period_end.isoformat()})"
                    for r in history
                )
                _note(f"ROE fell two years running, {steps}")
        erosion = margin_erosion_pp(cache, ticker, as_of)
        if erosion is not None:
            judged += 1
            if round(erosion, _PRECISION) > DEFAULT_MAX_MARGIN_EROSION_PP:
                _note(
                    f"pre-tax margin collapsing, down {erosion:.1f} points "
                    f"over {DEFAULT_TREND_YEARS} years"
                )

    return HardStop(broke=broke, judged=judged)


def decide(
    position: HeldPosition,
    fin: PointInTimeFinancials | None,
    price: float | None,
    market: MarketPE | None,
    *,
    cache: EdgarCache | None,
    as_of: date,
    max_de: float = DEFAULT_MAX_DE,
) -> ExitVerdict | None:
    """Keep or sell one held name, or None when it cannot be judged."""
    ticker = position.ticker
    if fin is None or price is None or price <= 0:
        return None

    stop = hard_stop(fin, cache=cache, as_of=as_of, max_de=max_de)
    if stop.broke is not None:
        return ExitVerdict(
            ticker,
            sell=True,
            reason=f"fundamental deterioration (hard stop): {stop.broke}",
        )

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
                f"P/E reached the market average: {multiple}, at or past "
                f"the {COMPLETE_EXIT_PCT:.0f}% line where the exit is complete"
            ),
        )

    tests = f"hard-stop tests: {stop.judged} of 4 judged, none broke"
    if pct >= TRIM_START_PCT:
        return ExitVerdict(
            ticker,
            sell=False,
            reason=(
                f"trim zone: {multiple}, between the {TRIM_START_PCT:.0f}% "
                f"line where trimming begins and the "
                f"{COMPLETE_EXIT_PCT:.0f}% line where the exit is complete; "
                f"the trim is not executed (a verdict is keep or sell), the "
                f"position is kept; {tests}"
            ),
        )
    return ExitVerdict(
        ticker,
        sell=False,
        reason=(
            f"contrarian gap still open: {multiple}, under the "
            f"{TRIM_START_PCT:.0f}% line where trimming begins; {tests}"
        ),
    )


def verdicts(
    held: Mapping[str, HeldPosition] | None,
    prices: PriceLookup,
    fundamentals: FundamentalsLookup,
    market: MarketPE | None,
    *,
    cache: EdgarCache | None,
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
        verdict = decide(
            held[ticker],
            fundamentals.get(ticker),
            prices.get(ticker),
            market,
            cache=cache,
            as_of=as_of,
            max_de=max_de,
        )
        if verdict is None:
            logger.info(f"{as_of}: {ticker} held — no Dreman exit verdict (data)")
            continue
        out.append(verdict)
    if out:
        sells = sum(1 for v in out if v.sell)
        logger.info(
            f"{as_of}: Dreman exits on {len(held)} held: "
            f"{len(out) - sells} keep, {sells} sell, "
            f"{len(held) - len(out)} no verdict"
        )
    return out


__all__ = [
    "COMPLETE_EXIT_PCT",
    "HARD_STOP_MIN_CURRENT_RATIO",
    "ROE_DECLINE_YEARS",
    "TRIM_START_PCT",
    "AnnualRoe",
    "HardStop",
    "annual_roe_history",
    "decide",
    "hard_stop",
    "verdicts",
]
