"""Unit tests for transaction cost models."""

from __future__ import annotations

import pytest

from core.backtest.transaction_costs import (
    PercentageCost,
    PerShareCost,
    ZeroCost,
)


class TestZeroCost:
    def test_zero(self) -> None:
        m = ZeroCost()
        assert m.cost(shares=100, price=50) == 0.0

    def test_negative_inputs_raise(self) -> None:
        with pytest.raises(ValueError):
            ZeroCost().cost(shares=-1, price=50)
        with pytest.raises(ValueError):
            ZeroCost().cost(shares=10, price=-1)


class TestPercentageCost:
    def test_default_is_10bps(self) -> None:
        m = PercentageCost()
        # 100 shares × $50 × 0.001 = $5
        assert m.cost(shares=100, price=50) == pytest.approx(5.0)

    def test_custom_rate(self) -> None:
        m = PercentageCost(rate=0.005)  # 50 bps
        assert m.cost(shares=10, price=100) == pytest.approx(5.0)

    def test_zero_shares_zero_cost(self) -> None:
        assert PercentageCost().cost(shares=0, price=100) == 0.0

    def test_invalid_rate_raises(self) -> None:
        with pytest.raises(ValueError):
            PercentageCost(rate=-0.01)
        with pytest.raises(ValueError):
            PercentageCost(rate=0.10)  # 10% is absurd

    def test_name_includes_bps(self) -> None:
        assert "10 bps" in PercentageCost(0.001).name()
        assert "50 bps" in PercentageCost(0.005).name()


class TestPerShareCost:
    def test_default_half_cent(self) -> None:
        m = PerShareCost()
        assert m.cost(shares=200, price=50) == pytest.approx(1.0)

    def test_independent_of_price(self) -> None:
        m = PerShareCost(0.01)
        assert m.cost(shares=100, price=10) == pytest.approx(1.0)
        assert m.cost(shares=100, price=1000) == pytest.approx(1.0)

    def test_negative_rate_raises(self) -> None:
        with pytest.raises(ValueError):
            PerShareCost(-0.001)
