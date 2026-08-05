"""David Dreman 4-metric contrarian strategy.

Plug-in to ``core.backtest.Strategy`` — Section 4 of ``playbook.md``:

1. Apply quality gates per stock: positive trailing earnings,
   manageable debt (D/E ≤ 1.0), market cap ≥ $500M, exclude share
   classes/preferreds.
2. Compute population-aware quintile thresholds across the survivors
   on P/E, P/CF, P/B, dividend yield.
3. Keep stocks in the bottom 20% on at least 2 of 4 metrics
   (top 20% for yield).
4. Rank the survivors by (qualifying_metrics desc, composite percentile
   rank asc) and select the top 20-30.
5. Equal-weight, annual rebalance.
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

from .diversification import (
    DEFAULT_MAX_INDUSTRY_WEIGHT_PCT,
    DEFAULT_MIN_INDUSTRIES,
    DiversificationReport,
    diversify,
)
from .filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    DEFAULT_MIN_QUALIFYING_METRICS,
    DEFAULT_QUINTILE,
    apply_quality_gates,
)
from .ranking import DremanScore, score_candidates

logger = get_logger("agents.dreman.contrarian")


@dataclass
class DremanSelection:
    """Audit record per rebalance."""

    as_of: date
    universe_size: int
    candidates_with_data: int
    candidates_after_quality: int
    candidates_after_quintile: int
    selected_tickers: list[str]
    top_scores: list[DremanScore]
    all_scores: list[DremanScore] = field(default_factory=list)


class DavidDreman(Strategy):
    """Dreman contrarian strategy.

    Args:
        portfolio_size: Target number of holdings (Dreman: 20-30).
        min_qualifying_metrics: Bottom-quintile hits required to make
            the cut. Default 2 of 4 per the playbook.
        quintile: Quintile cutoff. 0.20 = bottom (or top) 20%.
        max_de: D/E ceiling. Default 1.0.
        min_market_cap: Market-cap floor in USD.
    """

    name = "david_dreman"

    def __init__(
        self,
        *,
        portfolio_size: int = 25,
        min_qualifying_metrics: int = DEFAULT_MIN_QUALIFYING_METRICS,
        quintile: float = DEFAULT_QUINTILE,
        max_de: float = DEFAULT_MAX_DE,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
        min_industries: int = DEFAULT_MIN_INDUSTRIES,
        max_industry_weight_pct: float = DEFAULT_MAX_INDUSTRY_WEIGHT_PCT,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if portfolio_size <= 0:
            raise ValueError(f"portfolio_size must be positive; got {portfolio_size}")
        if min_qualifying_metrics < 1 or min_qualifying_metrics > 4:
            raise ValueError(
                f"min_qualifying_metrics must be 1..4; got {min_qualifying_metrics}"
            )
        if not 0.0 < quintile < 0.5:
            raise ValueError(f"quintile must be in (0, 0.5); got {quintile}")
        if max_de <= 0:
            raise ValueError(f"max_de must be positive; got {max_de}")
        if min_market_cap <= 0:
            raise ValueError(f"min_market_cap must be positive; got {min_market_cap}")
        if min_industries <= 0:
            raise ValueError(f"min_industries must be positive; got {min_industries}")
        if not 0.0 < max_industry_weight_pct <= 100.0:
            raise ValueError(
                "max_industry_weight_pct must be in (0, 100]; "
                f"got {max_industry_weight_pct}"
            )
        self.portfolio_size = portfolio_size
        self.min_qualifying_metrics = min_qualifying_metrics
        self.quintile = quintile
        self.max_de = max_de
        self.min_market_cap = min_market_cap
        self.min_industries = min_industries
        self.max_industry_weight_pct = max_industry_weight_pct
        self.decision_logger = decision_logger
        self.selection_history: list[DremanSelection] = []
        self.last_diversification: DiversificationReport | None = None

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
            f"{as_of}: starting Dreman contrarian selection over {len(universe)} candidates"
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

        # Stage 1: quality gates (per-stock).
        survivors = apply_quality_gates(
            triples,
            as_of=as_of,
            max_de=self.max_de,
            min_market_cap=self.min_market_cap,
        )

        # Stage 2: population-aware quintile screen + ranking.
        scores = score_candidates(
            survivors,
            min_qualifying_metrics=self.min_qualifying_metrics,
            quintile=self.quintile,
        )
        # Rule 18: 20-30 names across 15+ industries, none above 15% of
        # the book. Taking the top N by rank alone produced 25 holdings
        # in 11 industries with insurance at 28.5% and financials at
        # half the book — the cheapest quintile is where damaged
        # businesses cluster, and they cluster by industry.
        top, self.last_diversification = diversify(
            scores,
            portfolio_size=self.portfolio_size,
            min_industries=self.min_industries,
            max_industry_weight_pct=self.max_industry_weight_pct,
        )

        if not top:
            logger.warning(
                f"{as_of}: no contrarian candidates qualified — staying in cash"
            )
            self._record(
                as_of, len(universe), with_data, len(survivors), 0, [], [], scores
            )
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
            scores,
        )
        if self.decision_logger is not None:
            self._log_decisions(as_of, top, scores)
        median_rank = sorted(s.composite_rank for s in top)[len(top) // 2]
        logger.info(
            f"{as_of}: selected {len(top)} stocks "
            f"(top composite rank {top[0].composite_rank:.3f}, "
            f"median {median_rank:.3f})"
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
        after_quintile: int,
        selected: list[str],
        top: list[DremanScore],
        all_scores: list[DremanScore] | None = None,
    ) -> None:
        self.selection_history.append(
            DremanSelection(
                as_of=as_of,
                universe_size=universe_size,
                candidates_with_data=with_data,
                candidates_after_quality=survivors,
                all_scores=list(all_scores or []),
                candidates_after_quintile=after_quintile,
                selected_tickers=selected,
                top_scores=top,
            )
        )

    def _log_decisions(
        self,
        as_of: date,
        top: list[DremanScore],
        all_scores: list[DremanScore],
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
                        criteria_met=_criteria_for(s),
                        criteria_values={
                            "pe": _round(s.pe),
                            "pcf": _round(s.pcf),
                            "pb": _round(s.pb),
                            "div_yield": _round(s.div_yield),
                            "qualifying_metrics": s.qualifying_metrics,
                            "composite_rank": round(s.composite_rank, 4),
                            "debt_to_equity": round(s.debt_to_equity, 4),
                            "net_income": s.net_income,
                            "market_cap": s.market_cap,
                            "price": s.price,
                        },
                        confidence=max(0.0, min(1.0, 1.0 - s.composite_rank)),
                        entry_price=s.price,
                        target_price=None,
                        exit_trigger=(
                            "metrics revert toward median (composite rank "
                            ">= 0.5) OR ~50% gain"
                        ),
                        rationale=(
                            f"Dreman contrarian: bottom-quintile on "
                            f"{s.qualifying_metrics}/4 metrics, composite "
                            f"rank {s.composite_rank:.3f}"
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
                "candidates_after_quintile": sel.candidates_after_quintile,
                "selected_tickers": list(sel.selected_tickers),
                "top_composite_rank": (
                    sel.top_scores[0].composite_rank if sel.top_scores else None
                ),
                "median_composite_rank": (
                    sorted(s.composite_rank for s in sel.top_scores)[
                        len(sel.top_scores) // 2
                    ]
                    if sel.top_scores
                    else None
                ),
            }
            for sel in self.selection_history
        ]


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


def _criteria_for(s: DremanScore) -> list[str]:
    """Human-readable per-metric attribution for the decision log."""
    crit: list[str] = []
    pe_q, pcf_q, pb_q, yld_q = s.qualifying_flags
    if pe_q and s.pe is not None:
        crit.append(f"P/E={s.pe:.2f} (bottom quintile)")
    if pcf_q and s.pcf is not None:
        crit.append(f"P/CF={s.pcf:.2f} (bottom quintile)")
    if pb_q and s.pb is not None:
        crit.append(f"P/B={s.pb:.2f} (bottom quintile)")
    if yld_q and s.div_yield is not None:
        crit.append(f"yield={s.div_yield:.2%} (top quintile)")
    crit.append(f"D/E={s.debt_to_equity:.2f} (≤ {DEFAULT_MAX_DE})")
    crit.append("positive trailing net income")
    return crit


__all__ = ["DavidDreman", "DremanSelection"]
