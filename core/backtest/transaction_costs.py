"""Transaction cost models for the backtest engine.

Three models are provided:

* :class:`ZeroCost` — theoretical baseline
* :class:`PercentageCost` — N basis points per side (default 10 bps)
* :class:`PerShareCost` — flat $/share (e.g. IBKR-style)

Costs are computed per *side* (buy or sell). For a round-trip trade
the agent pays twice.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class CostModel(ABC):
    """Abstract base for transaction cost models."""

    @abstractmethod
    def cost(self, *, shares: float, price: float) -> float:
        """Return the dollar cost of executing one side of a trade.

        Args:
            shares: Number of shares (always positive).
            price: Per-share price in USD.

        Returns:
            Dollar cost of the trade. Always non-negative.
        """

    def name(self) -> str:
        """Human-readable name of the model — for logging and reports."""
        return type(self).__name__


@dataclass(frozen=True)
class ZeroCost(CostModel):
    """No-cost model — for theoretical comparison."""

    def cost(self, *, shares: float, price: float) -> float:
        if shares < 0 or price < 0:
            raise ValueError(f"shares and price must be non-negative; got {shares=}, {price=}")
        return 0.0


@dataclass(frozen=True)
class PercentageCost(CostModel):
    """Cost is a fraction of trade notional value.

    Example: ``PercentageCost(0.001)`` means 10 basis points per side.
    Most retail-friendly model and the engine default.
    """

    rate: float = 0.001

    def __post_init__(self) -> None:
        if self.rate < 0 or self.rate > 0.05:
            raise ValueError(f"rate must be in [0, 0.05]; got {self.rate}")

    def cost(self, *, shares: float, price: float) -> float:
        if shares < 0 or price < 0:
            raise ValueError(f"shares and price must be non-negative; got {shares=}, {price=}")
        return shares * price * self.rate

    def name(self) -> str:
        return f"PercentageCost({self.rate * 10000:.0f} bps)"


@dataclass(frozen=True)
class PerShareCost(CostModel):
    """Flat dollar charge per share traded — Interactive-Brokers style."""

    rate: float = 0.005

    def __post_init__(self) -> None:
        if self.rate < 0:
            raise ValueError(f"rate must be non-negative; got {self.rate}")

    def cost(self, *, shares: float, price: float) -> float:
        if shares < 0 or price < 0:
            raise ValueError(f"shares and price must be non-negative; got {shares=}, {price=}")
        return shares * self.rate

    def name(self) -> str:
        return f"PerShareCost(${self.rate:.4f}/share)"


__all__ = ["CostModel", "PerShareCost", "PercentageCost", "ZeroCost"]
