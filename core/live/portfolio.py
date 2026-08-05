"""Persistent paper-trading portfolio for one agent.

Single-agent state — cash + positions + watchlist — plus the rules for
applying buy/sell orders at a given price with transaction costs.
Same accounting discipline as the backtest engine; the only difference
is that state is loaded from / written to JSON on disk so it persists
across daily runs.

JSON schema is fixed (see :class:`LivePortfolio.to_dict`). Bilingual
``why_en`` / ``why_he`` rationale fields are populated by the per-agent
adapter at trade time.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from core.exceptions import ValueCouncilError
from core.logger import get_logger

logger = get_logger("core.live.portfolio")

DEFAULT_INITIAL_CASH: float = 10_000.0
DEFAULT_COST_BPS: float = 0.001  # 0.1% per trade

#: Decimal places share counts are rounded to. Matches what ``to_dict``
#: already persists, so a round-trip through JSON is lossless. Six
#: places on a $10,000 book is sub-cent precision.
SHARE_PRECISION: int = 6

from core.paths import portfolios_dir as _portfolios_dir

# Resolved via the single ``core.paths`` module so that env-var
# override (VALUE_COUNCIL_DATA_DIR) and the macOS ~/Library default
# work consistently across all modules.
DEFAULT_PORTFOLIO_DIR = _portfolios_dir()


class LivePortfolioError(ValueCouncilError):
    """Raised when portfolio state cannot be loaded, saved, or mutated."""


@dataclass
class Position:
    """One open long position. Cost basis = entry_price × shares."""

    ticker: str
    shares: float
    entry_price: float
    entry_date: str  # ISO date — deliberately a string so JSON round-trips cleanly
    current_price: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    weight_pct: float = 0.0
    why_en: str = ""
    why_he: str = ""

    @property
    def market_value(self) -> float:
        return self.shares * self.current_price

    @property
    def cost_basis(self) -> float:
        return self.shares * self.entry_price


@dataclass
class WatchEntry:
    """A name an agent is tracking but has not bought.

    ``entry_trigger`` is a free-text description of what the agent is
    waiting for ("rank enters top 30", "P/B drops below 0.75", etc.) —
    each agent adapter writes its own. ``entry_price_target`` is
    optional and populated only when the trigger is price-based.
    """

    ticker: str
    identified_date: str
    current_rank: int | None = None
    entry_trigger: str = ""
    entry_price_target: float | None = None
    why_en: str = ""
    why_he: str = ""


@dataclass
class LivePortfolio:
    agent: str
    cash: float = DEFAULT_INITIAL_CASH
    positions: list[Position] = field(default_factory=list)
    watchlist: list[WatchEntry] = field(default_factory=list)
    # ``last_updated`` is the most recent of any kind of run (open or
    # close) — kept for backward compat. The split fields below let
    # the dashboard show "last open scan @ time" vs "last close mark
    # @ time" separately.
    last_updated: str = ""
    last_open_run: str = ""
    last_close_run: str = ""
    initial_cash: float = DEFAULT_INITIAL_CASH
    cumulative_costs: float = 0.0

    # ------------------------------------------------------------------
    # Derived metrics (computed against current_price on positions)
    # ------------------------------------------------------------------
    @property
    def invested(self) -> float:
        return sum(p.market_value for p in self.positions)

    @property
    def total_nav(self) -> float:
        return self.cash + self.invested

    @property
    def cumulative_return_pct(self) -> float:
        if self.initial_cash <= 0:
            return 0.0
        return (self.total_nav / self.initial_cash - 1.0) * 100.0

    # ------------------------------------------------------------------
    # Mark-to-market — refresh prices on existing positions
    # ------------------------------------------------------------------
    def mark_to_market(self, prices: dict[str, float | None]) -> None:
        """Update every position's ``current_price``, ``pnl_*``, and
        weight using the latest prices. Tickers with no price keep their
        last known mark (defensive against transient yfinance failures).
        """
        nav_after = self.cash
        for p in self.positions:
            new_price = prices.get(p.ticker)
            if new_price is not None and new_price > 0:
                p.current_price = float(new_price)
            elif p.current_price <= 0:
                # First mark and no price available — fall back to entry.
                p.current_price = p.entry_price
            p.pnl_usd = (p.current_price - p.entry_price) * p.shares
            denom = p.entry_price * p.shares
            p.pnl_pct = (p.pnl_usd / denom * 100.0) if denom > 0 else 0.0
            nav_after += p.market_value
        # Second pass for weights now that NAV is known.
        for p in self.positions:
            p.weight_pct = (
                (p.market_value / nav_after * 100.0) if nav_after > 0 else 0.0
            )

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def sell(
        self,
        ticker: str,
        *,
        price: float,
        shares: float | None = None,
        cost_bps: float = DEFAULT_COST_BPS,
    ) -> "TradeRecord":
        """Sell ``shares`` of ``ticker`` at ``price``; all of it by default.

        A partial sale keeps the position open at its original entry
        price — trimming does not change the cost basis of what remains,
        so the reported P&L on the residual stays honest. Selling the
        whole line, or more than is held, closes it.

        Raises LivePortfolioError if no such position exists.
        """
        if price <= 0:
            raise LivePortfolioError(f"non-positive sell price for {ticker}: {price}")
        idx = self._index_of(ticker)
        if idx is None:
            raise LivePortfolioError(f"{ticker} not in portfolio")

        pos = self.positions[idx]
        if shares is None:
            sold = pos.shares
        else:
            if shares <= 0:
                raise LivePortfolioError(
                    f"non-positive sell quantity for {ticker}: {shares}"
                )
            sold = round(min(shares, pos.shares), SHARE_PRECISION)

        remaining = round(pos.shares - sold, SHARE_PRECISION)
        if remaining <= 0:
            self.positions.pop(idx)
        else:
            pos.shares = remaining

        gross = sold * price
        cost = gross * cost_bps
        self.cash += gross - cost
        self.cumulative_costs += cost
        return TradeRecord(
            ticker=ticker,
            side="SELL",
            shares=sold,
            price=price,
            gross_value=gross,
            cost_paid=cost,
            realized_pnl_usd=(price - pos.entry_price) * sold,
        )

    def buy(
        self,
        ticker: str,
        *,
        target_dollars: float,
        price: float,
        entry_date: str,
        why_en: str,
        why_he: str,
        cost_bps: float = DEFAULT_COST_BPS,
    ) -> "TradeRecord":
        """Buy ``ticker`` for at most ``target_dollars`` notional.

        Sizes fractional shares. Whole-share rounding used to discard
        the remainder of every position, and on a $10,000 book spread
        across 27 names the residue compounded into roughly a fifth of
        the portfolio sitting in cash against a design target of zero —
        an accounting artifact large enough to dominate the return
        difference between two agents. Every real broker has offered
        fractional shares for years; the constraint was ours, not the
        market's.

        Costs are taken on top of notional, so ``target_dollars`` is the
        limit including fees.
        """
        if price <= 0:
            raise LivePortfolioError(f"non-positive buy price for {ticker}: {price}")
        if target_dollars <= 0:
            raise LivePortfolioError(
                f"non-positive target dollars for {ticker}: {target_dollars}"
            )
        if target_dollars > self.cash + 1e-9:
            raise LivePortfolioError(
                f"insufficient cash for {ticker}: target ${target_dollars:.2f}, "
                f"cash ${self.cash:.2f}"
            )
        # Solve: notional + notional*cost_bps <= target_dollars  →  notional max.
        max_notional = target_dollars / (1.0 + cost_bps)
        shares_bought = round(max_notional / price, SHARE_PRECISION)
        if shares_bought <= 0:
            raise LivePortfolioError(
                f"${target_dollars:.2f} target on {ticker} at ${price:.2f} "
                f"rounds to zero shares"
            )
        gross = shares_bought * price
        cost = gross * cost_bps
        spend = gross + cost
        if spend > self.cash + 1e-9:
            raise LivePortfolioError(
                f"rounding error: {ticker} spend ${spend:.2f} > cash ${self.cash:.2f}"
            )
        self.cash -= spend
        self.cumulative_costs += cost
        # Merge with existing position if any (avg in cost basis)
        existing = self._index_of(ticker)
        if existing is not None:
            old = self.positions[existing]
            new_shares = old.shares + shares_bought
            new_basis = old.cost_basis + gross
            self.positions[existing] = Position(
                ticker=ticker,
                shares=new_shares,
                entry_price=new_basis / new_shares,
                entry_date=old.entry_date,
                current_price=price,
                why_en=why_en or old.why_en,
                why_he=why_he or old.why_he,
            )
        else:
            self.positions.append(
                Position(
                    ticker=ticker,
                    shares=shares_bought,
                    entry_price=price,
                    entry_date=entry_date,
                    current_price=price,
                    why_en=why_en,
                    why_he=why_he,
                )
            )
        return TradeRecord(
            ticker=ticker,
            side="BUY",
            shares=shares_bought,
            price=price,
            gross_value=gross,
            cost_paid=cost,
            realized_pnl_usd=0.0,
        )

    def _index_of(self, ticker: str) -> int | None:
        for i, p in enumerate(self.positions):
            if p.ticker == ticker:
                return i
        return None

    def has(self, ticker: str) -> bool:
        return self._index_of(ticker) is not None

    # ------------------------------------------------------------------
    # Watchlist management
    # ------------------------------------------------------------------
    def set_watchlist(self, entries: Iterable[WatchEntry]) -> None:
        """Replace the watchlist wholesale. Caller is responsible for
        filtering out tickers that became open positions."""
        held = {p.ticker for p in self.positions}
        self.watchlist = [e for e in entries if e.ticker not in held]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        # Round dollar amounts for human-readable JSON; precision lost
        # is well below transaction-cost noise.
        return {
            "agent": self.agent,
            "cash": round(self.cash, 2),
            "invested": round(self.invested, 2),
            "total_nav": round(self.total_nav, 2),
            "cumulative_return_pct": round(self.cumulative_return_pct, 4),
            "initial_cash": round(self.initial_cash, 2),
            "cumulative_costs": round(self.cumulative_costs, 4),
            "positions": [_round_position(p) for p in self.positions],
            "watchlist": [asdict(w) for w in self.watchlist],
            "last_updated": self.last_updated,
            "last_open_run": self.last_open_run,
            "last_close_run": self.last_close_run,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LivePortfolio":
        positions = [
            Position(
                ticker=str(p["ticker"]),
                shares=float(p["shares"]),
                entry_price=float(p["entry_price"]),
                entry_date=str(p["entry_date"]),
                current_price=float(p.get("current_price", 0.0)),
                pnl_usd=float(p.get("pnl_usd", 0.0)),
                pnl_pct=float(p.get("pnl_pct", 0.0)),
                weight_pct=float(p.get("weight_pct", 0.0)),
                why_en=str(p.get("why_en", "")),
                why_he=str(p.get("why_he", "")),
            )
            for p in data.get("positions", [])
        ]
        watchlist = [
            WatchEntry(
                ticker=str(w["ticker"]),
                identified_date=str(w["identified_date"]),
                current_rank=(
                    int(w["current_rank"]) if w.get("current_rank") is not None else None
                ),
                entry_trigger=str(w.get("entry_trigger", "")),
                entry_price_target=(
                    float(w["entry_price_target"])
                    if w.get("entry_price_target") is not None
                    else None
                ),
                why_en=str(w.get("why_en", "")),
                why_he=str(w.get("why_he", "")),
            )
            for w in data.get("watchlist", [])
        ]
        return cls(
            agent=str(data["agent"]),
            cash=float(data.get("cash", DEFAULT_INITIAL_CASH)),
            positions=positions,
            watchlist=watchlist,
            last_updated=str(data.get("last_updated", "")),
            last_open_run=str(data.get("last_open_run", "")),
            last_close_run=str(data.get("last_close_run", "")),
            initial_cash=float(data.get("initial_cash", DEFAULT_INITIAL_CASH)),
            cumulative_costs=float(data.get("cumulative_costs", 0.0)),
        )

    @classmethod
    def load_or_seed(
        cls,
        agent: str,
        *,
        directory: Path = DEFAULT_PORTFOLIO_DIR,
        initial_cash: float = DEFAULT_INITIAL_CASH,
    ) -> "LivePortfolio":
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{agent}.json"
        if not path.exists():
            logger.info(f"seeding new portfolio for {agent} at {path}")
            return cls(agent=agent, cash=initial_cash, initial_cash=initial_cash)
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise LivePortfolioError(f"failed to load {path}: {exc}") from exc
        return cls.from_dict(data)

    def save(self, *, directory: Path = DEFAULT_PORTFOLIO_DIR) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.agent}.json"
        # Atomic write: tmp file + rename so a crash mid-write can't
        # corrupt the portfolio file.
        with tempfile.NamedTemporaryFile(
            "w", dir=directory, delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(self.to_dict(), tmp, indent=2, ensure_ascii=False)
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
        return path


@dataclass(frozen=True)
class TradeRecord:
    ticker: str
    side: str  # "BUY" or "SELL"
    shares: float
    price: float
    gross_value: float
    cost_paid: float
    realized_pnl_usd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _round_position(p: Position) -> dict[str, Any]:
    d = asdict(p)
    for k in ("entry_price", "current_price"):
        d[k] = round(d[k], 4)
    for k in ("shares",):
        d[k] = round(d[k], 6)
    for k in ("pnl_usd", "weight_pct"):
        d[k] = round(d[k], 2)
    d["pnl_pct"] = round(d["pnl_pct"], 2)
    return d


def now_iso() -> str:
    """Timestamp string used for ``last_updated``. UTC, second-precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today_iso() -> str:
    """Today's date as ISO YYYY-MM-DD."""
    return date.today().isoformat()


__all__ = [
    "DEFAULT_COST_BPS",
    "DEFAULT_INITIAL_CASH",
    "DEFAULT_PORTFOLIO_DIR",
    "LivePortfolio",
    "LivePortfolioError",
    "Position",
    "TradeRecord",
    "WatchEntry",
    "now_iso",
    "today_iso",
]
