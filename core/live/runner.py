"""Daily live-paper-trading runner.

Orchestrates one day for all configured agents:

1. Load each agent's persisted portfolio (or seed $10K if missing).
2. Resolve today's universe via the FullMarketUniverse.
3. Pre-fetch current prices (one yfinance call per ticker via the
   existing PriceDataLoader cache — fast on warm cache, slow on cold).
4. Mark every existing position to today's price.
5. Run the agent's strategy via its live adapter to produce target
   weights and a watchlist.
6. Diff target vs. current portfolio:
   * SELL positions that fell out of the target list (rotated out).
   * BUY new targets sized to ``target_weight × NAV``.
7. Persist updated portfolio JSON + log decisions to
   ``data/decisions/<agent>/<YYYY-MM-DD>.json``.

The runner records a decision every day, but it does not trade every
day. A name that leaves the target list is only sold once it has been
held past ``min_holding_days`` — see DEFAULT_MIN_HOLDING_DAYS. The
claim that "trade churn stays low" turned out to be false: the
decision logs carry 246 completed round-trips across the ten agents,
the shortest of them two days.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from agents.buffett import WarrenBuffett
from agents.buffett.moat_analyzer import MoatAnalyzer
from agents.council.cadence import (
    RunType,
    is_first_run_of_week,
    is_rebalance_run,
    permissions_for,
)
from agents.council.nav_history import drawdown_from_peak
from agents.council.published_list import PublishedList, executable_list, publish
from agents.council.regime import read_regime
from agents.council.strategy import MohnishPabrai
from agents.dreman.contrarian import DavidDreman
from agents.fisher import PhilipFisher
from agents.fisher.scuttlebutt import ScuttlebuttAnalyzer
from agents.graham.net_net import BenjaminGraham
from agents.greenblatt.magic_formula import MagicFormula
from agents.klarman import SethKlarman
from agents.klarman.downside import DownsideAnalyzer
from agents.lynch import PeterLynch
from agents.lynch.category_classifier import CategoryClassifier
from agents.market_core.strategy import MarketCore
from agents.marks import HowardMarks
from agents.marks.second_level import SecondLevelAnalyzer
from agents.neff.total_return import JohnNeff
from agents.schloss.deep_value import WalterSchloss
from core.backtest.data_loader import PriceDataLoader, _to_date
from core.backtest.decision_logger import DecisionLogger, make_decision
from core.backtest.full_market_universe import FullMarketUniverse
from core.backtest.point_in_time import PointInTimeFinancials, PointInTimeLoader
from core.backtest.strategy_runner import HeldPosition
from core.data.edgar_cache import EdgarCache
from core.data.fundamentals_fetcher import (
    CachedEdgarAdapter,
    FundamentalsFetcher,
    FundamentalsFetcherConfig,
)
from core.data.news_service import NewsService
from core.live.agent_adapter import (
    AgentAdapter,
    BuffettLive,
    DremanLive,
    FisherLive,
    GrahamLive,
    GrahamSecondCohortLive,
    GreenblattLive,
    KlarmanLive,
    LiveTarget,
    LiveWatch,
    LynchLive,
    MarketCoreLive,
    MarksLive,
    NeffLive,
    PabraiLive,
    ScanResult,
    SchlossLive,
)
from core.live.notify import notify_run
from core.live.portfolio import (
    DEFAULT_COST_BPS,
    DEFAULT_INITIAL_CASH,
    DEFAULT_PORTFOLIO_DIR,
    LivePortfolio,
    LivePortfolioError,
    Position,
    TradeRecord,
    WatchEntry,
    now_iso,
)
from core.live.price_export import export_prices
from core.live.sector_export import export_sectors
from core.live.snapshots import make_snapshot, save_snapshot
from core.live.trade_ledger import record_day
from core.llm.mode import LLMMode
from core.llm.wiring import build_analyst_layer
from core.logger import get_logger
from core.market_calendar import closes_early, why_closed
from core.paths import council_published_dir, trade_ledger_dir

logger = get_logger("core.live.runner")

#: Smallest notional worth executing. Below this the transaction cost
#: dominates and the position is noise in the weight table.
MIN_TRADE_USD: float = 1.0

#: How far a position may drift from its target weight before the
#: rebalance pass corrects it, as a fraction of the target. 0.25 means a
#: 3.7% target is left alone between 2.8% and 4.6%. Positions were
#: previously never resized after entry at all, so every sizing doctrine
#: was expressed once and then abandoned to price drift.
DEFAULT_REBALANCE_BAND: float = 0.25

#: Days a position is held before a rotation may sell it.
#:
#: Not a doctrine — a floor under one. Every investor here holds for
#: years; none of them sells because a name slipped a rank overnight.
#: The decision logs carry 246 completed round-trips, and the shortest
#: are two days.
#:
#: Thirty days is deliberately short. It is long enough that a rank
#: wobble resolves and the position is still there, and short enough
#: that a genuine collapse still exits within the month. A longer floor
#: would start overriding the agents' own exit rules, which is theirs
#: to decide, not the runner's.
DEFAULT_MIN_HOLDING_DAYS: int = 30


def pos_age_days(pos: Position, as_of: date) -> int | None:
    """Calendar days since entry, or None when the date is unusable.

    None means "cannot tell", and the caller treats that as no floor
    rather than as an infinite one — a position with a corrupt entry
    date should still be sellable.
    """
    try:
        entry = _to_date(pos.entry_date)
    except (ValueError, TypeError):
        return None
    return max(0, (as_of - entry).days)


def reachable_targets(
    targets: Sequence[LiveTarget],
    prices: dict[str, float | None],
    nav: float,
    held: frozenset[str] = frozenset(),
) -> list[LiveTarget]:
    """``targets`` reduced to the names this book can hold a share of.

    A $10,000 book divided 27 ways is a $370 slot, and one share of Eli
    Lilly is $810. The name is not expensive relative to its worth — it
    is expensive relative to the book, and no amount of conviction buys
    a third of a share from a broker that sells whole ones.

    The question is what happens to that $370. Leaving it in cash is
    one answer and it is the wrong one: the money was never earmarked
    for Lilly specifically, it was earmarked for *a position*, and an
    investor told "you cannot buy this one" puts it into the next name
    on the same list rather than under the mattress. So an unreachable
    name is dropped and the survivors' weights are scaled back up to
    the same invested total.

    Dropping is iterative and one name at a time, worst offender first,
    because dropping one widens every remaining slot and may bring a
    second name back within reach. Discarding the whole unaffordable
    set in one pass would throw away names the first removal had
    already fixed.

    Names already in ``held`` are never dropped. The cash is in them
    already, and re-deciding their affordability would sell a position
    over a price move — the exit path's business, not sizing's.

    A book with no reachable name at all returns an empty list and
    stays in cash. That is the honest outcome for a small book pointed
    at a list of expensive names, and the agents it happens to should
    show it rather than have it modelled away.
    """
    priced = [t for t in targets if (prices.get(t.ticker) or 0.0) > 0.0]
    invested = sum(t.weight for t in priced)
    if not priced or nav <= 0 or invested <= 0:
        return list(priced)

    kept = list(priced)
    while True:
        scale = invested / sum(t.weight for t in kept)
        # Worst offender: the biggest shortfall relative to its own
        # slot, so a name missing by tenfold goes before one missing by
        # a tenth.
        worst: tuple[float, LiveTarget, float] | None = None
        for t in kept:
            if t.ticker in held:
                continue
            price = prices[t.ticker] or 0.0
            slot = nav * t.weight * scale
            if slot <= 0 or price <= slot:
                continue
            ratio = price / slot
            if worst is None or ratio > worst[0]:
                worst = (ratio, t, slot)
        if worst is None:
            break
        _, dropped, slot = worst
        logger.info(
            f"{dropped.ticker} out of reach — one share is "
            f"${prices[dropped.ticker]:,.2f} against a ${slot:,.2f} slot; "
            f"its weight goes to the {len(kept) - 1} name(s) that fit"
        )
        kept = [t for t in kept if t.ticker != dropped.ticker]
        if not kept:
            return []

    scale = invested / sum(t.weight for t in kept)
    if scale == 1.0:
        return kept
    return [replace(t, weight=t.weight * scale) for t in kept]


def plan_whole_share_buys(
    targets: Sequence[LiveTarget],
    prices: dict[str, float | None],
    nav: float,
    cash: float,
    *,
    cost_bps: float,
    band: float,
) -> dict[str, int]:
    """How many whole shares of each target to buy, decided in one pass.

    Flooring each slot on its own strands up to one share's price per
    name. Across 27 names priced $37 to $331 against a $370 slot that is
    20.1% of the book in cash — measured, not estimated — and no
    investor here held a fifth of their book in cash by accident.

    So the remainders are pooled and spent, one share at a time, on
    whichever name is furthest below its slot in dollars. That is
    largest-remainder apportionment: the rule a person uses on the last
    $2,000, which is to top up whatever is most short.

    Decided here, in full, rather than executed as a second round of
    trades. A top-up pass that bought again would put two BUY rows on
    one position in one day — two ledger entries, two notification
    lines, two decision records — for a single decision to hold N
    shares. The caller gets one number per ticker and places one trade.

    ``band`` is the ceiling, as a fraction above the slot. A share that
    would carry a position past it is refused, because the next run's
    rebalance would only sell it back, and buying into a trim is worse
    than holding the cash. That ceiling is why the residue is
    irreducible rather than zero: a $331 share in a $370 slot cannot
    take a second one without reaching 179% of its target, so its $40
    stays in cash — visibly, which is the point.
    """
    per_share: dict[str, float] = {}
    for t in targets:
        price = prices.get(t.ticker) or 0.0
        if price > 0 and t.weight > 0:
            per_share[t.ticker] = price * (1.0 + cost_bps)
    if not per_share or nav <= 0 or cash <= 0:
        return {}

    slots = {t.ticker: nav * t.weight for t in targets if t.ticker in per_share}
    ceiling = {tk: slot * (1.0 + band) for tk, slot in slots.items()}

    shares: dict[str, int] = {}
    spent = 0.0
    for tk, slot in slots.items():
        n = int(min(slot, cash) // per_share[tk])
        shares[tk] = n
        spent += n * per_share[tk]

    # The pooled remainder, one share at a time, to the biggest gap.
    while True:
        remaining = cash - spent
        best: tuple[float, str] | None = None
        for tk, slot in slots.items():
            outlay = per_share[tk]
            if outlay > remaining:
                continue
            held_value = (shares[tk] + 1) * outlay
            if held_value > ceiling[tk]:
                continue
            gap = slot - shares[tk] * outlay
            if gap <= 0:
                continue
            if best is None or gap > best[0]:
                best = (gap, tk)
        if best is None:
            return {tk: n for tk, n in shares.items() if n > 0}
        shares[best[1]] += 1
        spent += per_share[best[1]]


def _parse_date(value: str) -> date | None:
    """An ISO date, or ``None`` when the field is empty or unreadable."""
    if not value:
        return None
    try:
        return _to_date(value)
    except (ValueError, TypeError):
        return None


def _derive_run_type(as_of: date, last_open_date: str) -> RunType:
    """Which run of §7 this invocation is, for one book.

    Per agent rather than per fleet, and from that book's own
    ``last_open_date``: the runner can be pointed at a single agent, and
    a book that missed a week has to get its council on the next run it
    sees rather than waiting for everyone else.

    An empty or unparseable field reads as "no previous run", which makes
    this a COUNCIL. That is the direction that keeps an agent working:
    the opposite reading would leave a book with one corrupt field
    unable to open a position ever again, silently.
    """
    try:
        previous: date | None = _to_date(last_open_date) if last_open_date else None
    except (ValueError, TypeError):
        previous = None
    return RunType.COUNCIL if is_first_run_of_week(as_of, previous) else RunType.HEARTBEAT


@dataclass
class AgentRunResult:
    """Per-agent summary returned by :meth:`DailyRunner.run`."""

    agent: str
    portfolio: LivePortfolio
    targets: list[LiveTarget] = field(default_factory=list)
    watchlist: list[LiveWatch] = field(default_factory=list)
    #: Tickers that were on the watch list when this run started.
    #:
    #: Captured because the run overwrites the stored list before
    #: anything reads it, and a notifier that reported the list rather
    #: than the change would send the same names every day. Tickers, not
    #: entries: the prices in the stored entries are the previous run's
    #: and nothing should be able to report them as today's.
    previous_watchlist: tuple[str, ...] = ()
    trades: list[TradeRecord] = field(default_factory=list)
    universe_size: int = 0
    error: str | None = None  # set if the run failed for this agent
    #: True when this agent had already completed ``as_of`` and the run
    #: was a no-op. Distinguishes "nothing to do" from "traded nothing".
    skipped: bool = False


def build_default_adapters(
    *,
    decision_logger: DecisionLogger,
    edgar_cache: EdgarCache | None = None,
    price_loader: PriceDataLoader | None = None,
    llm_mode: LLMMode = LLMMode.OFF,
) -> list[AgentAdapter]:
    """Construct the live adapters for the council's live books.

    Twelve adapters for eleven members: Graham appears twice, the second
    on a later start date, so the pair isolates entry timing. See
    :class:`~core.live.agent_adapter.GrahamSecondCohortLive`.

    The 4 original quant agents (Greenblatt, Schloss, Graham, Dreman)
    don't need ``edgar_cache`` (they read fundamentals via the runner's
    ``FundamentalsLookup``). The 6 hybrid agents (Neff, Buffett, Lynch,
    Marks, Klarman, Fisher) DO need it for trailing growth / FCF /
    margin-trend lookups, so we pass the runner's cache through.

    Schloss additionally takes the price loader: his entry condition is
    a 52-week low and a five-year high, which are price facts rather
    than filings.

    ``llm_mode`` decides whether the five hybrid agents get an analyzer
    at all. It defaults to ``OFF``, which is what every caller meant
    before there was a choice, and what a backtest must keep meaning —
    a model that has read the future is not analysis.

    ``SHADOW`` builds them and lets them write memos without touching
    the selection. Nothing here fails when the layer cannot be built:
    no API key is the normal state of a checkout, and a book that
    cannot reach the layer trades on its screen exactly as it did
    before there was one.
    """
    cache = edgar_cache or EdgarCache()
    prices = price_loader or PriceDataLoader()
    layer = build_analyst_layer(
        ("buffett", "lynch", "marks", "klarman", "fisher"), mode=llm_mode
    )
    # The mode the agents are told about is the layer's own, which is
    # OFF when it could not be built. Handing them SHADOW alongside a
    # None analyzer would be harmless but would log a lie.
    mode = layer.mode
    return [
        GreenblattLive(
            MagicFormula(
                portfolio_size=30,
                min_market_cap=500_000_000.0,
                decision_logger=decision_logger,
            )
        ),
        SchlossLive(
            WalterSchloss(
                portfolio_size=30,
                min_years_public=0,  # universe filter handles "established" already
                min_market_cap=500_000_000.0,
                # Schloss's entry condition — near the 52-week low, or
                # 50% off the five-year high. The rule was written and
                # tested behind a flag no caller passed.
                price_loader=prices,
                require_distressed_price=True,
                decision_logger=decision_logger,
            )
        ),
        GrahamLive(
            BenjaminGraham(
                portfolio_size=30,
                min_market_cap=500_000_000.0,
                decision_logger=decision_logger,
            )
        ),
        # The same rules on a later start date. Every parameter here is
        # copied deliberately rather than shared through a constant: a
        # later edit that tunes one Graham and not the other would make
        # the pair measure two things, and the whole point is that it
        # measures one. If you change a number above, change it here.
        GrahamSecondCohortLive(
            BenjaminGraham(
                portfolio_size=30,
                min_market_cap=500_000_000.0,
                decision_logger=decision_logger,
            )
        ),
        DremanLive(
            DavidDreman(
                portfolio_size=25,
                min_market_cap=500_000_000.0,
                # Playbook 4.2's strength battery reads multi-year
                # margin and earnings trends; without the cache it
                # cannot run and the agent buys on cheapness alone.
                edgar_cache=cache,
                decision_logger=decision_logger,
            )
        ),
        NeffLive(
            JohnNeff(
                edgar_cache=cache,
                portfolio_size=30,  # soft-scoring: top-30 by total_score
                min_market_cap=500_000_000.0,
                decision_logger=decision_logger,
            )
        ),
        BuffettLive(
            WarrenBuffett(
                edgar_cache=cache,
                portfolio_size=8,
                min_market_cap=5_000_000_000.0,
                moat_analyzer=layer.build("buffett", MoatAnalyzer),
                llm_mode=mode,
                decision_logger=decision_logger,
            )
        ),
        LynchLive(
            PeterLynch(
                edgar_cache=cache,
                portfolio_size=30,
                min_market_cap=300_000_000.0,
                category_classifier=layer.build("lynch", CategoryClassifier),
                llm_mode=mode,
                decision_logger=decision_logger,
            )
        ),
        MarksLive(
            HowardMarks(
                edgar_cache=cache,
                min_market_cap=500_000_000.0,
                second_level_analyzer=layer.build("marks", SecondLevelAnalyzer),
                llm_mode=mode,
                decision_logger=decision_logger,
            )
        ),
        KlarmanLive(
            SethKlarman(
                edgar_cache=cache,
                min_market_cap=500_000_000.0,
                max_portfolio_size=20,
                downside_analyzer=layer.build("klarman", DownsideAnalyzer),
                llm_mode=mode,
                decision_logger=decision_logger,
            )
        ),
        FisherLive(
            PhilipFisher(
                edgar_cache=cache,
                min_market_cap=1_000_000_000.0,
                max_portfolio_size=15,
                scuttlebutt_analyzer=layer.build("fisher", ScuttlebuttAnalyzer),
                llm_mode=mode,
                decision_logger=decision_logger,
            )
        ),
        # The eleventh seat. Unlike the ten above it answers to a
        # measurement rather than to a book, and it needs the EDGAR
        # cache directly — its screen is price times filed share count,
        # and the share count has to be restated into the price
        # series' split units before the product means anything.
        MarketCoreLive(
            MarketCore(
                price_loader=prices,
                edgar_cache=cache,
                decision_logger=decision_logger,
            )
        ),
        # The twelfth. It reads filings rather than the other books, so
        # its position in this list carries no meaning any more — it did
        # when the rule was "buy what three of the eleven hold", and
        # that rule is gone.
        PabraiLive(
            MohnishPabrai(
                edgar_cache=cache,
                price_loader=prices,
                regime_reader=read_regime,
                # E1's input. Without it the breaker reads "unreadable",
                # which blocks every entry -- so the agent screened two
                # thousand names, found a hundred and eleven candidates,
                # and bought none of them.
                drawdown_reader=lambda: drawdown_from_peak("mohnish_pabrai"),
            )
        ),
    ]


def _news_service() -> NewsService | None:
    """The feed, or None when nothing is configured.

    Built once per run and shared, so twelve agents asking about one
    company cost one round of calls rather than twelve. Returns None
    rather than raising: the Council treats an absent feed as no news,
    which skips its news veto instead of blocking every entry.
    """
    try:
        svc = NewsService.from_settings()
    except Exception as exc:
        logger.warning(f"news service unavailable — {type(exc).__name__}: {exc}")
        return None
    return svc if svc.available else None


def _held_positions(
    portfolio: LivePortfolio, prices: dict[str, float | None]
) -> dict[str, HeldPosition]:
    """Project the portfolio into what a strategy is allowed to see.

    Narrow on purpose: entry price, entry date, size and current mark.
    A doctrine needs those to express an exit; it has no business
    reaching into cash or weights, which the runner owns.
    """
    out: dict[str, HeldPosition] = {}
    for pos in portfolio.positions:
        mark = prices.get(pos.ticker)
        try:
            entry = _to_date(pos.entry_date)
        except (TypeError, ValueError):
            logger.debug(f"unparseable entry_date on {pos.ticker}: {pos.entry_date!r}")
            continue
        out[pos.ticker] = HeldPosition(
            ticker=pos.ticker,
            shares=pos.shares,
            entry_price=pos.entry_price,
            entry_date=entry,
            current_price=mark if mark is not None else pos.current_price,
        )
    return out


class _DictLookup:
    """Trivial wrapper exposing .get(ticker) for the Strategy protocol."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def get(self, ticker: str):  # type: ignore[no-untyped-def]
        return self._data.get(ticker)


