"""Strategy abstract base + backtest execution loop.

The runner ticks at month-ends (or any configured cadence). At each
tick it asks the strategy for target weights, executes orders, and
snapshots NAV. The result is a :class:`BacktestResult` with a daily
NAV time series plus per-trade audit log.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

import pandas as pd

from core.exceptions import ValueCouncilError
from core.logger import get_logger

from .data_loader import PriceDataLoader
from .point_in_time import (
    PointInTimeError,
    PointInTimeFinancials,
    PointInTimeLoader,
)
from .portfolio import BacktestPortfolio, NavSnapshot, TradeRecord
from .transaction_costs import CostModel, PercentageCost
from .universe import load_universe
from .universe_protocol import Universe

logger = get_logger("core.backtest.strategy_runner")

RebalanceFreq = Literal["daily", "weekly", "monthly", "quarterly", "annual"]


# ----------------------------------------------------------------------
# Lookup adapters — what strategies see when they `select`.
# ----------------------------------------------------------------------
class PriceLookup:
    """Strategy-facing wrapper over :class:`PriceDataLoader`."""

    def __init__(self, loader: PriceDataLoader, as_of: date) -> None:
        self._loader = loader
        self._as_of = as_of

    def get(self, ticker: str) -> float | None:
        """Most recent price on or before the strategy's as_of date."""
        return self._loader.get_price_on(ticker, self._as_of)


class FundamentalsLookup:
    """Strategy-facing wrapper over :class:`PointInTimeLoader`.

    :meth:`PointInTimeLoader.get_financials` raises when a filing exists
    but yields nothing usable — the right call there, because returning
    ``None`` would be indistinguishable from a company that never filed.
    But this is the screening seam: a strategy asks it about every name
    in a 6,600-ticker universe, and one unparseable 10-Q killed a
    five-hour backtest at the fourth rebalance (Buffett, FCHS, 2026-08-07).

    So the exception stops here and becomes ``None``. That is the honest
    answer to the strategy's actual question — *can I value this
    company?* — and no doctrine buys what it cannot value. The tickers
    are kept in :attr:`unparseable` so the runner can report how many
    names a rebalance dropped for this reason rather than swallowing it.
    """

    def __init__(self, loader: PointInTimeLoader | None, as_of: date) -> None:
        self._loader = loader
        self._as_of = as_of
        #: Tickers whose filing was found but could not be parsed.
        self.unparseable: set[str] = set()

    def get(self, ticker: str) -> PointInTimeFinancials | None:
        if self._loader is None:
            return None
        try:
            return self._loader.get_financials(ticker, self._as_of)
        except PointInTimeError as exc:
            # Per-ticker at debug: a bad quarter for a data vendor can
            # produce hundreds of these, and a warning each would bury
            # the run's real output. The count is logged once, below.
            self.unparseable.add(ticker.upper())
            logger.debug(f"{ticker} @ {self._as_of}: unusable filing — {exc}")
            return None


# ----------------------------------------------------------------------
# Strategy ABC
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class HeldPosition:
    """What a strategy is allowed to know about a position it owns.

    Deliberately narrow. A strategy needs to answer "how long have I
    held this, at what price, and how has it done" to express an exit
    rule; it does not need the portfolio object, and handing it one
    would let a doctrine reach into cash and weights that the runner
    owns.
    """

    ticker: str
    shares: float
    entry_price: float
    entry_date: date
    current_price: float

    def days_held_at(self, as_of: date) -> int:
        """Calendar days since entry. Zero for a same-day purchase."""
        return max(0, (as_of - self.entry_date).days)

    @property
    def return_pct(self) -> float | None:
        """Unrealised return in percent, or None when the basis is unusable."""
        if self.entry_price <= 0 or self.current_price <= 0:
            return None
        return (self.current_price / self.entry_price - 1.0) * 100.0


