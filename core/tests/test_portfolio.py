"""Unit tests for :class:`Portfolio` and :class:`DecisionLog`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.exceptions import PortfolioError
from core.portfolio import DecisionLog, Portfolio


@pytest.fixture
def tmp_agents(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path / "agents"


@pytest.fixture
def tmp_decisions(tmp_path: Path) -> DecisionLog:
    return DecisionLog(tmp_path / "data" / "decisions.jsonl")


@pytest.fixture
def portfolio(tmp_agents: Path, tmp_decisions: DecisionLog) -> Portfolio:
    return Portfolio(
        "test_agent",
        initial_cash_usd=10_000.0,
        agents_dir=tmp_agents,
        global_log=tmp_decisions,
    )


class TestBuy:
    def test_initial_buy_creates_position(self, portfolio: Portfolio) -> None:
        portfolio.buy("AAPL", shares=10, price=150.0, rationale={"reason": "good"})
        assert "AAPL" in portfolio.positions
        assert portfolio.positions["AAPL"].shares == 10
        assert portfolio.cash_usd == pytest.approx(10_000 - 1500)
        assert portfolio.initialized

    def test_second_buy_blends_avg_cost(self, portfolio: Portfolio) -> None:
        portfolio.buy("AAPL", 10, 100.0)
        portfolio.buy("AAPL", 10, 200.0)
        pos = portfolio.positions["AAPL"]
        assert pos.shares == 20
        assert pos.avg_cost == pytest.approx(150.0)

    def test_oversize_buy_raises(self, portfolio: Portfolio) -> None:
        with pytest.raises(PortfolioError, match="insufficient cash"):
            portfolio.buy("AAPL", 1000, 1000.0)

    @pytest.mark.parametrize(
        ("shares", "price"),
        [(0, 100), (-1, 100), (10, 0), (10, -1)],
    )
    def test_invalid_inputs_raise(
        self, portfolio: Portfolio, shares: float, price: float
    ) -> None:
        with pytest.raises(PortfolioError):
            portfolio.buy("AAPL", shares, price)


class TestSell:
    def test_partial_sell(self, portfolio: Portfolio) -> None:
        portfolio.buy("AAPL", 10, 100.0)
        portfolio.sell("AAPL", 4, 150.0)
        assert portfolio.positions["AAPL"].shares == 6
        assert portfolio.cash_usd == pytest.approx(10_000 - 1000 + 600)

    def test_full_sell_removes_position(self, portfolio: Portfolio) -> None:
        portfolio.buy("AAPL", 10, 100.0)
        portfolio.sell("AAPL", 10, 150.0)
        assert "AAPL" not in portfolio.positions

    def test_oversell_raises(self, portfolio: Portfolio) -> None:
        portfolio.buy("AAPL", 10, 100.0)
        with pytest.raises(PortfolioError, match="oversell"):
            portfolio.sell("AAPL", 11, 100.0)

    def test_sell_without_position_raises(self, portfolio: Portfolio) -> None:
        with pytest.raises(PortfolioError, match="no position"):
            portfolio.sell("AAPL", 1, 100.0)


class TestValuation:
    def test_value_with_lookup(self, portfolio: Portfolio) -> None:
        portfolio.buy("AAPL", 10, 100.0)
        portfolio.buy("MSFT", 5, 200.0)
        prices = {"AAPL": 150.0, "MSFT": 250.0}
        assert portfolio.current_value(lambda t: prices[t]) == pytest.approx(
            portfolio.cash_usd + 10 * 150 + 5 * 250
        )

    def test_value_falls_back_to_cost_on_lookup_failure(
        self, portfolio: Portfolio
    ) -> None:
        portfolio.buy("AAPL", 10, 100.0)

        def bad_lookup(_: str) -> float:
            raise RuntimeError("network down")

        # Falls back to avg_cost (100), so value = cash + 10*100.
        assert portfolio.current_value(bad_lookup) == pytest.approx(
            portfolio.cash_usd + 10 * 100
        )


class TestPersistence:
    def test_round_trip(self, portfolio: Portfolio, tmp_agents: Path) -> None:
        portfolio.buy("AAPL", 5, 100.0)
        portfolio.save()
        restored = Portfolio.load("test_agent", agents_dir=tmp_agents)
        assert restored.cash_usd == portfolio.cash_usd
        assert "AAPL" in restored.positions
        assert restored.positions["AAPL"].shares == 5
        assert restored.initialized

    def test_load_missing_returns_fresh(self, tmp_agents: Path) -> None:
        portfolio = Portfolio.load("brand_new", agents_dir=tmp_agents)
        assert portfolio.cash_usd == Portfolio.INITIAL_CASH_USD
        assert not portfolio.initialized


class TestDecisionLog:
    def test_append_and_read(self, tmp_decisions: DecisionLog) -> None:
        tmp_decisions.append({"action": "BUY", "ticker": "AAPL"})
        tmp_decisions.append({"action": "SELL", "ticker": "AAPL"})
        records = tmp_decisions.read_all()
        assert len(records) == 2
        assert records[0]["ticker"] == "AAPL"
        assert "timestamp" in records[0]

    def test_read_skips_malformed(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        path.write_text(
            json.dumps({"action": "BUY"}) + "\nNOT JSON\n" + json.dumps({"action": "SELL"}) + "\n"
        )
        log = DecisionLog(path)
        assert len(log.read_all()) == 2
