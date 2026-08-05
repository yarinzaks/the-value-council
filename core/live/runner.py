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

The runner deliberately *always* trades on every day's run — the user's
spec says "Buy/sell decisions saved to data/decisions/" daily. In
practice, on most days the target set won't change (fundamentals don't
move daily), so trade churn stays low.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from agents.buffett import WarrenBuffett
from agents.dreman.contrarian import DavidDreman
from agents.fisher import PhilipFisher
from agents.graham.net_net import BenjaminGraham
from agents.greenblatt.magic_formula import MagicFormula
from agents.klarman import SethKlarman
from agents.lynch import PeterLynch
from agents.marks import HowardMarks
from agents.neff.total_return import JohnNeff
from agents.schloss.deep_value import WalterSchloss
from core.backtest.data_loader import PriceDataLoader
from core.backtest.decision_logger import DecisionLogger, make_decision
from core.backtest.full_market_universe import FullMarketUniverse
from core.backtest.point_in_time import PointInTimeFinancials, PointInTimeLoader
from core.data.edgar_cache import EdgarCache
from core.data.fundamentals_fetcher import (
    CachedEdgarAdapter,
    FundamentalsFetcher,
    FundamentalsFetcherConfig,
)
from core.live.agent_adapter import (
    AgentAdapter,
    BuffettLive,
    DremanLive,
    FisherLive,
    GrahamLive,
    GreenblattLive,
    KlarmanLive,
    LiveTarget,
    LiveWatch,
    LynchLive,
    MarksLive,
    NeffLive,
    ScanResult,
    SchlossLive,
)
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
    today_iso,
)
from core.live.snapshots import make_snapshot, save_snapshot
from core.logger import get_logger

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


@dataclass
class AgentRunResult:
    """Per-agent summary returned by :meth:`DailyRunner.run`."""

    agent: str
    portfolio: LivePortfolio
    targets: list[LiveTarget] = field(default_factory=list)
    watchlist: list[LiveWatch] = field(default_factory=list)
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
) -> list[AgentAdapter]:
    """Construct the live adapters for all 10 council members.

    The 4 original quant agents (Greenblatt, Schloss, Graham, Dreman)
    don't need ``edgar_cache`` (they read fundamentals via the runner's
    ``FundamentalsLookup``). The 6 hybrid agents (Neff, Buffett, Lynch,
    Marks, Klarman, Fisher) DO need it for trailing growth / FCF /
    margin-trend lookups, so we pass the runner's cache through.

    LLM analyzers are intentionally ``None`` for live mode here — same
    rationale as backtest: lookahead bias + free-tier quota burn. The
    quant pipelines drive selection identically; if/when the LLM-quota
    story changes, the analyzers can be wired in by the caller.
    """
    cache = edgar_cache or EdgarCache()
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
        DremanLive(
            DavidDreman(
                portfolio_size=25,
                min_market_cap=500_000_000.0,
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
                moat_analyzer=None,
                decision_logger=decision_logger,
            )
        ),
        LynchLive(
            PeterLynch(
                edgar_cache=cache,
                portfolio_size=30,
                min_market_cap=300_000_000.0,
                category_classifier=None,
                decision_logger=decision_logger,
            )
        ),
        MarksLive(
            HowardMarks(
                edgar_cache=cache,
                min_market_cap=500_000_000.0,
                second_level_analyzer=None,
                decision_logger=decision_logger,
            )
        ),
        KlarmanLive(
            SethKlarman(
                edgar_cache=cache,
                min_market_cap=500_000_000.0,
                max_portfolio_size=20,
                downside_analyzer=None,
                decision_logger=decision_logger,
            )
        ),
        FisherLive(
            PhilipFisher(
                edgar_cache=cache,
                min_market_cap=1_000_000_000.0,
                max_portfolio_size=15,
                scuttlebutt_analyzer=None,
                decision_logger=decision_logger,
            )
        ),
    ]