class Strategy(ABC):
    """Abstract base class for backtest strategies.

    Subclasses implement :meth:`select`, returning target weights as a
    fraction of NAV. The runner handles execution, cost modeling, and
    NAV tracking.
    """

    name: str = "base"

    @abstractmethod
    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
        *,
        held: Mapping[str, HeldPosition] | None = None,
    ) -> dict[str, float]:
        """Return target weights for ``as_of``.

        Args:
            as_of: The rebalance date.
            universe: Tickers in the survivorship-bias-free universe
                on this date.
            prices: Lookup for prices known on this date.
            fundamentals: Lookup for fundamentals known on this date.
            held: What the agent currently owns, keyed by ticker, or
                ``None`` when the caller does not track positions.

                Without this a strategy could not express an exit rule
                at all, so every agent inherited the same one — "sold
                because you left today's top N". Greenblatt's twelve
                month hold, Fisher's decades, Lynch's category-specific
                exits and Schloss's fifty-percent rule were all
                unimplementable, and eight doctrines whose real-world
                turnover differed by an order of magnitude traded
                identically.

        Returns:
            Dict ``{ticker: weight}`` where weights are non-negative
            fractions of NAV. Sum must be ≤ 1.0; the residual is held
            as cash.
        """


class BuyAndHoldSPY(Strategy):
    """Reference strategy — 100% SPY, never rebalanced.

    Used as the sanity check: backtesting this should reproduce the
    actual SPY total return for any window.
    """

    name = "buy_and_hold_spy"

    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
        *,
        held: Mapping[str, HeldPosition] | None = None,
    ) -> dict[str, float]:
        return {"SPY": 1.0}


class EqualWeightUniverse(Strategy):
    """Equal weight across the universe, capped at ``max_positions``."""

    name = "equal_weight_universe"

    def __init__(self, max_positions: int = 50) -> None:
        if max_positions <= 0:
            raise ValueError(f"max_positions must be positive; got {max_positions}")
        self.max_positions = max_positions

    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
        *,
        held: Mapping[str, HeldPosition] | None = None,
    ) -> dict[str, float]:
        # Pick the first N alphabetically — stable, reproducible.
        # Real strategies will override this with their own ranking logic.
        chosen = sorted(universe)[: self.max_positions]
        # Filter out any with no price (e.g., halted/delisted on this date)
        priced = [t for t in chosen if prices.get(t) is not None]
        if not priced:
            return {}
        weight = 1.0 / len(priced)
        return {t: weight for t in priced}


# ----------------------------------------------------------------------
# Runner config + result
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class RunnerConfig:
    """Configuration for one backtest run."""

    start_date: date
    end_date: date
    initial_cash: float = 10_000.0
    rebalance_freq: RebalanceFreq = "monthly"
    benchmark_ticker: str = "SPY"
    cost_model: CostModel = field(default_factory=PercentageCost)
    use_universe: bool = True  # if False, strategy works without universe input
    use_fundamentals: bool = False  # if True, point-in-time loader is constructed

    def __post_init__(self) -> None:
        if self.start_date >= self.end_date:
            raise ValueError(
                f"start_date {self.start_date} must be < end_date {self.end_date}"
            )
        if self.initial_cash <= 0:
            raise ValueError(f"initial_cash must be positive; got {self.initial_cash}")


@dataclass
class BacktestResult:
    """Output of a backtest run."""

    run_id: str
    config: RunnerConfig
    strategy_name: str
    nav_series: pd.Series  # daily NAV, indexed by date
    benchmark_nav_series: pd.Series  # daily benchmark NAV (initial_cash * benchmark_total_return)
    trades: list[TradeRecord]
    snapshots: list[NavSnapshot]
    total_costs_paid: float
    n_rebalances: int


class BacktestError(ValueCouncilError):
    """Raised when a backtest run fails."""


