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

from agents.dreman.contrarian import DavidDreman
from agents.graham.net_net import BenjaminGraham
from agents.greenblatt.magic_formula import MagicFormula
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
    DremanLive,
    GrahamLive,
    GreenblattLive,
    LiveTarget,
    LiveWatch,
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


def build_default_adapters(
    *, decision_logger: DecisionLogger
) -> list[AgentAdapter]:
    """Construct the four currently-operational live adapters."""
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
        adapters: Live adapters to run. Default = the four built-in agents.
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
            decision_logger=self.decision_logger
        )
        self.portfolio_dir = portfolio_dir
        self.cost_bps = cost_bps
        self.initial_cash = initial_cash
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
                # Fetch fresh prices for every held ticker.
                tickers = [p.ticker for p in portfolio.positions]
                fresh: dict[str, float | None] = {}
                for t in tickers:
                    try:
                        fresh[t] = self.price_loader.get_price_on(t, as_of)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            f"close-of-day price for {t} @ {as_of} failed: {exc}"
                        )
                        fresh[t] = None
                portfolio.mark_to_market(fresh)
                portfolio.last_updated = now_iso()
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
    def run(self, *, as_of: date | None = None) -> list[AgentRunResult]:
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
                result = self._run_one(adapter, as_of, members, prices, fundamentals)
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
    ) -> AgentRunResult:
        portfolio = LivePortfolio.load_or_seed(
            adapter.name,
            directory=self.portfolio_dir,
            initial_cash=self.initial_cash,
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
                price = held_prices.get(pos.ticker) or pos.current_price
                if price is None or price <= 0:
                    logger.warning(
                        f"{as_of}: cannot sell {pos.ticker} — no price; skipping"
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
            if target_dollars < price:
                logger.info(
                    f"{as_of}: skipping {target.ticker} — "
                    f"target ${target_dollars:.2f} < price ${price:.2f}"
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

        portfolio.last_updated = now_iso()
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
                    decision="BUY",
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
                    decision="SELL",
                    agent=agent,
                    timestamp=f"{as_of.isoformat()}T00:00:00+00:00",
                    criteria_met=["rotated out of target portfolio"],
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
