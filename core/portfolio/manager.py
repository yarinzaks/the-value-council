"""Per-agent portfolio state and operations.

Each agent owns a :class:`Portfolio` persisted to
``agents/<name>/portfolio.json``. Every buy/sell mutates the state and
appends an entry to both the per-agent history and the global
``data/decisions.jsonl`` log.

Cash is denominated in USD. Positions are held in shares (allowing
fractional shares — common at modern brokers). Israeli positions
(``XXXX.TA``) are still tracked in USD-equivalent at the time of trade;
multi-currency support is out of scope for the foundation.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.config import get_settings
from core.exceptions import PortfolioError
from core.logger import get_logger

from .decision_log import DecisionLog


class Position(BaseModel):
    """A held position in a single ticker."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    shares: float = Field(gt=0)
    avg_cost: float = Field(gt=0)
    opened_at: datetime


class Portfolio:
    """Per-agent paper-money portfolio with persistent state.

    State lives at ``agents/<agent_name>/portfolio.json`` and is the
    single source of truth. Decisions also stream to the global log so
    the dashboard can render cross-agent history without scanning every
    portfolio file.
    """

    INITIAL_CASH_USD: float = 10_000.0

    def __init__(
        self,
        agent_name: str,
        *,
        initial_cash_usd: float = INITIAL_CASH_USD,
        agents_dir: Path | None = None,
        global_log: DecisionLog | None = None,
    ) -> None:
        self.logger = get_logger(f"core.portfolio.{agent_name}")
        self.agent_name = agent_name

        settings_loaded = False
        try:
            settings = get_settings()
            settings_loaded = True
        except Exception:  # noqa: BLE001 — tests may call without .env
            settings = None

        base_agents = (
            agents_dir
            if agents_dir is not None
            else settings.agents_dir
            if settings_loaded
            else Path(__file__).resolve().parent.parent.parent / "agents"
        )
        self._agent_dir = base_agents / agent_name
        self._agent_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._agent_dir / "portfolio.json"
        self._history_path = self._agent_dir / "history.jsonl"

        self.cash_usd: float = initial_cash_usd
        self.positions: dict[str, Position] = {}
        self.history: list[dict[str, Any]] = []
        self.initialized: bool = False

        if global_log is not None:
            self._global_log = global_log
        else:
            base_data = (
                settings.data_dir
                if settings_loaded
                else Path(__file__).resolve().parent.parent.parent / "data"
            )
            self._global_log = DecisionLog(base_data / "decisions.jsonl")

    # --- Buy / Sell ---------------------------------------------------------
    def buy(
        self,
        ticker: str,
        shares: float,
        price: float,
        rationale: dict[str, Any] | None = None,
    ) -> None:
        """Add or increase a position.

        Raises:
            PortfolioError: When inputs are invalid or cash is insufficient.
        """
        self._validate_trade(ticker, shares, price)
        cost = shares * price
        if cost > self.cash_usd + 1e-9:
            raise PortfolioError(
                f"insufficient cash: need ${cost:.2f}, have ${self.cash_usd:.2f}"
            )

        symbol = ticker.upper()
        existing = self.positions.get(symbol)
        if existing is None:
            self.positions[symbol] = Position(
                ticker=symbol,
                shares=shares,
                avg_cost=price,
                opened_at=datetime.now(UTC),
            )
        else:
            new_shares = existing.shares + shares
            new_avg = (
                (existing.shares * existing.avg_cost) + (shares * price)
            ) / new_shares
            self.positions[symbol] = existing.model_copy(
                update={"shares": new_shares, "avg_cost": new_avg}
            )

        self.cash_usd -= cost
        self.initialized = True
        self._record("BUY", symbol, shares, price, rationale)

    def sell(
        self,
        ticker: str,
        shares: float,
        price: float,
        rationale: dict[str, Any] | None = None,
    ) -> None:
        """Reduce or close a position.

        Raises:
            PortfolioError: When inputs are invalid, the position is
                missing, or shares to sell exceed shares held.
        """
        self._validate_trade(ticker, shares, price)
        symbol = ticker.upper()
        existing = self.positions.get(symbol)
        if existing is None:
            raise PortfolioError(f"no position in {symbol}")
        if shares > existing.shares + 1e-9:
            raise PortfolioError(
                f"oversell: holding {existing.shares} of {symbol}, asked to sell {shares}"
            )

        proceeds = shares * price
        remaining = existing.shares - shares
        if remaining <= 1e-9:
            del self.positions[symbol]
        else:
            self.positions[symbol] = existing.model_copy(update={"shares": remaining})

        self.cash_usd += proceeds
        self._record("SELL", symbol, shares, price, rationale)

    # --- Valuation ----------------------------------------------------------
    def current_value(self, price_lookup: Callable[[str], float]) -> float:
        """Return the total portfolio value (cash + positions at market).

        ``price_lookup`` is a function mapping ticker → current price.
        Tickers whose lookup raises are valued at their average cost as
        a conservative fallback and a warning is logged.
        """
        total = self.cash_usd
        for pos in self.positions.values():
            try:
                price = price_lookup(pos.ticker)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(
                    f"price lookup failed for {pos.ticker}; using avg_cost: {exc}"
                )
                price = pos.avg_cost
            total += pos.shares * price
        return total

    # --- Persistence --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialize the portfolio to a JSON-friendly dict."""
        return {
            "initialized": self.initialized,
            "cash_usd": self.cash_usd,
            "positions": [p.model_dump(mode="json") for p in self.positions.values()],
            "history": self.history,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        agent_name: str,
        *,
        agents_dir: Path | None = None,
    ) -> "Portfolio":
        """Recreate a Portfolio from its serialized dict."""
        portfolio = cls(
            agent_name,
            initial_cash_usd=float(data.get("cash_usd", cls.INITIAL_CASH_USD)),
            agents_dir=agents_dir,
        )
        portfolio.cash_usd = float(data.get("cash_usd", cls.INITIAL_CASH_USD))
        portfolio.initialized = bool(data.get("initialized", False))
        portfolio.history = list(data.get("history", []))
        portfolio.positions = {
            (raw := dict(row))["ticker"].upper(): Position.model_validate(raw)
            for row in data.get("positions", [])
        }
        return portfolio

    def save(self) -> None:
        """Write the portfolio state to disk."""
        with self._state_path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str, sort_keys=True)
        self.logger.debug(f"saved portfolio to {self._state_path}")

    @classmethod
    def load(cls, agent_name: str, *, agents_dir: Path | None = None) -> "Portfolio":
        """Load a portfolio from disk, creating fresh state if absent."""
        portfolio = cls(agent_name, agents_dir=agents_dir)
        if portfolio._state_path.exists():
            with portfolio._state_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data, agent_name, agents_dir=agents_dir)
        return portfolio

    # --- Internals ----------------------------------------------------------
    @staticmethod
    def _validate_trade(ticker: str, shares: float, price: float) -> None:
        if not ticker or not ticker.strip():
            raise PortfolioError("ticker must not be empty")
        if shares <= 0:
            raise PortfolioError(f"shares must be positive, got {shares}")
        if price <= 0:
            raise PortfolioError(f"price must be positive, got {price}")

    def _record(
        self,
        action: str,
        ticker: str,
        shares: float,
        price: float,
        rationale: dict[str, Any] | None,
    ) -> None:
        entry: dict[str, Any] = {
            "agent": self.agent_name,
            "action": action,
            "ticker": ticker,
            "shares": shares,
            "price": price,
            "value": shares * price,
            "cash_after": self.cash_usd,
            "timestamp": datetime.now(UTC).isoformat(),
            "rationale": rationale or {},
        }
        self.history.append(entry)
        # Append to the per-agent history file too — full-fidelity audit trail.
        with self._history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str, sort_keys=True) + "\n")
        self._global_log.append(entry)
        self.logger.info(
            f"{action} {shares} {ticker} @ ${price:.2f} (cash now ${self.cash_usd:.2f})"
        )


__all__ = ["Portfolio", "Position"]
