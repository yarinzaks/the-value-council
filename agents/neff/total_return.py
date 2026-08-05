"""John Neff Total-Return / PE strategy.

Plug-in to ``core.backtest.Strategy`` — implements the Section 4 of
``playbook.md``:

1. Apply quality gates (positive earnings, manageable debt, market-cap
   floor, no share-class tickers).
2. Compute market-median P/E and dividend yield across the survivors.
3. Apply the 7-criterion screen against those medians (criterion 6 —
   quarterly persistence — is deferred; the other 6 are enforced).
4. Rank survivors by Neff's signature metric:
   ``Total-Return/PE = (EPS growth + dividend yield) / P/E``.
5. Take the top ``portfolio_size`` (Neff: 60-80; for our $10K paper
   portfolio: 20-30).
6. Equal-weight, annual rebalance.

Note on growth inputs: Neff used FORWARD analyst-consensus estimates
which we don't have. We use 4-year trailing CAGR from the EDGAR
cache instead — honest backtest practice that the data supports.
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
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

from .filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    apply_quality_gates,
)
from .ranking import NeffScore, score_candidates, select_top_n

logger = get_logger("agents.neff.total_return")


@dataclass
class NeffSelection:
    """Audit record per rebalance."""

    as_of: date
    universe_size: int
    candidates_with_data: int
    candidates_after_quality: int
    candidates_after_screen: int
    selected_tickers: list[str] = field(default_factory=list)
    top_scores: list[NeffScore] = field(default_factory=list)


class JohnNeff(Strategy):
    """Neff Total-Return / PE strategy.

    Args:
        portfolio_size: Target number of holdings. Default 25.
        edgar_cache: EDGAR XBRL cache for historical-growth lookups.
            Required (the strategy is unrunnable without it).
        max_de: D/E ceiling (default 1.0).
        min_market_cap: Market-cap floor in USD.
        decision_logger: Optional DecisionLogger for audit trail.
    """

    name = "john_neff"

    def __init__(
        self,
        *,
        edgar_cache: EdgarCache,
        portfolio_size: int = 25,
        max_de: float = DEFAULT_MAX_DE,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if portfolio_size <= 0:
            raise ValueError(f"portfolio_size must be positive; got {portfolio_size}")
        if max_de <= 0:
            raise ValueError(f"max_de must be positive; got {max_de}")
        if min_market_cap <= 0:
            raise ValueError(f"min_market_cap must be positive; got {min_market_cap}")
        if edgar_cache is None:
            raise ValueError("edgar_cache is required for the Neff strategy")
        self.portfolio_size = portfolio_size
        self.max_de = max_de
        self.min_market_cap = min_market_cap
        self.edgar_cache = edgar_cache
        self.decision_logger = decision_logger
        self.selection_history: list[NeffSelection] = []

    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
        *,
        held: Mapping[str, HeldPosition] | None = None,
    ) -> dict[str, float]:
        logger.info(
            f"{as_of}: starting Neff Total-Return scan over {len(universe)} candidates"
        )

        triples: list[
            tuple[PointInTimeFinancials | None, float | None, float | None]
        ] = []
        for ticker in universe:
            fin = fundamentals.get(ticker)
            price = prices.get(ticker)
            mcap = self._market_cap(fin, price)
            triples.append((fin, mcap, price))

        with_data = sum(1 for fin, _, _ in triples if fin is not None)

        # Stage 1: per-stock quality gates.
        survivors = apply_quality_gates(
            triples,
            as_of=as_of,
            max_de=self.max_de,
            min_market_cap=self.min_market_cap,
        )

        # Stage 2: market-aware 7-criterion screen + TR/PE ranking.
        scores = score_candidates(
            survivors, as_of=as_of, edgar_cache=self.edgar_cache
        )
        top = select_top_n(scores, n=self.portfolio_size)

        if not top:
            logger.warning(
                f"{as_of}: no Neff candidates qualified — staying in cash"
            )
            self._record(as_of, len(universe), with_data, len(survivors), 0, [], [])
            return {}

        weight = 1.0 / len(top)
        weights = {s.ticker: weight for s in top}
        self._record(
            as_of,
            len(universe),
            with_data,
            len(survivors),
            len(scores),
            list(weights.keys()),
            top,
        )
        if self.decision_logger is not None:
            self._log_decisions(as_of, top, scores)
        median_tr_pe = sorted(s.total_return_pe for s in top)[len(top) // 2]
        logger.info(
            f"{as_of}: selected {len(top)} stocks "
            f"(top TR/PE {top[0].total_return_pe:.3f}, "
            f"median {median_tr_pe:.3f})"
        )
        return weights

    @staticmethod
    def _market_cap(
        fin: PointInTimeFinancials | None, price: float | None
    ) -> float | None:
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
        after_screen: int,
        selected: list[str],
        top: list[NeffScore],
    ) -> None:
        self.selection_history.append(
            NeffSelection(
                as_of=as_of,
                universe_size=universe_size,
                candidates_with_data=with_data,
                candidates_after_quality=survivors,
                candidates_after_screen=after_screen,
                selected_tickers=selected,
                top_scores=top,
            )
        )

    def _log_decisions(
        self,
        as_of: date,
        top: list[NeffScore],
        all_scores: list[NeffScore],
    ) -> None:
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
                            f"P/E={s.pe:.2f}",
                            f"EPS growth={s.eps_growth_pct:.1f}%",
                            f"yield={s.dividend_yield_pct:.1f}%",
                            f"TR/PE={s.total_return_pe:.3f}",
                            f"ROE={s.roe_pct:.1f}%",
                            f"D/E={s.debt_to_equity:.2f}",
                            "positive trailing net income",
                        ],
                        criteria_values={
                            "pe": round(s.pe, 4),
                            "eps_growth_pct": round(s.eps_growth_pct, 4),
                            "sales_growth_pct": round(s.sales_growth_pct, 4),
                            "dividend_yield_pct": round(s.dividend_yield_pct, 4),
                            "roe_pct": round(s.roe_pct, 4),
                            "total_return_pe": round(s.total_return_pe, 4),
                            "debt_to_equity": round(s.debt_to_equity, 4),
                            "net_income": s.net_income,
                            "market_cap": s.market_cap,
                            "price": s.price,
                        },
                        confidence=max(
                            0.0, min(1.0, s.total_return_pe / 3.0)
                        ),
                        entry_price=s.price,
                        target_price=None,
                        exit_trigger=(
                            "P/E rises toward market average OR earnings "
                            "miss two quarters OR dividend yield collapses"
                        ),
                        rationale=(
                            f"Neff Total-Return/PE: TR/PE {s.total_return_pe:.3f}, "
                            f"P/E {s.pe:.2f}, growth {s.eps_growth_pct:.1f}%, "
                            f"yield {s.dividend_yield_pct:.1f}%, ROE {s.roe_pct:.1f}%"
                        ),
                    )
                )
            except Exception as exc:
                logger.warning(f"decision log failed for {s.ticker}: {exc}")

    def selections_to_records(self) -> list[dict[str, Any]]:
        return [
            {
                "as_of": sel.as_of.isoformat(),
                "universe_size": sel.universe_size,
                "candidates_with_data": sel.candidates_with_data,
                "candidates_after_quality": sel.candidates_after_quality,
                "candidates_after_screen": sel.candidates_after_screen,
                "selected_tickers": list(sel.selected_tickers),
                "top_tr_pe": (
                    sel.top_scores[0].total_return_pe if sel.top_scores else None
                ),
                "median_tr_pe": (
                    sorted(s.total_return_pe for s in sel.top_scores)[
                        len(sel.top_scores) // 2
                    ]
                    if sel.top_scores
                    else None
                ),
            }
            for sel in self.selection_history
        ]


__all__ = ["JohnNeff", "NeffSelection"]