def _filter_adapters(
    adapters: list[AgentAdapter], only: Sequence[str]
) -> list[AgentAdapter]:
    """Keep only the named agents, preserving registration order.

    Order is preserved rather than following the caller's argument order
    because registration order is meaningful — an agent that reads the
    others' published books must run after them.

    Raises:
        ValueError: If any name matches no adapter. Fail loud: a typo
            that silently produced an empty run would write no
            portfolios and report success, which is indistinguishable
            from a day on which nothing qualified.
    """
    wanted = {name.strip().lower() for name in only if name.strip()}
    if not wanted:
        raise ValueError("only_agents was given but contained no names")

    known = {a.name for a in adapters}
    unknown = sorted(wanted - known)
    if unknown:
        raise ValueError(
            f"unknown agent(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(known))}"
        )
    return [a for a in adapters if a.name in wanted]


class DailyRunner:
    """Run one day of paper-trading across all agents.

    Args:
        market: Which market to scan. ``"US"`` (default) uses
            :class:`FullMarketUniverse` (SEC EDGAR). ``"TASE"`` is
            currently a placeholder — the Israeli scanner isn't built
            yet, so a TASE run logs a notice and returns empty results
            without erroring (so the daily schedule can fire safely).
        adapters: Live adapters to run. Default = the 10 built-in
            agents (4 quant + 6 hybrid). Each hybrid agent runs its
            quant-only path; LLM analyzers are not invoked in live mode
            here (see ``build_default_adapters`` docstring).
        portfolio_dir: Where to persist portfolio JSON.
        cost_bps: Per-trade cost (default 10 bps).
        initial_cash: Seed cash for first run (default $10,000).
        only_agents: Restrict the run to these agent slugs. ``None``
            (default) runs everyone. An unknown slug raises rather than
            silently running nothing — a filter that quietly matches
            no-one looks exactly like a clean run that traded nothing,
            which is also the normal outcome on a quiet day.
    """

    def __init__(
        self,
        *,
        market: str = "US",
        adapters: list[AgentAdapter] | None = None,
        portfolio_dir: Path = DEFAULT_PORTFOLIO_DIR,
        published_dir: Path | None = None,
        ledger_dir: Path | None = None,
        cost_bps: float = DEFAULT_COST_BPS,
        initial_cash: float = DEFAULT_INITIAL_CASH,
        rebalance_band: float = DEFAULT_REBALANCE_BAND,
        min_holding_days: int = DEFAULT_MIN_HOLDING_DAYS,
        cache: EdgarCache | None = None,
        price_loader: PriceDataLoader | None = None,
        universe: FullMarketUniverse | None = None,
        pit_loader: PointInTimeLoader | None = None,
        decision_logger: DecisionLogger | None = None,
        only_agents: Sequence[str] | None = None,
        llm_mode: LLMMode = LLMMode.OFF,
    ) -> None:
        if market not in ("US", "TASE"):
            raise ValueError(
                f"unknown market {market!r}; expected 'US' or 'TASE'"
            )
        self.market = market
        self.cache = cache or EdgarCache()
        self.decision_logger = decision_logger or DecisionLogger()
        # Before the adapters: Schloss's distressed-price condition
        # needs this loader, and the builder takes it as an argument.
        self.price_loader = price_loader or PriceDataLoader()
        self.adapters = adapters or build_default_adapters(
            decision_logger=self.decision_logger,
            edgar_cache=self.cache,
            price_loader=self.price_loader,
            llm_mode=llm_mode,
        )
        if only_agents is not None:
            self.adapters = _filter_adapters(self.adapters, only_agents)
        self.portfolio_dir = portfolio_dir
        # Where the cooling-off lists live. Resolved once here rather
        # than per call so a test can point it at a tmp_path.
        self.published_dir = published_dir or council_published_dir()
        self.ledger_dir = ledger_dir or trade_ledger_dir()
        self.cost_bps = cost_bps
        self.initial_cash = initial_cash
        self.rebalance_band = rebalance_band
        self.min_holding_days = min_holding_days
        self.universe = universe or FullMarketUniverse(
            cache=self.cache,
            # Live only: a symbol the SEC no longer lists cannot be
            # bought today. ASGN became EFOR and both were held at
            # once. Backtests leave this off — see the flag.
            require_current_listing=True,
        )
        if pit_loader is None:
            fetcher = FundamentalsFetcher(
                cache=self.cache,
                client=None,
                config=FundamentalsFetcherConfig(populate_cache_on_miss=False),
            )
            adapter = CachedEdgarAdapter(fetcher=fetcher)
            self.pit_loader = PointInTimeLoader(adapter=adapter)
        else:
            self.pit_loader = pit_loader

    # ------------------------------------------------------------------
    def _tase_placeholder(
        self, mode_label: str, as_of: date | None
    ) -> list[AgentRunResult]:
        """Return empty results for a TASE run.

        The Israeli scanner isn't built yet (see
        ``core/backtest/tase_universe.py`` for status). We emit a clear
        notice so cron logs are honest about why nothing happened, then
        return zero results. This keeps the schedule safe to run
        unattended on Sun-Thu — no exceptions, no spurious commits.
        """
        eff = as_of or date.today()
        logger.info(
            f"{eff}: TASE {mode_label} scan — Israeli scanner not yet built; "
            f"returning empty results (placeholder)."
        )
        return []

    def _market_shut(self, mode_label: str, as_of: date) -> bool:
        """Whether to decline ``as_of`` because the US market had no session.

        The schedule ran Monday to Friday and knew nothing else, so a
        public holiday was an ordinary trading day to it. On four of
        them in 2026 the agents wrote 1,770 journal rows and executed 21
        trades, every one priced at the previous session's close —
        because that is what a quote feed returns when there is no
        session. The NAV was not even distorted. The dates were, and a
        paper-trading record whose dates cannot be trusted records
        nothing.

        Declining is deliberately louder than skipping: the workflow's
        own verifier asserts every book was refreshed, so a day stopped
        here has to be a day it also knows about, or this becomes a way
        to fail the build quietly. They read the same calendar.
        """
        reason = why_closed(as_of)
        if reason is None:
            early = closes_early(as_of)
            if early is not None:
                # Reported, not acted on: an early close moves the
                # auction forward, and a mark struck after it reads the
                # real official close, which is the number wanted.
                logger.info(
                    f"{as_of}: the market closes at 13:00 ET today "
                    f"({early}); running as normal"
                )
            return False
        logger.warning(
            f"{as_of}: no US session today ({reason}) — {mode_label} "
            f"declined. Prices on a shut day are the previous close, and "
            f"booking them under today's date is how four 2026 holidays "
            f"acquired 21 trades that never happened."
        )
        return True

    def run_mark_to_market(
        self, *, as_of: date | None = None
    ) -> list[AgentRunResult]:
        if self.market == "TASE":
            return self._tase_placeholder("close", as_of)
        if self._market_shut("mark-to-market", as_of or date.today()):
            return []
        """Close-of-day mode: refresh prices on every open position,
        update NAV / P&L / weights, write a fresh portfolio.json AND a
        snapshot. No new BUYs or SELLs executed.

        Why split this from :meth:`run`?
          - The strategies decide based on FUNDAMENTALS available at
            open. Re-running them at close would not produce different
            target weights — but it WOULD churn cost dollars.
          - Closing prices give the truest end-of-day NAV for the
            history chart.
        """
        as_of = as_of or date.today()
        logger.info(f"close-of-day mark-to-market for {as_of}")
        results: list[AgentRunResult] = []
        for adapter in self.adapters:
            try:
                portfolio = LivePortfolio.load_or_seed(
                    adapter.name,
                    directory=self.portfolio_dir,
                    initial_cash=self.initial_cash,
                )
                # Fetch fresh prices for every held ticker. force_refresh
                # is required: the morning run cached an intraday quote
                # under today's date, and without it get_price_on's fast
                # path returns that same number, so the close-of-day mark
                # silently records the 09:35 ET price as the close.
                tickers = [p.ticker for p in portfolio.positions]
                fresh: dict[str, float | None] = {}
                for t in tickers:
                    try:
                        fresh[t] = self.price_loader.get_price_on(
                            t, as_of, force_refresh=True
                        )
                    except Exception as exc:
                        logger.warning(
                            f"close-of-day price for {t} @ {as_of} failed: {exc}"
                        )
                        fresh[t] = None
                portfolio.mark_to_market(fresh)
                stamp = now_iso()
                portfolio.last_updated = stamp
                portfolio.last_close_run = stamp
                portfolio.last_close_date = as_of.isoformat()
                portfolio.save(directory=self.portfolio_dir)
                # Snapshot — no trades, just refreshed valuation.
                try:
                    snap = make_snapshot(portfolio, as_of=as_of, trades=[])
                    save_snapshot(snap)
                except Exception as exc:
                    logger.warning(
                        f"{as_of}: close snapshot for {adapter.name} failed: {exc}"
                    )
                results.append(
                    AgentRunResult(
                        agent=adapter.name,
                        portfolio=portfolio,
                        targets=[],
                        watchlist=[],
                        trades=[],
                        universe_size=0,
                    )
                )
            except Exception as exc:
                logger.error(f"{as_of}: {adapter.name} close failed: {exc}")
                portfolio = LivePortfolio.load_or_seed(
                    adapter.name,
                    directory=self.portfolio_dir,
                    initial_cash=self.initial_cash,
                )
                results.append(
                    AgentRunResult(
                        agent=adapter.name, portfolio=portfolio, error=str(exc)
                    )
                )

        # The close run marks every position and is the last word on the
        # day, but it used to publish nothing — only ``run`` did. So the
        # dashboard's charts, and the bar dates it reads to tell a fresh
        # mark from a stale one, were always a session behind the book
        # they described. 71 of 172 held positions had no published
        # series at all on 2026-08-08.
        try:
            export_sectors([r.portfolio for r in results if r.portfolio is not None])
            export_prices(
                [r.portfolio for r in results if r.portfolio is not None],
                as_of=as_of,
                loader=self.price_loader,
            )
        except Exception as exc:  # presentation only; never fails a run
            logger.warning(f"{as_of}: price export failed: {exc}")

        # Deliberately last, and outside everything that matters: the run
        # has executed and persisted by now, so the worst a broken
        # notifier can do is fail to describe work already done right.
        # `notify_run` never raises and no-ops without credentials.
        notify_run(results, as_of=as_of or date.today(), env=os.environ)

        return results

    # ------------------------------------------------------------------
    def run(
        self, *, as_of: date | None = None, force: bool = False
    ) -> list[AgentRunResult]:
        if self.market == "TASE":
            return self._tase_placeholder("open", as_of)
        as_of = as_of or date.today()
        if self._market_shut("scan", as_of):
            return []
        if as_of != date.today():
            # Said once, at the top, because everything below writes
            # files dated to the session rather than to today and a
            # reader of those files should not have to deduce it.
            logger.warning(
                f"catching up the {as_of} session on {date.today()}: prices and "
                f"filings are read as of {as_of}, and every row written is "
                f"dated to it. The ledger marks these rows 'caught-up'."
            )
        logger.info(f"daily run for {as_of}")
        self.universe._ensure_loaded()
        members = self.universe.constituents_at(as_of)
        logger.info(f"{as_of}: universe = {len(members)} tickers")

        # Pre-fetch prices once for the whole run; PriceDataLoader caches
        # to SQLite so subsequent days hit the cache immediately.
        prices = self._fetch_prices(members, as_of)
        logger.info(
            f"{as_of}: priced {sum(1 for v in prices.values() if v is not None)}"
            f"/{len(members)} tickers"
        )

        # Pre-load fundamentals once per ticker per run.
        fundamentals = self._load_fundamentals(members, as_of)
        logger.info(
            f"{as_of}: fundamentals available for "
            f"{sum(1 for v in fundamentals.values() if v is not None)}/{len(members)}"
        )

        results: list[AgentRunResult] = []
        for adapter in self.adapters:
            try:
                result = self._run_one(
                    adapter, as_of, members, prices, fundamentals, force=force
                )
            except Exception as exc:
                logger.error(f"{as_of}: {adapter.name} failed: {exc}")
                portfolio = LivePortfolio.load_or_seed(
                    adapter.name,
                    directory=self.portfolio_dir,
                    initial_cash=self.initial_cash,
                )
                result = AgentRunResult(
                    agent=adapter.name, portfolio=portfolio, error=str(exc)
                )
            results.append(result)

        # Publish a price series per held ticker so the dashboard can
        # draw the line from entry to today. Cache-only and best-effort:
        # a chart is not worth failing a trading run over.
        try:
            export_sectors([r.portfolio for r in results if r.portfolio is not None])
            export_prices(
                [r.portfolio for r in results if r.portfolio is not None],
                as_of=as_of,
                loader=self.price_loader,
            )
        except Exception as exc:  # presentation only; never fails a run
            logger.warning(f"{as_of}: price export failed: {exc}")

        # Deliberately last, and outside everything that matters: the run
        # has executed and persisted by now, so the worst a broken
        # notifier can do is fail to describe work already done right.
        # `notify_run` never raises and no-ops without credentials.
        notify_run(results, as_of=as_of or date.today(), env=os.environ)

        return results

    # ------------------------------------------------------------------
    def _run_one(
        self,
        adapter: AgentAdapter,
        as_of: date,
        members: list[str],
        prices: dict[str, float | None],
        fundamentals: dict[str, PointInTimeFinancials | None],
        *,
        force: bool = False,
        run_type: RunType | None = None,
    ) -> AgentRunResult:
        portfolio = LivePortfolio.load_or_seed(
            adapter.name,
            directory=self.portfolio_dir,
            initial_cash=self.initial_cash,
        )

        # Idempotency. The workflow can fire twice for one date — a
        # retry, a manual dispatch after a scheduled run, the watchdog's
        # make-up trigger — and without this the second pass re-executes
        # every rotation and writes a second set of decision records for
        # the same day. last_open_run has been written since the runner
        # was built and never read until now.
        # Compare logical date to logical date. This used to read
        # ``last_open_run[:10]``, a wall-clock stamp, against ``as_of``.
        # The two agree only when a run happens on the same calendar day
        # it covers — so a run that crosses UTC midnight stamped
        # tomorrow, and the next day's run skipped itself as a duplicate
        # and lost a trading day silently. The 2026-08-07 00:51Z make-up
        # run is exactly that shape.
        if not force and portfolio.last_open_date == as_of.isoformat():
            logger.info(
                f"{as_of}: {adapter.name} already completed this date — "
                f"skipping (pass force=True to re-run)"
            )
            return AgentRunResult(
                agent=adapter.name, portfolio=portfolio, skipped=True
            )

        # Mark existing positions to today's price (some held tickers
        # may not be in members — still need their current price for NAV).
        held_prices = dict(prices)
        for pos in portfolio.positions:
            if pos.ticker not in held_prices or held_prices[pos.ticker] is None:
                held_prices[pos.ticker] = self.price_loader.get_price_on(
                    pos.ticker, as_of
                )
        portfolio.mark_to_market(held_prices)

        # The agent sees what it owns. Without this every doctrine
        # inherited the same exit rule — "sold because you left today's
        # top N" — and eight investors whose real-world turnover
        # differed by an order of magnitude traded identically.
        scan = adapter.run_scan(
            as_of,
            members,
            _DictLookup(prices),
            _DictLookup(fundamentals),
            held=_held_positions(portfolio, held_prices),
        )
        # ---- RUN TYPE: what Part 7 lets this run do -------------------
        # Only the twelfth agent is bound by the table; see
        # AgentAdapter.honours_run_types. A run that may not open keeps
        # every name already held and refuses the rest, which is the
        # single rule the cooling-off depends on: a position is opened
        # in a COUNCIL run or not at all.
        #
        # Exits are untouched on purpose. The heartbeat exists to catch
        # a kill trigger or a breaking 8-K, and a run type that could
        # see one and not act would be worse than no run at all.
        if run_type is None:
            run_type = _derive_run_type(as_of, portfolio.last_open_date)

        # ---- COOLING-OFF: publish now, execute what was published ----
        # §7 wires the statistical rebalance to a list published at least
        # one run earlier, "the cooling-off rule holds even for the
        # machine". So every Council scan writes its would-be list, and
        # a rebalance run trades from whatever was written before today
        # rather than from the rank computed a second ago.
        if getattr(adapter, "honours_run_types", False):
            self._publish_would_be_list(adapter, scan, as_of)
            if is_rebalance_run(
                as_of,
                previous_run=_parse_date(portfolio.last_open_date),
                last_rebalance=_parse_date(portfolio.last_rebalance_date),
            ):
                scan = self._rebalance_from_published(adapter, scan, portfolio, as_of)
                portfolio.last_rebalance_date = as_of.isoformat()
        if getattr(adapter, "honours_run_types", False) and not permissions_for(
            run_type
        ).may_open:
            held_now = {p.ticker for p in portfolio.positions}
            refused = [t for t in scan.targets if t.ticker not in held_now]
            if refused:
                logger.info(
                    f"{as_of}: {adapter.name} {run_type} may not open — "
                    f"holding off {len(refused)} name(s): "
                    f"{', '.join(sorted(t.ticker for t in refused))}"
                )
            scan.targets = [t for t in scan.targets if t.ticker in held_now]

        target_tickers = {t.ticker for t in scan.targets}
        trades: list[TradeRecord] = []

        # ---- DIVIDENDS: pay income before any trading ------------------
        # Before the diff, so a name sold today still collects a dividend
        # whose ex-date has already passed. Ordering matters: after the
        # sell it would be silently forfeited.
        self._settle_dividends(portfolio, as_of)

        # ---- SELLs: positions that left the target list ---------------
        # Skip if no targets — that's a "no-trade" signal (e.g. universe
        # produced no candidates) and we'd otherwise liquidate everything.
        forced_exits = {t.upper() for t in scan.forced_exits}
        # A forced exit runs even on a no-trade day. The `scan.targets`
        # guard below exists so an empty target list is not read as
        # "liquidate everything", but a doctrine that has named the
        # positions it wants out has not gone quiet — it has been
        # specific, and specificity is the opposite of the ambiguity the
        # guard protects against.
        # An intentional flat is the third way the sell loop may run. It
        # is the case the `scan.targets` guard cannot serve: the doctrine
        # wants no equities at all, which produces the same empty list a
        # failed scan does. The flag is how the two are told apart.
        going_flat = scan.flat_is_intentional
        if scan.targets or forced_exits or going_flat:
            for pos in list(portfolio.positions):
                # Forced first: a name can be both a current target and
                # under a terminal filing, and the filing wins.
                if pos.ticker in target_tickers and pos.ticker not in forced_exits:
                    continue
                if not scan.targets and not going_flat and pos.ticker not in forced_exits:
                    continue
                # A name can leave the target list for a day because a
                # price moved and it slipped a rank, then come straight
                # back. Selling on that is churn, not doctrine: across
                # the ten agents the decision logs hold 246 completed
                # round-trips, 75 of them Graham's, who bought CRMD,
                # sold it two days later and bought it back the next
                # morning — four times in three months. Whatever that
                # is, it is not the Defensive Investor of chapter 14.
                #
                # So a position younger than the floor is held. If the
                # thesis genuinely broke inside a month the exit is
                # merely late; if it slipped a rank, the exit never
                # happens and the round-trip costs nothing.
                #
                # A forced exit is exempt. The floor is aimed at names
                # that slipped a rank and will slip back; it was never
                # meant to sit on an 8-K item 4.02, a delisting notice
                # or a trailing stop for the remainder of a month. Under
                # COUNCIL_SELECTION E2 those sell next session, "no
                # council, no discussion", and a floor that outranked
                # them would make the rule decorative.
                # Going flat is exempt from the floor for the same reason
                # a forced exit is. The floor stops a name that slipped a
                # rank from being sold before it slips back; "hold no
                # equities" is not oscillation, and a floor that outranked
                # it would leave the book part-invested against an explicit
                # decision to be in cash.
                forced = pos.ticker in forced_exits or going_flat
                age = pos_age_days(pos, as_of)
                if not forced and age is not None and age < self.min_holding_days:
                    logger.info(
                        f"{as_of}: {adapter.name} keeping {pos.ticker} — "
                        f"held {age}d, floor is {self.min_holding_days}d"
                    )
                    continue
                if forced and age is not None and age < self.min_holding_days:
                    logger.info(
                        f"{as_of}: {adapter.name} forcing {pos.ticker} out at "
                        f"{age}d, inside the {self.min_holding_days}d floor"
                    )
                # No fallback to pos.current_price. mark_to_market keeps the
                # last known mark when a price is missing, which is right for
                # valuing a position but wrong for executing a sale: a
                # delisted or halted holding would book fabricated proceeds
                # at a price that no longer trades, and a realized P&L of
                # exactly zero. Absent a fresh price we hold and retry
                # tomorrow.
                price = held_prices.get(pos.ticker)
                if price is None or price <= 0:
                    logger.warning(
                        f"{as_of}: cannot sell {pos.ticker} — no fresh price; "
                        f"holding position"
                    )
                    continue
                try:
                    trade = portfolio.sell(
                        pos.ticker, price=price, cost_bps=self.cost_bps
                    )
                    trades.append(trade)
                    self._log_sell(adapter.name, pos, price, as_of)
                except LivePortfolioError as exc:
                    logger.warning(f"{as_of}: sell of {pos.ticker} failed: {exc}")

        # ---- BUYs: targets we don't yet hold --------------------------
        nav_after_sells = portfolio.total_nav
        # Each target's notional is target.weight × NAV. The strategies
        # produce equal weights so this is uniform; we honour whatever
        # weight was set just in case.
        #
        # First, drop the names a book this size cannot hold a share of
        # and give their weight to the ones it can. The same list then
        # feeds the rebalance pass — sizing and the band have to agree
        # on what a target weight is, or the band spends every run
        # trimming a position the sizing pass deliberately widened.
        scan.targets = reachable_targets(
            scan.targets,
            prices,
            nav_after_sells,
            held=frozenset(pos.ticker for pos in portfolio.positions),
        )
        # Then decide the share count for every new name together, so
        # the remainder one slot cannot round up becomes a share in the
        # slot that needs it most.
        plan = plan_whole_share_buys(
            [t for t in scan.targets if not portfolio.has(t.ticker)],
            prices,
            nav_after_sells,
            portfolio.cash,
            cost_bps=self.cost_bps,
            band=self.rebalance_band,
        )
        for target in scan.targets:
            if portfolio.has(target.ticker):
                # Refresh the why string on the existing position so the
                # dashboard always shows the *current* rationale.
                idx = portfolio._index_of(target.ticker)
                if idx is not None:
                    portfolio.positions[idx].why_en = target.why_en
                    portfolio.positions[idx].why_he = target.why_he
                continue
            price = prices.get(target.ticker)
            if price is None or price <= 0:
                logger.warning(
                    f"{as_of}: cannot buy {target.ticker} — no price"
                )
                continue
            want_shares = plan.get(target.ticker, 0)
            if want_shares <= 0:
                # No whole share of this name fits what is left. The
                # allocator has already said so and said why; repeating
                # it per ticker here would log the same fact twice.
                continue
            # The plan is in shares; buy() takes dollars. One cent of
            # headroom absorbs the float error in price × shares × cost
            # without being able to buy an extra share of anything that
            # trades above a cent.
            target_dollars = want_shares * price * (1.0 + self.cost_bps) + 0.01
            if target_dollars > portfolio.cash:
                target_dollars = portfolio.cash
            if target_dollars < MIN_TRADE_USD:
                logger.info(
                    f"{as_of}: skipping {target.ticker} — target "
                    f"${target_dollars:.2f} below ${MIN_TRADE_USD:.2f} floor"
                )
                continue
            try:
                trade = portfolio.buy(
                    target.ticker,
                    target_dollars=target_dollars,
                    price=price,
                    entry_date=as_of.isoformat(),
                    why_en=target.why_en,
                    why_he=target.why_he,
                    cost_bps=self.cost_bps,
                )
                trades.append(trade)
                self._log_buy(adapter.name, target, price, as_of)
            except LivePortfolioError as exc:
                logger.warning(f"{as_of}: buy of {target.ticker} failed: {exc}")

        # ---- REBALANCE: pull drifted holdings back toward target ------
        trades.extend(
            self._rebalance(portfolio, scan.targets, prices, as_of, adapter.name)
        )

        # Re-mark after trades and refresh weights.
        portfolio.mark_to_market(held_prices)

        # ---- Watchlist ------------------------------------------------
        watch_entries = [
            WatchEntry(
                ticker=w.ticker,
                identified_date=as_of.isoformat(),
                current_rank=w.rank,
                entry_trigger=w.entry_trigger,
                entry_price_target=w.entry_price_target,
                last_price=w.last_price,
                why_en=w.why_en,
                why_he=w.why_he,
            )
            for w in scan.watchlist
        ]
        # Read before the overwrite on the next line — this is the only
        # moment the previous run's list still exists.
        previous_watchlist = tuple(w.ticker for w in portfolio.watchlist)
        portfolio.set_watchlist(watch_entries)

        stamp = now_iso()
        portfolio.last_updated = stamp
        portfolio.last_open_run = stamp
        portfolio.last_open_date = as_of.isoformat()
        portfolio.save(directory=self.portfolio_dir)

        # Persist a daily snapshot for History tab + Today's-activity.
        try:
            snap = make_snapshot(portfolio, as_of=as_of, trades=trades)
            save_snapshot(snap)
        except Exception as exc:
            logger.warning(f"{as_of}: snapshot save for {adapter.name} failed: {exc}")

        # The ledger. The snapshot above keeps only tickers, which is
        # why a book could report +24.55% with every open position
        # losing money and no file able to say which sale paid for it.
        # Never fatal: the trades already happened and the book is
        # already saved, so failing here costs attribution, not money.
        try:
            record_day(
                adapter.name,
                as_of,
                trades,
                directory=self.ledger_dir,
                # Only written down when the two differ, which marks a
                # session caught up later rather than traded on the day.
                # The rows stay dated to the session either way, so the
                # ledger never claims a trade on a shut market; this is
                # so a reader who notices a Friday book written on a
                # Saturday finds the answer rather than a puzzle.
                recorded_on=date.today(),
            )
        except Exception as exc:
            logger.warning(f"{as_of}: ledger write for {adapter.name} failed: {exc}")

        return AgentRunResult(
            agent=adapter.name,
            portfolio=portfolio,
            targets=scan.targets,
            watchlist=scan.watchlist,
            previous_watchlist=previous_watchlist,
            trades=trades,
            universe_size=scan.universe_size,
        )

    # ------------------------------------------------------------------
    def _publish_would_be_list(
        self, adapter: AgentAdapter, scan: ScanResult, as_of: date
    ) -> None:
        """Write today's rank for a later run to trade from.

        Publication never fails a run. The list is an input to a future
        rebalance, not to this one, so a disk problem here costs the
        next quarter its freshest list — which the cooling-off rule then
        handles by falling back to an older one or refusing to rebalance.
        Letting it take down a run that has already moved eleven other
        books would be the wrong trade by a wide margin.
        """
        try:
            publish(
                PublishedList(
                    published_on=as_of,
                    as_of=as_of,
                    tickers=tuple(t.ticker for t in scan.targets),
                    weights={t.ticker: t.weight for t in scan.targets},
                    note=f"{adapter.name} would-be list",
                ),
                directory=self.published_dir,
            )
        except Exception as exc:
            logger.warning(f"{as_of}: could not publish {adapter.name}'s list: {exc}")

    def _rebalance_from_published(
        self,
        adapter: AgentAdapter,
        scan: ScanResult,
        portfolio: LivePortfolio,
        as_of: date,
    ) -> ScanResult:
        """Swap today's rank for the one published on an earlier run.

        With nothing published in time the book is held rather than
        traded: the fallback of "rank now, execute now" is the exact
        breach §7 forbids, and it would arrive looking like resilience.
        """
        published = executable_list(self.published_dir, as_of=as_of)
        if published is None:
            logger.info(
                f"{as_of}: {adapter.name} rebalance skipped — nothing published "
                "before today, and today's rank has not cooled off"
            )
            held = {p.ticker for p in portfolio.positions}
            scan.targets = [t for t in scan.targets if t.ticker in held]
            return scan

        logger.info(
            f"{as_of}: {adapter.name} rebalancing from the list published "
            f"{published.published_on} ({len(published.tickers)} names)"
        )
        by_ticker = {t.ticker: t for t in scan.targets}
        rebalanced: list[LiveTarget] = []
        for rank, ticker in enumerate(published.tickers, start=1):
            existing = by_ticker.get(ticker)
            if existing is not None:
                # replace(), not assignment: LiveTarget is frozen, and
                # assigning raised FrozenInstanceError on every name
                # that survived the quarter — which is most of them.
                rebalanced.append(
                    replace(existing, weight=published.weights[ticker])
                )
                continue
            # On the published list and no longer in today's scan. It is
            # still what the cooled-off decision named, so it is bought —
            # with the published weight and a reason that says where the
            # instruction came from rather than inventing one.
            rebalanced.append(
                LiveTarget(
                    ticker=ticker,
                    weight=published.weights[ticker],
                    rank=rank,
                    why_en=(
                        f"quarterly rebalance, from the list published "
                        f"{published.published_on}"
                    ),
                    why_he=(
                        f"איזון רבעוני, מהרשימה שפורסמה ב-{published.published_on}"
                    ),
                    score=None,
                )
            )
        scan.targets = rebalanced
        return scan

    def _settle_dividends(self, portfolio: LivePortfolio, as_of: date) -> float:
        """Credit cash for every unpaid dividend on the current holdings.

        Marking a position at the close means the ex-date price drop
        lands in NAV while the payment never does, so the recorded
        return was price return rather than total return. The penalty
        is proportional to yield, which makes it doctrine-correlated:
        Neff, Dreman and Graham were losing roughly their portfolio
        yield a year against Buffett and Fisher for no reason either
        investor would recognise.

        Each position is scanned over its **whole holding period**, from
        its own ``entry_date`` — not over a window since the last run.
        A one-day window was what made this silently pay nothing: the
        floor came from ``last_open_run``, which the previous run had
        already advanced to the current date, so the query asked for
        dividends in an empty interval. 68 consecutive daily runs
        credited $0.00 while eleven ex-dates passed on Neff's book.

        Re-scanning is safe because :meth:`LivePortfolio.credit_dividend`
        is idempotent per ``(ticker, ex_date)``, and it is what makes
        settlement self-healing: a dividend whose row reaches the local
        cache days after its ex-date is still picked up, where the old
        window had already moved past it. ``entry_date`` remains the
        lower bound, so a name bought yesterday never collects on an
        ex-date from before it was owned.
        """
        if not portfolio.positions:
            return 0.0

        if not portfolio.dividend_floor_date:
            # First settlement for this book. Stamp the floor and pay
            # nothing before it: a seeded portfolio carries synthetic
            # entry dates that may run years back, and it never owned
            # those shares.
            portfolio.dividend_floor_date = as_of.isoformat()

        floor = portfolio.dividend_floor_date[:10]
        total = 0.0
        for pos in list(portfolio.positions):
            since = max(pos.entry_date[:10], floor)
            try:
                payments = self.price_loader.dividends_between(
                    pos.ticker, since, as_of
                )
            except Exception as exc:
                logger.warning(f"{as_of}: dividend lookup {pos.ticker} failed: {exc}")
                continue
            for ex_date, amount in payments:
                try:
                    cash = portfolio.credit_dividend(
                        pos.ticker,
                        amount_per_share=amount,
                        ex_date=ex_date.isoformat(),
                    )
                except LivePortfolioError as exc:
                    logger.warning(f"{as_of}: dividend {pos.ticker} failed: {exc}")
                    continue
                if cash:
                    total += cash
                    logger.info(
                        f"{as_of}: {portfolio.agent} received ${cash:,.2f} "
                        f"from {pos.ticker} (ex {ex_date}, ${amount:.4f}/sh)"
                    )
        # last_dividend_date is maintained by credit_dividend as the
        # latest ex-date actually paid. Stamping it with as_of here would
        # claim the book is settled through today on a run that paid
        # nothing, which is the assumption that hid this bug.
        return total

    # ------------------------------------------------------------------
    def _rebalance(
        self,
        portfolio: LivePortfolio,
        targets: list[LiveTarget],
        prices: dict[str, float | None],
        as_of: date,
        agent: str,
    ) -> list[TradeRecord]:
        """Pull holdings that drifted outside the band back to target.

        Positions were sized once at entry and never touched again, so a
        name that doubled became twice its intended weight and every
        sizing doctrine decayed into "whatever the market did since we
        bought". Rebalancing is not a doctrine choice — an agent that
        states equal weights means to hold equal weights.

        Only names still in the target list are considered; anything
        that left is handled by the exit path. Trims are executed before
        adds so the cash from a trim is available to fund an add in the
        same pass.
        """
        if not targets:
            return []

        band = self.rebalance_band
        nav = portfolio.total_nav
        if nav <= 0:
            return []

        trims: list[tuple[str, float, float]] = []  # (ticker, price, shares)
        adds: list[tuple[str, float, float]] = []  # (ticker, price, dollars)

        for target in targets:
            idx = portfolio._index_of(target.ticker)
            if idx is None:
                continue
            price = prices.get(target.ticker)
            if price is None or price <= 0:
                continue
            pos = portfolio.positions[idx]
            want = nav * target.weight
            have = pos.shares * price
            if want <= 0:
                continue
            drift = (have - want) / want
            if abs(drift) <= band:
                continue
            delta = abs(have - want)
            if delta < MIN_TRADE_USD:
                continue
            if drift > 0:
                if delta < price:
                    # Less than one share of drift. Trimming would need a
                    # fraction, and leaving it is what a real book does.
                    continue
                trims.append((target.ticker, price, delta / price))
            else:
                adds.append((target.ticker, price, delta))

        out: list[TradeRecord] = []
        for ticker, price, shares in trims:
            try:
                out.append(
                    portfolio.sell(
                        ticker, price=price, shares=shares, cost_bps=self.cost_bps
                    )
                )
                logger.info(f"{as_of}: {agent} trimmed {ticker} back to target")
            except LivePortfolioError as exc:
                logger.warning(f"{as_of}: trim of {ticker} failed: {exc}")

        for ticker, price, dollars in adds:
            spend = min(dollars, portfolio.cash * 0.99)
            if spend < MIN_TRADE_USD or spend < price:
                # Under a share's price there is no trade to make, and
                # the drift stays until the next run — which is correct:
                # a band cannot be closed with less than one share.
                continue
            try:
                out.append(
                    portfolio.buy(
                        ticker,
                        target_dollars=spend,
                        price=price,
                        entry_date=as_of.isoformat(),
                        why_en="",
                        why_he="",
                        cost_bps=self.cost_bps,
                    )
                )
                logger.info(f"{as_of}: {agent} added to {ticker} back to target")
            except LivePortfolioError as exc:
                logger.warning(f"{as_of}: add to {ticker} failed: {exc}")

        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _fetch_prices(
        self, tickers: list[str], as_of: date
    ) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for t in tickers:
            try:
                out[t] = self.price_loader.get_price_on(t, as_of)
            except Exception as exc:
                logger.warning(f"price fetch {t} @ {as_of} failed: {exc}")
                out[t] = None
        return out

    def _load_fundamentals(
        self, tickers: list[str], as_of: date
    ) -> dict[str, PointInTimeFinancials | None]:
        out: dict[str, PointInTimeFinancials | None] = {}
        for t in tickers:
            try:
                out[t] = self.pit_loader.get_financials(t, as_of)
            except Exception as exc:
                logger.debug(f"fundamentals {t} @ {as_of} failed: {exc}")
                out[t] = None
        return out

    def _log_buy(
        self, agent: str, target: LiveTarget, price: float, as_of: date
    ) -> None:
        try:
            self.decision_logger.log(
                make_decision(
                    ticker=target.ticker,
                    # FILL, not BUY. The strategy already logged BUY as
                    # its intent; this record is the execution.
                    decision="FILL",
                    agent=agent,
                    timestamp=f"{as_of.isoformat()}T00:00:00+00:00",
                    criteria_met=[f"rank #{target.rank}", "live paper-trade"],
                    rationale=target.why_en,
                    entry_price=price,
                )
            )
        except Exception as exc:
            logger.debug(f"decision log BUY {target.ticker} failed: {exc}")

    def _log_sell(
        self, agent: str, pos: Position, price: float, as_of: date
    ) -> None:
        try:
            self.decision_logger.log(
                make_decision(
                    ticker=pos.ticker,
                    # EXIT, not SELL — this is the executed disposal, and
                    # the trigger is a real one: the name left today's
                    # target list. Recording it as "SELL" made a
                    # mechanical rotation look like a doctrine decision.
                    decision="EXIT",
                    agent=agent,
                    timestamp=f"{as_of.isoformat()}T00:00:00+00:00",
                    criteria_met=["left today's target list"],
                    rationale=(
                        f"Closed live position: entry ${pos.entry_price:.2f}, "
                        f"exit ${price:.2f} ({(price/pos.entry_price - 1)*100:+.2f}%)"
                    ),
                    entry_price=pos.entry_price,
                )
            )
        except Exception as exc:
            logger.debug(f"decision log SELL {pos.ticker} failed: {exc}")


__all__ = [
    "AgentRunResult",
    "DailyRunner",
    "build_default_adapters",
]