# ----------------------------------------------------------------------
# Date helpers
# ----------------------------------------------------------------------
def _rebalance_dates(
    start: date, end: date, freq: RebalanceFreq, trading_calendar: pd.DatetimeIndex
) -> list[date]:
    """Return rebalance dates clipped to the given trading calendar."""
    cal = trading_calendar[
        (trading_calendar.date >= start) & (trading_calendar.date <= end)
    ]
    if len(cal) == 0:
        return []
    if freq == "daily":
        return [d.date() for d in cal]
    if freq == "weekly":
        # Last trading day of each ISO week
        return [g.date().max() for _, g in pd.Series(cal).groupby(
            pd.Series(cal).dt.to_period("W").astype(str)
        )]
    if freq == "monthly":
        s = pd.Series(cal)
        return sorted({g.max().date() for _, g in s.groupby(s.dt.to_period("M"))})
    if freq == "quarterly":
        s = pd.Series(cal)
        return sorted({g.max().date() for _, g in s.groupby(s.dt.to_period("Q"))})
    if freq == "annual":
        s = pd.Series(cal)
        return sorted({g.max().date() for _, g in s.groupby(s.dt.to_period("Y"))})
    raise ValueError(f"unknown frequency {freq!r}")


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------
class BacktestRunner:
    """Executes a strategy over a historical window and records results."""

    def __init__(
        self,
        config: RunnerConfig,
        *,
        price_loader: PriceDataLoader | None = None,
        pit_loader: PointInTimeLoader | None = None,
        universe: Universe | None = None,
    ) -> None:
        self.config = config
        self.price_loader = price_loader or PriceDataLoader()
        # PointInTime loader is heavy; only build if requested
        if config.use_fundamentals:
            self.pit_loader = pit_loader or PointInTimeLoader()
        else:
            self.pit_loader = pit_loader  # may be None
        # Universe loaded lazily if needed
        if config.use_universe and universe is None:
            try:
                self.universe = load_universe()
            except Exception as exc:
                logger.warning(f"could not load universe ({exc}); proceeding without")
                self.universe = None
        else:
            self.universe = universe

    def run(self, strategy: Strategy) -> BacktestResult:
        """Execute the strategy and return a complete :class:`BacktestResult`."""
        cfg = self.config
        run_id = f"{strategy.name}_{datetime.now().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
        logger.info(
            f"starting backtest {run_id}: {strategy.name} "
            f"{cfg.start_date}..{cfg.end_date} ${cfg.initial_cash:.0f} "
            f"freq={cfg.rebalance_freq} costs={cfg.cost_model.name()}"
        )

        # 1. Pull benchmark prices for the entire window — this gives
        # us the trading calendar and the benchmark NAV series.
        bench_history = self.price_loader.get_history(
            cfg.benchmark_ticker, cfg.start_date, cfg.end_date
        )
        if bench_history.empty:
            raise BacktestError(
                f"no price history for benchmark {cfg.benchmark_ticker} "
                f"between {cfg.start_date} and {cfg.end_date}"
            )
        trading_calendar = bench_history.index
        bench_close = bench_history["adj_close"].dropna()
        if bench_close.empty:
            raise BacktestError(f"benchmark {cfg.benchmark_ticker} has no adj_close data")

        # 2. Compute the rebalance schedule
        rebalance_dates = _rebalance_dates(
            cfg.start_date, cfg.end_date, cfg.rebalance_freq, trading_calendar
        )
        if not rebalance_dates:
            raise BacktestError("no rebalance dates in window")

        # 3. Initialize the portfolio
        portfolio = BacktestPortfolio(
            initial_cash=cfg.initial_cash, cost_model=cfg.cost_model
        )

        # 4. Run the loop. We iterate over ALL trading days for NAV
        # snapshots, and rebalance on the rebalance dates.
        # Always include the first trading day as a rebalance — otherwise
        # an annual / quarterly backtest would sit in cash from start_date
        # until the first end-of-period, missing the early returns.
        first_trading_day = trading_calendar[0].date()
        if first_trading_day not in rebalance_dates:
            rebalance_dates = sorted({first_trading_day, *rebalance_dates})
        rebalance_set = set(rebalance_dates)
        # Pre-fetch SPY (we may need to buy it for BuyAndHoldSPY)
        bench_history.copy() if cfg.benchmark_ticker == "SPY" else None

        # Universe membership cache per rebalance date
        universe_cache: dict[date, list[str]] = {}

        # Pre-load all tickers we may need: union of universe across all
        # rebalance dates, plus SPY for benchmark and BuyAndHoldSPY.
        if self.universe is not None and cfg.use_universe:
            for d in rebalance_dates:
                universe_cache[d] = self.universe.constituents_at(d)

        # Track NAV snapshots
        for ts in trading_calendar:
            today = ts.date()

            # Build today's price map for current holdings + benchmark + SPY.
            tickers_to_price = set(portfolio.holdings.keys()) | {cfg.benchmark_ticker, "SPY"}
            prices_today = self._prices_at(tickers_to_price, today)

            # Rebalance, if scheduled
            if today in rebalance_set:
                if cfg.use_universe and self.universe is not None:
                    universe_today = universe_cache.get(today, self.universe.constituents_at(today))
                else:
                    universe_today = []

                price_lookup = PriceLookup(self.price_loader, today)
                fund_lookup = FundamentalsLookup(self.pit_loader, today)

                weights = strategy.select(today, universe_today, price_lookup, fund_lookup)
                if fund_lookup.unparseable:
                    sample = ", ".join(sorted(fund_lookup.unparseable)[:5])
                    logger.warning(
                        f"{today}: {len(fund_lookup.unparseable)} of "
                        f"{len(universe_today)} names dropped — filing found "
                        f"but unparseable (e.g. {sample})"
                    )
                # Add prices for any NEW tickers the strategy wants
                new_tickers = [t for t in weights if t not in prices_today]
                if new_tickers:
                    prices_today.update(self._prices_at(set(new_tickers), today))
                portfolio.execute_target_weights(today, weights, prices_today)

            # NAV snapshot every day
            portfolio.snapshot(today, prices_today)

        # 5. Build NAV series
        nav_series = pd.Series(
            data=[s.nav for s in portfolio.nav_history],
            index=pd.DatetimeIndex([s.snapshot_date for s in portfolio.nav_history]),
            name=f"nav_{strategy.name}",
        )

        # 6. Build benchmark NAV: scale benchmark close to start at initial_cash
        bench_aligned = bench_close.reindex(nav_series.index, method="ffill").dropna()
        if bench_aligned.empty:
            raise BacktestError("benchmark has no overlap with trading calendar")
        bench_scaled = bench_aligned / bench_aligned.iloc[0] * cfg.initial_cash
        bench_scaled.name = f"nav_benchmark_{cfg.benchmark_ticker}"

        # Align nav_series to bench_scaled
        nav_series = nav_series.reindex(bench_scaled.index, method="ffill")

        result = BacktestResult(
            run_id=run_id,
            config=cfg,
            strategy_name=strategy.name,
            nav_series=nav_series,
            benchmark_nav_series=bench_scaled,
            trades=portfolio.trades,
            snapshots=portfolio.nav_history,
            total_costs_paid=portfolio.total_costs_paid,
            n_rebalances=len(rebalance_dates),
        )
        logger.info(
            f"backtest {run_id} complete: "
            f"final NAV ${nav_series.iloc[-1]:.2f}, "
            f"benchmark ${bench_scaled.iloc[-1]:.2f}, "
            f"trades {len(portfolio.trades)}, "
            f"costs ${portfolio.total_costs_paid:.2f}"
        )
        return result

    def _prices_at(self, tickers: set[str], as_of: date) -> dict[str, float]:
        """Best-effort price lookup for a set of tickers at as_of."""
        out: dict[str, float] = {}
        for t in tickers:
            try:
                p = self.price_loader.get_price_on(t, as_of)
                if p is not None and p > 0:
                    out[t] = p
            except Exception as exc:
                logger.debug(f"price lookup failed for {t} on {as_of}: {exc}")
        return out


__all__ = [
    "BacktestError",
    "BacktestResult",
    "BacktestRunner",
    "BuyAndHoldSPY",
    "EqualWeightUniverse",
    "FundamentalsLookup",
    "PriceLookup",
    "RebalanceFreq",
    "RunnerConfig",
    "Strategy",
]
