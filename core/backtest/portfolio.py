"""Backtest portfolio — NAV-tracking, cost-aware, history-keeping.

Distinct from ``core.portfolio.Portfolio`` (which is the live agent's
state). This portfolio is in-memory only, optimized for fast simulation
of millions of trades.

Usage::

    pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=PercentageCost())
    pf.execute_orders(date(2020, 1, 31), {"AAPL": 100, "MSFT": 200}, prices)
    nav = pf.value_at(date(2020, 1, 31), prices)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from core.exceptions import PortfolioError
from core.logger import get_logger

from .transaction_costs import CostModel, PercentageCost

logger = get_logger("core.backtest.portfolio")


@dataclass
class Holding:
    """One position held in a backtest portfolio."""

    ticker: str
    shares: float
    avg_cost: float

    def market_value(self, price: float) -> float:
        return self.shares * price


@dataclass
class TradeRecord:
    """Audit record of a single executed trade."""

    trade_date: date
    ticker: str
    side: str  # "BUY" or "SELL"
    shares: float
    price: float
    notional: float
    cost: float
    cash_after: float


@dataclass
class NavSnapshot:
    """Portfolio NAV at a point in time."""

    snapshot_date: date
    cash: float
    positions_value: float
    nav: float
    n_positions: int


class BacktestPortfolio:
    """In-memory portfolio for backtesting.

    Differences from the live ``core.portfolio.Portfolio``:
    - No disk persistence (purely in-memory).
    - Tracks transaction costs explicitly.
    - Records every NAV snapshot for time-series construction.
    - Allows fractional shares (consistent with modern brokers and
      simpler position-sizing math).
    """

    def __init__(
        self,
        initial_cash: float = 10_000.0,
        cost_model: CostModel | None = None,
    ) -> None:
        if initial_cash <= 0:
            raise PortfolioError(f"initial_cash must be positive; got {initial_cash}")
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.cost_model: CostModel = cost_model or PercentageCost()
        self.holdings: dict[str, Holding] = {}
        self.trades: list[TradeRecord] = []
        self.nav_history: list[NavSnapshot] = []
        self.total_costs_paid: float = 0.0

    # ------------------------------------------------------------------
    # Trade execution
    # ------------------------------------------------------------------
    def buy(self, *, trade_date: date, ticker: str, shares: float, price: float) -> None:
        """Execute a buy. Raises if insufficient cash."""
        ticker = ticker.upper()
        self._validate_inputs(shares, price)
        cost = self.cost_model.cost(shares=shares, price=price)
        notional = shares * price
        total = notional + cost
        if total > self.cash + 1e-9:
            raise PortfolioError(
                f"insufficient cash: need ${total:.2f} (notional ${notional:.2f} + cost ${cost:.2f}), "
                f"have ${self.cash:.2f}"
            )
        existing = self.holdings.get(ticker)
        if existing is None:
            self.holdings[ticker] = Holding(
                ticker=ticker, shares=shares, avg_cost=price
            )
        else:
            new_shares = existing.shares + shares
            new_avg = (
                (existing.shares * existing.avg_cost) + (shares * price)
            ) / new_shares
            existing.shares = new_shares
            existing.avg_cost = new_avg
        self.cash -= total
        self.total_costs_paid += cost
        self.trades.append(
            TradeRecord(
                trade_date=trade_date,
                ticker=ticker,
                side="BUY",
                shares=shares,
                price=price,
                notional=notional,
                cost=cost,
                cash_after=self.cash,
            )
        )

    def sell(self, *, trade_date: date, ticker: str, shares: float, price: float) -> None:
        """Execute a sell. Raises if oversell."""
        ticker = ticker.upper()
        self._validate_inputs(shares, price)
        existing = self.holdings.get(ticker)
        if existing is None:
            raise PortfolioError(f"no position in {ticker}")
        if shares > existing.shares + 1e-9:
            raise PortfolioError(
                f"oversell: holding {existing.shares} of {ticker}, asked to sell {shares}"
            )
        cost = self.cost_model.cost(shares=shares, price=price)
        notional = shares * price
        proceeds = notional - cost
        new_shares = existing.shares - shares
        if new_shares <= 1e-9:
            del self.holdings[ticker]
        else:
            existing.shares = new_shares
            # avg_cost unchanged on sells (FIFO/avg-cost convention)
        self.cash += proceeds
        self.total_costs_paid += cost
        self.trades.append(
            TradeRecord(
                trade_date=trade_date,
                ticker=ticker,
                side="SELL",
                shares=shares,
                price=price,
                notional=notional,
                cost=cost,
                cash_after=self.cash,
            )
        )

    def execute_orders(
        self,
        trade_date: date,
        target_shares: Mapping[str, float],
        prices: Mapping[str, float],
    ) -> int:
        """Move toward target share counts, executing buys and sells.

        Args:
            trade_date: The date the trades execute on.
            target_shares: Desired share count per ticker. Tickers
                missing from this map are interpreted as "exit".
            prices: Prices to use for execution (typically that day's
                adjusted close).

        Returns:
            Number of trades executed.
        """
        target = {t.upper(): float(s) for t, s in target_shares.items()}
        # First pass: sell everything we want to reduce. This frees up
        # cash before we buy.
        n_executed = 0
        for ticker, holding in list(self.holdings.items()):
            target_shares_t = target.get(ticker, 0.0)
            if target_shares_t < holding.shares - 1e-9:
                price = prices.get(ticker)
                if price is None or price <= 0:
                    logger.warning(
                        f"{trade_date}: no price for {ticker}; skipping sell"
                    )
                    continue
                shares_to_sell = holding.shares - target_shares_t
                self.sell(
                    trade_date=trade_date,
                    ticker=ticker,
                    shares=shares_to_sell,
                    price=price,
                )
                n_executed += 1

        # Second pass: buy to reach targets.
        for ticker, target_shares_t in target.items():
            current = self.holdings.get(ticker)
            current_shares = current.shares if current else 0.0
            if target_shares_t > current_shares + 1e-9:
                price = prices.get(ticker)
                if price is None or price <= 0:
                    logger.warning(
                        f"{trade_date}: no price for {ticker}; skipping buy"
                    )
                    continue
                shares_to_buy = target_shares_t - current_shares
                try:
                    self.buy(
                        trade_date=trade_date,
                        ticker=ticker,
                        shares=shares_to_buy,
                        price=price,
                    )
                    n_executed += 1
                except PortfolioError as exc:
                    logger.warning(f"{trade_date}: {ticker} buy failed: {exc}")

        return n_executed

    def execute_target_weights(
        self,
        trade_date: date,
        target_weights: Mapping[str, float],
        prices: Mapping[str, float],
    ) -> int:
        """Convenience: convert target weights → target shares → execute.

        Cash floor: any weight that doesn't sum to 1.0 is held as cash.
        """
        weights = {t.upper(): float(w) for t, w in target_weights.items()}
        if any(w < 0 for w in weights.values()):
            raise PortfolioError("negative weights are not supported (long-only)")
        if sum(weights.values()) > 1.0 + 1e-9:
            raise PortfolioError(
                f"target weights sum to {sum(weights.values()):.4f} > 1.0"
            )

        nav = self.value(prices)
        target_shares: dict[str, float] = {}
        for ticker, w in weights.items():
            price = prices.get(ticker)
            if price is None or price <= 0:
                logger.warning(
                    f"{trade_date}: no price for {ticker}; weight {w} dropped"
                )
                continue
            dollars = nav * w
            target_shares[ticker] = dollars / price

        return self.execute_orders(trade_date, target_shares, prices)

    # ------------------------------------------------------------------
    # Valuation
    # ------------------------------------------------------------------
    def value(self, prices: Mapping[str, float]) -> float:
        """Return total NAV using the supplied prices."""
        positions_value = sum(
            h.shares * prices.get(t, h.avg_cost) for t, h in self.holdings.items()
        )
        return self.cash + positions_value

    def positions_value(self, prices: Mapping[str, float]) -> float:
        return sum(
            h.shares * prices.get(t, h.avg_cost) for t, h in self.holdings.items()
        )

    def snapshot(self, snapshot_date: date, prices: Mapping[str, float]) -> NavSnapshot:
        """Record and return a NavSnapshot for the given date."""
        pv = self.positions_value(prices)
        snap = NavSnapshot(
            snapshot_date=snapshot_date,
            cash=self.cash,
            positions_value=pv,
            nav=self.cash + pv,
            n_positions=len(self.holdings),
        )
        self.nav_history.append(snap)
        return snap

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_inputs(shares: float, price: float) -> None:
        if shares <= 0:
            raise PortfolioError(f"shares must be positive; got {shares}")
        if price <= 0:
            raise PortfolioError(f"price must be positive; got {price}")

    def __repr__(self) -> str:
        return (
            f"BacktestPortfolio(cash=${self.cash:.2f}, "
            f"positions={len(self.holdings)}, "
            f"trades={len(self.trades)}, "
            f"costs_paid=${self.total_costs_paid:.2f})"
        )


__all__ = ["BacktestPortfolio", "Holding", "NavSnapshot", "TradeRecord"]
