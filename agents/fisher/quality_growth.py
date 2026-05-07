"""Philip Fisher "Quality Growth" strategy.

Plug-in to ``core.backtest.Strategy``. Implements playbook §3-§7:

  1. Quality gates (light — most filtering happens via the 5-point
     quant score in :mod:`ranking`).
  2. 5-point quant score → tier classification (A=5/5, B=4/5,
     reject otherwise) + per-tier P/E ceiling.
  3. Tier-weighted position sizing per playbook §5.2 / §6.2:
       * Tier A: 12% per position
       * Tier B: 6%  per position
  4. Total portfolio capped at ``max_portfolio_size`` (default 15
     per playbook §6.1).
  5. Cash residual = 1 − Σ(weights). Fisher held cash naturally when
     few candidates qualified; he never targeted a cash level.

LLM scuttlebutt analyzer (:mod:`scuttlebutt`) is consulted in live
mode for each top candidate. REJECT memos veto the buy. The
backtest runs WITHOUT the LLM (lookahead bias + free-tier quota —
documented in ``run_full_market_validation.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from core.backtest.decision_logger import DecisionLogger, make_decision
from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import (
    FundamentalsLookup,
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
from .ranking import (
    FisherScore,
    score_candidates,
    select_top_n,
)
from .scuttlebutt import FisherMemo, ScuttlebuttAnalyzer

logger = get_logger("agents.fisher.quality_growth")


#: Playbook §6.1 — Fisher held 14-30 stocks; we target the lower end
#: scaled for our $10K paper book.
DEFAULT_MAX_PORTFOLIO_SIZE: int = 15


@dataclass
class FisherSelection:
    """Audit record per rebalance."""

    as_of: date
    universe_size: int
    candidates_with_data: int
    candidates_after_quality: int
    candidates_after_screen: int
    selected_tickers: list[str] = field(default_factory=list)
    top_scores: list[FisherScore] = field(default_factory=list)
    deployed_fraction: float = 0.0
    memos_collected: int = 0


class PhilipFisher(Strategy):
    """Quality-growth with tier-weighted sizing.

    Args:
        edgar_cache: EDGAR XBRL cache (required for revenue / R&D /
            margin / share-count history).
        min_market_cap: Market-cap floor. Default $1B (Fisher held
            mid- to large-cap).
        max_de: D/E ceiling. Default 0.6.
        max_portfolio_size: Cap on positions. Default 15.
        scuttlebutt_analyzer: Optional LLM analyzer. Live mode only.
        decision_logger: Optional audit trail.
    """

    name = "philip_fisher"

    def __init__(
        self,
        *,
        edgar_cache: EdgarCache,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
        max_de: float = DEFAULT_MAX_DE,
        max_portfolio_size: int = DEFAULT_MAX_PORTFOLIO_SIZE,
        scuttlebutt_analyzer: ScuttlebuttAnalyzer | None = None,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if min_market_cap <= 0:
            raise ValueError(f"min_market_cap must be positive; got {min_market_cap}")
        if max_de <= 0:
            raise ValueError(f"max_de must be positive; got {max_de}")
        if max_portfolio_size <= 0:
            raise ValueError(
                f"max_portfolio_size must be positive; got {max_portfolio_size}"
            )
        if edgar_cache is None:
            raise ValueError("edgar_cache is required for the Fisher strategy")
        self.edgar_cache = edgar_cache
        self.min_market_cap = min_market_cap
        self.max_de = max_de
        self.max_portfolio_size = max_portfolio_size
        self.scuttlebutt_analyzer = scuttlebutt_analyzer
        self.decision_logger = decision_logger
        self.selection_history: list[FisherSelection] = []
        self.last_memos: list[FisherMemo] = []

    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
    ) -> dict[str, float]:
        logger.info(
            f"{as_of}: starting Fisher quality-growth scan over "
            f"{len(universe)} candidates"
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

        # Stage 1: quality gates.
        survivors = apply_quality_gates(
            triples,
            as_of=as_of,
            min_market_cap=self.min_market_cap,
            max_de=self.max_de,
        )

        # Stage 2: 5-point quant score → tier classification.
        scores = score_candidates(
            survivors, as_of=as_of, edgar_cache=self.edgar_cache
        )
        top = select_top_n(scores, n=self.max_portfolio_size)

        # Stage 3 (live only): LLM scuttlebutt analyzer.
        memos: list[FisherMemo] = []
        if self.scuttlebutt_analyzer is not None and top:
            top, memos = self._llm_filter(top, as_of)
        self.last_memos = memos

        if not top:
            logger.warning(
                f"{as_of}: no Fisher candidates qualified — staying in cash"
            )
            self._record(
                as_of,
                len(universe),
                with_data,
                len(survivors),
                len(scores),
                [],
                [],
                0.0,
                0,
            )
            return {}

        # Tier-weighted sizing. Each name gets its tier's target size,
        # capped so the total deployed fraction is ≤ 1.0. Cash =
        # residual.
        weights: dict[str, float] = {}
        for s in top:
            weights[s.ticker] = s.suggested_position_size_pct / 100.0
        total = sum(weights.values())
        if total > 1.0:
            # Scale all weights down proportionally.
            scale = 1.0 / total
            weights = {t: w * scale for t, w in weights.items()}
            deployed = 1.0
        else:
            deployed = total

        self._record(
            as_of,
            len(universe),
            with_data,
            len(survivors),
            len(scores),
            list(weights.keys()),
            top,
            deployed,
            len(memos),
        )
        if self.decision_logger is not None:
            self._log_decisions(as_of, top, scores, memos)
        n_a = sum(1 for s in top if s.tier == "A")
        n_b = sum(1 for s in top if s.tier == "B")
        logger.info(
            f"{as_of}: selected {len(top)} stocks "
            f"({n_a} Tier A, {n_b} Tier B; deploy {deployed:.0%})"
        )
        return weights

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _market_cap(
        fin: PointInTimeFinancials | None, price: float | None
    ) -> float | None:
        if fin is None or fin.shares_outstanding is None:
            return None
        if price is None or price <= 0:
            return None
        return price * fin.shares_outstanding

    def _llm_filter(
        self,
        top: list[FisherScore],
        as_of: date,
    ) -> tuple[list[FisherScore], list[FisherMemo]]:
        kept: list[FisherScore] = []
        memos: list[FisherMemo] = []
        analyzer = self.scuttlebutt_analyzer
        assert analyzer is not None
        for s in top:
            stock_data = {
                "ticker": s.ticker,
                "as_of": as_of.isoformat(),
                "price": s.price,
                "market_cap": s.market_cap,
                "pe": s.pe,
                "quant_quality_points": s.quality_points,
                "quant_tier": s.tier,
                "debt_to_equity": s.debt_to_equity,
                "quant_breakdown": {
                    "point_1_market_potential": (
                        s.quality_score.point_1_market_potential
                    ),
                    "point_3_rd_effectiveness": (
                        s.quality_score.point_3_rd_effectiveness
                    ),
                    "point_5_profit_margins": (
                        s.quality_score.point_5_profit_margins
                    ),
                    "point_6_margin_maintenance": (
                        s.quality_score.point_6_margin_maintenance
                    ),
                    "point_13_equity_dilution": (
                        s.quality_score.point_13_equity_dilution
                    ),
                    "revenue_cagr_5yr_pct": (
                        s.quality_score.revenue_cagr_5yr_pct
                    ),
                    "rd_to_revenue_pct": s.quality_score.rd_to_revenue_pct,
                    "operating_margin_pct": (
                        s.quality_score.operating_margin_pct
                    ),
                    "margin_trend_5yr_bps": (
                        s.quality_score.margin_trend_5yr_bps
                    ),
                    "share_count_change_5yr_pct": (
                        s.quality_score.share_count_change_5yr_pct
                    ),
                },
            }
            try:
                memo = analyzer.analyze(
                    stock_data=stock_data,
                    portfolio_state={"as_of": as_of.isoformat()},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"{as_of} {s.ticker}: scuttlebutt analyzer failed ({exc}); "
                    "keeping quant-only verdict"
                )
                kept.append(s)
                continue
            memos.append(memo)
            # Fisher's hard rule: integrity is non-negotiable.
            if not memo.integrity_check_passed:
                logger.info(
                    f"{as_of} {s.ticker}: integrity check FAILED — REJECT"
                )
                continue
            if memo.decision in ("BUY", "HOLD"):
                kept.append(s)
            else:
                logger.info(
                    f"{as_of} {s.ticker}: LLM verdict {memo.decision} — dropped"
                )
        return kept, memos

    def _record(
        self,
        as_of: date,
        universe_size: int,
        with_data: int,
        survivors: int,
        after_screen: int,
        selected: list[str],
        top: list[FisherScore],
        deployed_fraction: float,
        memos_collected: int,
    ) -> None:
        self.selection_history.append(
            FisherSelection(
                as_of=as_of,
                universe_size=universe_size,
                candidates_with_data=with_data,
                candidates_after_quality=survivors,
                candidates_after_screen=after_screen,
                selected_tickers=selected,
                top_scores=top,
                deployed_fraction=deployed_fraction,
                memos_collected=memos_collected,
            )
        )

    def _log_decisions(
        self,
        as_of: date,
        top: list[FisherScore],
        all_scores: list[FisherScore],
        memos: list[FisherMemo],
    ) -> None:
        timestamp = f"{as_of.isoformat()}T00:00:00+00:00"
        selected_set = {s.ticker for s in top}
        watch = [s for s in all_scores if s.ticker not in selected_set][
            : len(top)
        ]
        memos_by_ticker = {m.ticker: m for m in memos}
        for s in top + watch:
            decision_type = "BUY" if s.ticker in selected_set else "WATCH"
            memo = memos_by_ticker.get(s.ticker)
            try:
                self.decision_logger.log(
                    make_decision(
                        ticker=s.ticker,
                        decision=decision_type,
                        agent=self.name,
                        timestamp=timestamp,
                        criteria_met=[
                            f"Tier {s.tier}",
                            f"quality {s.quality_points}/5",
                            f"P/E {s.pe:.2f}",
                            f"D/E {s.debt_to_equity:.2f}",
                        ],
                        criteria_values={
                            "tier": s.tier,
                            "quality_points": s.quality_points,
                            "pe": round(s.pe, 4),
                            "revenue_cagr_5yr_pct": (
                                round(s.quality_score.revenue_cagr_5yr_pct, 2)
                                if s.quality_score.revenue_cagr_5yr_pct
                                is not None
                                else None
                            ),
                            "rd_to_revenue_pct": (
                                round(s.quality_score.rd_to_revenue_pct, 2)
                                if s.quality_score.rd_to_revenue_pct is not None
                                else None
                            ),
                            "operating_margin_pct": (
                                round(s.quality_score.operating_margin_pct, 2)
                                if s.quality_score.operating_margin_pct
                                is not None
                                else None
                            ),
                            "margin_trend_5yr_bps": (
                                round(s.quality_score.margin_trend_5yr_bps, 1)
                                if s.quality_score.margin_trend_5yr_bps
                                is not None
                                else None
                            ),
                            "share_count_change_5yr_pct": (
                                round(
                                    s.quality_score.share_count_change_5yr_pct,
                                    2,
                                )
                                if s.quality_score.share_count_change_5yr_pct
                                is not None
                                else None
                            ),
                            "debt_to_equity": round(s.debt_to_equity, 2),
                            "net_income": s.net_income,
                            "market_cap": s.market_cap,
                            "price": s.price,
                        },
                        confidence=(
                            memo.confidence
                            if memo is not None
                            else (0.85 if s.tier == "A" else 0.65)
                        ),
                        entry_price=s.price,
                        target_price=None,
                        exit_trigger=(
                            "; ".join(memo.reverse_triggers)
                            if memo is not None and memo.reverse_triggers
                            else (
                                "15-point score drops below 11; "
                                "integrity question; R&D / culture decline"
                            )
                        ),
                        rationale=(
                            memo.thesis_en
                            if memo is not None
                            else (
                                f"Fisher Tier {s.tier}: {s.quality_points}/5 "
                                f"quant points, P/E {s.pe:.1f}, "
                                f"OpMargin "
                                f"{s.quality_score.operating_margin_pct or 0:.1f}%"
                            )
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"decision log failed for {s.ticker}: {exc}")

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def selections_to_records(self) -> list[dict[str, Any]]:
        return [
            {
                "as_of": sel.as_of.isoformat(),
                "universe_size": sel.universe_size,
                "candidates_with_data": sel.candidates_with_data,
                "candidates_after_quality": sel.candidates_after_quality,
                "candidates_after_screen": sel.candidates_after_screen,
                "deployed_fraction": sel.deployed_fraction,
                "selected_tickers": list(sel.selected_tickers),
                "memos_collected": sel.memos_collected,
                "tier_a_count": sum(1 for s in sel.top_scores if s.tier == "A"),
                "tier_b_count": sum(1 for s in sel.top_scores if s.tier == "B"),
            }
            for sel in self.selection_history
        ]


__all__ = [
    "DEFAULT_MAX_PORTFOLIO_SIZE",
    "FisherSelection",
    "PhilipFisher",
]
