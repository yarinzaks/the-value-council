"""Walter Schloss strategy — diversified deep-value below book.

Implements the strategy described in ``playbook.md`` Sections 4 and 6.

At each annual rebalance:
1. Pull point-in-time financials for every ticker in the universe.
2. Compute price (from the price loader) and market cap.
3. Apply filters (P/B, D/E, history, market cap, profitability).
4. Sort survivors by P/B ascending.
5. Select the cheapest ``portfolio_size`` (default 75-100).
6. Equal-weight all selections.

The 1-year holding period is enforced naturally by the runner's
``rebalance_freq="annual"`` setting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from core.backtest.decision_logger import DecisionLogger, make_decision
from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
    Strategy,
)
from core.logger import get_logger

from .filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MAX_PB,
    DEFAULT_MIN_MARKET_CAP_USD,
    DEFAULT_MIN_YEARS_PUBLIC,
    filter_candidates,
)
from .ranking import SchlossScore, score_candidates, select_top_n

logger = get_logger("agents.schloss.deep_value")


@dataclass
class SchlossSelection:
    """Audit record of one rebalance."""

    as_of: date
    universe_size: int
    candidates_with_data: int
    candidates_after_filters: int
    selected_tickers: list[str]
    top_scores: list[SchlossScore]
    all_scores: list[SchlossScore] = field(default_factory=list)


class WalterSchloss(Strategy):
    """Schloss's diversified deep-value strategy.

    Args:
        portfolio_size: Number of stocks to hold (Schloss ran 75-100+;
            default 100).
        max_pb: Maximum acceptable Price-to-Book ratio. Default 0.75 —
            slightly stricter than the playbook's 0.80 default to focus
            on the deeper-discount segment Schloss preferred.
        max_de: Maximum Debt-to-Equity ratio. Default 1.0 (Schloss's
            hard rule).
        min_years_public: Minimum years of public filings visible.
        min_market_cap: USD floor on market cap (filters micro-caps).
    """

    name = "walter_schloss"

    def __init__(
        self,
        *,
        portfolio_size: int = 100,
        max_pb: float = DEFAULT_MAX_PB,
        max_de: float = DEFAULT_MAX_DE,
        min_years_public: int = DEFAULT_MIN_YEARS_PUBLIC,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if portfolio_size <= 0:
            raise ValueError(f"portfolio_size must be positive; got {portfolio_size}")
        if max_pb <= 0:
            raise ValueError(f"max_pb must be positive; got {max_pb}")
        if max_de <= 0:
            raise ValueError(f"max_de must be positive; got {max_de}")
        if min_market_cap <= 0:
            raise ValueError(f"min_market_cap must be positive; got {min_market_cap}")
        self.portfolio_size = portfolio_size
        self.max_pb = max_pb
        self.max_de = max_de
        self.min_years_public = min_years_public
        self.min_market_cap = min_market_cap
        self.decision_logger = decision_logger
        self.selection_history: list[SchlossSelection] = []

    # ------------------------------------------------------------------
    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
        *,
        held: Mapping[str, HeldPosition] | None = None,
    ) -> dict[str, float]:
        """Return target weights for the rebalance date."""
        logger.info(
            f"{as_of}: starting Schloss selection over {len(universe)} candidates"
        )

        # 1. Pull (financials, market_cap, price) tuples
        triples: list[
            tuple[PointInTimeFinancials | None, float | None, float | None]
        ] = []
        for ticker in universe:
            fin = fundamentals.get(ticker)
            price = prices.get(ticker)
            mcap = self._market_cap(fin, price)
            triples.append((fin, mcap, price))

        with_data = sum(1 for fin, _, _ in triples if fin is not None)

        # 2. Filter
        survivors = filter_candidates(
            triples,
            as_of=as_of,
            max_pb=self.max_pb,
            max_de=self.max_de,
            min_years_public=self.min_years_public,
            min_market_cap=self.min_market_cap,
        )

        # 3. Score (sort by P/B asc) + take top N
        scores = score_candidates(survivors)
        top = select_top_n(scores, n=self.portfolio_size)

        # 4. Equal weighting
        if not top:
            logger.warning(
                f"{as_of}: no Schloss candidates qualified — staying in cash"
            )
            self._record(as_of, len(universe), with_data, len(survivors), [], [], scores)
            return {}

        weight = 1.0 / len(top)
        weights = {s.ticker: weight for s in top}
        self._record(
            as_of,
            len(universe),
            with_data,
            len(survivors),
            list(weights.keys()),
            top,
            scores,
        )
        if self.decision_logger is not None:
            self._log_decisions(as_of, top, scores, prices)
        logger.info(
            f"{as_of}: selected {len(top)} stocks "
            f"(top P/B {top[0].pb_ratio:.3f}, "
            f"deepest D/E {min(s.debt_to_equity for s in top):.2f}, "
            f"median P/B {sorted(s.pb_ratio for s in top)[len(top) // 2]:.3f})"
        )
        return weights

    # ------------------------------------------------------------------
    @staticmethod
    def _market_cap(
        fin: PointInTimeFinancials | None,
        price: float | None,
    ) -> float | None:
        """Market cap = price × shares outstanding (point-in-time)."""
        if fin is None or fin.shares_outstanding is None:
            return None
        if price is None or price <= 0:
            return None
        return price * fin.shares_outstanding

    def _record(
        self,
        as_of: date,
        universe_size: int,
        with_data: int,
        survivors: int,
        selected: list[str],
        top: list[SchlossScore],
        all_scores: list[SchlossScore] | None = None,
    ) -> None:
        self.selection_history.append(
            SchlossSelection(
                as_of=as_of,
                universe_size=universe_size,
                candidates_with_data=with_data,
                candidates_after_filters=survivors,
                all_scores=list(all_scores or []),
                selected_tickers=selected,
                top_scores=top,
            )
        )

    def _log_decisions(
        self,
        as_of: date,
        top: list[SchlossScore],
        all_scores: list[SchlossScore],
        prices: PriceLookup,
    ) -> None:
        """Persist a Decision per scored candidate (BUY for top-N,
        WATCH for next portfolio_size near-misses)."""
        timestamp = f"{as_of.isoformat()}T00:00:00+00:00"
        selected = {s.ticker for s in top}
        watch = [s for s in all_scores if s.ticker not in selected][
            : self.portfolio_size
        ]
        for s in top + watch:
            decision_type = "BUY" if s.ticker in selected else "WATCH"
            try:
                self.decision_logger.log(
                    make_decision(
                        ticker=s.ticker,
                        decision=decision_type,
                        agent=self.name,
                        timestamp=timestamp,
                        criteria_met=[
                            f"P/B={s.pb_ratio:.3f} (< {self.max_pb})",
                            f"D/E={s.debt_to_equity:.2f} (≤ {self.max_de})",
                            "positive trailing net income",
                        ],
                        criteria_values={
                            "pb_ratio": round(s.pb_ratio, 4),
                            "debt_to_equity": round(s.debt_to_equity, 4),
                            "book_value_per_share": s.book_value_per_share,
                            "market_cap": s.market_cap,
                            "net_income": s.net_income,
                            "price": s.price,
                        },
                        confidence=max(0.0, min(1.0, 1.0 - s.pb_ratio)),
                        entry_price=s.price,
                        target_price=s.book_value_per_share,
                        exit_trigger="P/B reverts toward 1.0× book OR ~50% gain",
                        rationale=(
                            f"Schloss deep-value: P/B {s.pb_ratio:.3f}, "
                            f"D/E {s.debt_to_equity:.2f}, BVPS {s.book_value_per_share:.2f}"
                        ),
                    )
                )
            except Exception as exc:
                logger.warning(f"decision log failed for {s.ticker}: {exc}")

    def selections_to_records(self) -> list[dict[str, Any]]:
        """JSON-friendly per-rebalance audit records."""
        return [
            {
                "as_of": sel.as_of.isoformat(),
                "universe_size": sel.universe_size,
                "candidates_with_data": sel.candidates_with_data,
                "candidates_after_filters": sel.candidates_after_filters,
                "selected_tickers": list(sel.selected_tickers),
                "top_pb": sel.top_scores[0].pb_ratio if sel.top_scores else None,
                "median_pb": (
                    sorted(s.pb_ratio for s in sel.top_scores)[len(sel.top_scores) // 2]
                    if sel.top_scores
                    else None
                ),
            }
            for sel in self.selection_history
        ]


__all__ = ["SchlossSelection", "WalterSchloss"]