class _DictLookup:
    """Trivial wrapper exposing .get(ticker) for the Strategy protocol."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def get(self, ticker: str):  # type: ignore[no-untyped-def]
        return self._data.get(ticker)


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
    """

    def __init__(
        self,
        *,
        market: str = "US",
        adapters: list[AgentAdapter] | None = None,
        portfolio_dir: Path = DEFAULT_PORTFOLIO_DIR,
        cost_bps: float = DEFAULT_COST_BPS,
        initial_cash: float = DEFAULT_INITIAL_CASH,
        rebalance_band: float = DEFAULT_REBALANCE_BAND,
        cache: EdgarCache | None = None,
        price_loader: PriceDataLoader | None = None,
        universe: FullMarketUniverse | None = None,
        pit_loader: PointInTimeLoader | None = None,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if market not in ("US", "TASE"):
            raise ValueError(
                f"unknown market {market!r}; expected 'US' or 'TASE'"
            )
        self.market = market
        self.cache = cache or EdgarCache()
        self.decision_logger = decision_logger or DecisionLogger()
        self.adapters = adapters or build_default_adapters(
            decision_logger=self.decision_logger,
            edgar_cache=self.cache,
        )
        self.portfolio_dir = portfolio_dir
        self.cost_bps = cost_bps
        self.initial_cash = initial_cash
        self.rebalance_band = rebalance_band
        self.price_loader = price_loader or PriceDataLoader()
        self.universe = universe or FullMarketUniverse(cache=self.cache)
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

    def run_mark_to_market(
        self, *, as_of: date | None = None
    ) -> list[AgentRunResult]:
        if self.market == "TASE":
            return self._tase_placeholder("close", as_of)
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
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            f"close-of-day price for {t} @ {as_of} failed: {exc}"
                        )
                        fresh[t] = None
                portfolio.mark_to_market(fresh)
                stamp = now_iso()
                portfolio.last_updated = stamp
                portfolio.last_close_run = stamp
                portfolio.save(directory=self.portfolio_dir)
                # Snapshot — no trades, just refreshed valuation.
                try:
                    snap = make_snapshot(portfolio, as_of=as_of, trades=[])
                    save_snapshot(snap)
                except Exception as exc:  # noqa: BLE001
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
            except Exception as exc:  # noqa: BLE001
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
        return results

    # ------------------------------------------------------------------
    def run(
        self, *, as_of: date | None = None, force: bool = False
    ) -> list[AgentRunResult]:
        if self.market == "TASE":
            return self._tase_placeholder("open", as_of)
        as_of = as_of or date.today()
        logger.info(f"daily run for {as_of}")
        self.universe._ensure_loaded()  # noqa: SLF001 — public API surface
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
            except Exception as exc:  # noqa: BLE001
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
        if not force and portfolio.last_open_run[:10] == as_of.isoformat():
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

        scan = adapter.run_scan(
            as_of,
            members,
            _DictLookup(prices),
            _DictLookup(fundamentals),
        )
        target_tickers = {t.ticker for t in scan.targets}
        trades: list[TradeRecord] = []

        # ---- SELLs: positions that left the target list ---------------
        # Skip if no targets — that's a "no-trade" signal (e.g. universe
        # produced no candidates) and we'd otherwise liquidate everything.
        if scan.targets:
            for pos in list(portfolio.positions):
                if pos.ticker in target_tickers:
                    continue
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
        for target in scan.targets:
            if portfolio.has(target.ticker):
                # Refresh the why string on the existing position so the
                # dashboard always shows the *current* rationale.
                idx = portfolio._index_of(target.ticker)  # noqa: SLF001
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
            target_dollars = nav_after_sells * target.weight
            if target_dollars > portfolio.cash:
                target_dollars = portfolio.cash * 0.99  # leave a sliver for cost
            # Was `target_dollars < price` — a $50 slot in a $500 stock
            # bought nothing and the cash sat idle. Fractional shares
            # make any positive target executable; the floor now only
            # rejects amounts too small to be worth a trade.
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
                why_en=w.why_en,
                why_he=w.why_he,
            )
            for w in scan.watchlist
        ]
        portfolio.set_watchlist(watch_entries)

        stamp = now_iso()
        portfolio.last_updated = stamp
        portfolio.last_open_run = stamp
        portfolio.save(directory=self.portfolio_dir)

        # Persist a daily snapshot for History tab + Today's-activity.
        try:
            snap = make_snapshot(portfolio, as_of=as_of, trades=trades)
            save_snapshot(snap)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{as_of}: snapshot save for {adapter.name} failed: {exc}")

        return AgentRunResult(
            agent=adapter.name,
            portfolio=portfolio,
            targets=scan.targets,
            watchlist=scan.watchlist,
            trades=trades,
            universe_size=scan.universe_size,
        )

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
            if spend < MIN_TRADE_USD:
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
            except Exception as exc:  # noqa: BLE001
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
            except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"decision log SELL {pos.ticker} failed: {exc}")


__all__ = [
    "AgentRunResult",
    "DailyRunner",
    "build_default_adapters",
]
