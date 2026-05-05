"""Unit tests for :class:`BacktestPortfolio`."""

from __future__ import annotations

from datetime import date

import pytest

from core.backtest.portfolio import BacktestPortfolio
from core.backtest.transaction_costs import PercentageCost, ZeroCost
from core.exceptions import PortfolioError


class TestInitialState:
    def test_starts_with_initial_cash_no_holdings(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        assert pf.cash == 10_000.0
        assert len(pf.holdings) == 0
        assert len(pf.trades) == 0

    def test_invalid_initial_cash_raises(self) -> None:
        with pytest.raises(PortfolioError):
            BacktestPortfolio(initial_cash=0)
        with pytest.raises(PortfolioError):
            BacktestPortfolio(initial_cash=-100)


class TestBuyAndSell:
    def test_buy_creates_position_and_deducts_cash(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        pf.buy(trade_date=date(2020, 1, 2), ticker="AAPL", shares=10, price=150)
        assert "AAPL" in pf.holdings
        assert pf.holdings["AAPL"].shares == 10
        assert pf.cash == pytest.approx(10_000 - 1500)

    def test_buy_with_costs_deducts_extra(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=PercentageCost(0.001))
        pf.buy(trade_date=date(2020, 1, 2), ticker="AAPL", shares=10, price=150)
        # 10 * 150 = 1500 notional, plus 1500 * 0.001 = 1.5 cost
        assert pf.cash == pytest.approx(10_000 - 1500 - 1.5)
        assert pf.total_costs_paid == pytest.approx(1.5)

    def test_buy_insufficient_cash_raises(self) -> None:
        pf = BacktestPortfolio(initial_cash=100, cost_model=ZeroCost())
        with pytest.raises(PortfolioError, match="insufficient cash"):
            pf.buy(trade_date=date(2020, 1, 2), ticker="AAPL", shares=10, price=150)

    def test_sell_full_position_removes_holding(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        pf.buy(trade_date=date(2020, 1, 2), ticker="AAPL", shares=10, price=100)
        pf.sell(trade_date=date(2020, 6, 1), ticker="AAPL", shares=10, price=120)
        assert "AAPL" not in pf.holdings
        assert pf.cash == pytest.approx(10_000 - 1000 + 1200)

    def test_oversell_raises(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        pf.buy(trade_date=date(2020, 1, 2), ticker="AAPL", shares=10, price=100)
        with pytest.raises(PortfolioError, match="oversell"):
            pf.sell(trade_date=date(2020, 6, 1), ticker="AAPL", shares=11, price=120)

    def test_sell_without_position_raises(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        with pytest.raises(PortfolioError, match="no position"):
            pf.sell(trade_date=date(2020, 1, 2), ticker="AAPL", shares=1, price=100)

    def test_buy_blends_avg_cost(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        pf.buy(trade_date=date(2020, 1, 2), ticker="AAPL", shares=10, price=100)
        pf.buy(trade_date=date(2020, 6, 1), ticker="AAPL", shares=10, price=200)
        assert pf.holdings["AAPL"].shares == 20
        assert pf.holdings["AAPL"].avg_cost == pytest.approx(150)


class TestExecuteOrders:
    def test_target_shares_zero_exits_position(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        pf.buy(trade_date=date(2020, 1, 2), ticker="AAPL", shares=10, price=100)
        pf.execute_orders(date(2020, 6, 1), {"AAPL": 0.0}, {"AAPL": 110.0})
        assert "AAPL" not in pf.holdings

    def test_target_increase_buys_more(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        pf.buy(trade_date=date(2020, 1, 2), ticker="AAPL", shares=10, price=100)
        pf.execute_orders(date(2020, 6, 1), {"AAPL": 15.0}, {"AAPL": 110.0})
        assert pf.holdings["AAPL"].shares == 15

    def test_missing_price_skips_trade(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        pf.buy(trade_date=date(2020, 1, 2), ticker="AAPL", shares=10, price=100)
        # No price for AAPL — should not crash, position unchanged
        pf.execute_orders(date(2020, 6, 1), {"AAPL": 0.0}, prices={})
        assert "AAPL" in pf.holdings


class TestExecuteTargetWeights:
    def test_50_50_weights_split_correctly(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        prices = {"AAPL": 100.0, "MSFT": 200.0}
        pf.execute_target_weights(date(2020, 1, 2), {"AAPL": 0.5, "MSFT": 0.5}, prices)
        # 50% of 10k = 5000 in each. AAPL: 50 shares; MSFT: 25 shares.
        assert pf.holdings["AAPL"].shares == pytest.approx(50.0)
        assert pf.holdings["MSFT"].shares == pytest.approx(25.0)

    def test_negative_weights_raise(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        with pytest.raises(PortfolioError):
            pf.execute_target_weights(
                date(2020, 1, 2), {"AAPL": -0.5}, {"AAPL": 100.0}
            )

    def test_weights_above_one_raise(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        with pytest.raises(PortfolioError):
            pf.execute_target_weights(
                date(2020, 1, 2),
                {"AAPL": 0.6, "MSFT": 0.6},
                {"AAPL": 100.0, "MSFT": 100.0},
            )


class TestValuation:
    def test_value_with_prices(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        pf.buy(trade_date=date(2020, 1, 2), ticker="AAPL", shares=10, price=100)
        # cash now 9000, plus 10 shares × 110 = 1100
        assert pf.value({"AAPL": 110.0}) == pytest.approx(10_100.0)

    def test_snapshot_records_history(self) -> None:
        pf = BacktestPortfolio(initial_cash=10_000.0, cost_model=ZeroCost())
        snap = pf.snapshot(date(2020, 1, 2), {})
        assert len(pf.nav_history) == 1
        assert snap.nav == 10_000.0
        assert snap.cash == 10_000.0
