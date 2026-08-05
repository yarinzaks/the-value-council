"""Howard Marks "Cycle Value" strategy.

Plug-in to ``core.backtest.Strategy``. Implements playbook §3-§6:

  1. Quality gates (light — Marks tolerates more leverage than Buffett
     because cycle-positioning is the safety net).
  2. **Market temperature assessment** over the post-gate universe —
     this is the unique Marks step. Result: one of Cold / Cool /
     Neutral / Warm / Hot.
  3. Cycle-adjusted ranking — weights tilt with posture (deeper value
     in Cold; quality + balance sheet in Hot).
  4. **Posture-driven sizing** — portfolio_size shrinks and cash
     fraction grows as posture moves Cold → Hot (per playbook §6.2).
  5. Equal-weight inside the deployed fraction; residual held cash.

Marks's two cores — second-level thinking and risk-adjusted scenario
analysis — happen in :mod:`second_level` (LLM, live mode only). The
backtest path here is faithful to the *cycle-positioning* leg of his
framework.
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
from .ranking import MarksScore, score_candidates, select_top_n
from .second_level import MarksMemo, SecondLevelAnalyzer
from .temperature import (
    Posture,
    TemperatureAssessment,
    assess_market_temperature,
    profile_for,
)

logger = get_logger("agents.marks.cycle_value")


@dataclass
class MarksSelection:
    """Audit record per rebalance."""

    as_of: date
    universe_size: int
    candidates_with_data: int
    candidates_after_quality: int
    candidates_after_screen: int
    posture: Posture
    temperature_score: float
    deployed_fraction: float
    selected_tickers: list[str] = field(default_factory=list)
    top_scores: list[MarksScore] = field(default_factory=list)
    memos_collected: int = 0


class HowardMarks(Strategy):
    """Cycle-aware value strategy.

    Args:
        edgar_cache: EDGAR XBRL cache (required for FCF lookups).
        min_market_cap: Market-cap floor. Default $500M.
        max_de: Loose D/E ceiling for quality gates (the posture-
            specific tightening happens in ranking).
        second_level_analyzer: Optional LLM second-level analyzer.
            When provided (live mode), each top candidate is sent
            for a second-level memo; REJECT verdicts veto the buy.
        decision_logger: Optional audit trail.
    """

    name = "howard_marks"

    def __init__(
        self,
        *,
        edgar_cache: EdgarCache,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
        max_de: float = DEFAULT_MAX_DE,
        second_level_analyzer: SecondLevelAnalyzer | None = None,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if min_market_cap <= 0:
            raise ValueError(f"min_market_cap must be positive; got {min_market_cap}")
        if max_de <= 0:
            raise ValueError(f"max_de must be positive; got {max_de}")
        if edgar_cache is None:
            raise ValueError("edgar_cache is required for the Marks strategy")
        self.edgar_cache = edgar_cache
        self.min_market_cap = min_market_cap
        self.max_de = max_de
        self.second_level_analyzer = second_level_analyzer
        self.decision_logger = decision_logger
        self.selection_history: list[MarksSelection] = []
        self.last_temperature: TemperatureAssessment | None = None
        self.last_memos: list[MarksMemo] = []

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
            f"{as_of}: starting Marks cycle-value scan over {len(universe)} candidates"
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

        # Stage 2: market temperature over the post-gate universe.
        temperature = assess_market_temperature(survivors, as_of=as_of)
        self.last_temperature = temperature
        profile = profile_for(temperature.posture)

        logger.info(
            f"{as_of}: posture={temperature.posture} → "
            f"size={profile.portfolio_size}, "
            f"deploy={profile.deployed_fraction:.0%}"
        )

        # Stage 3: cycle-adjusted ranking.
        scores = score_candidates(
            survivors,
            as_of=as_of,
            edgar_cache=self.edgar_cache,
            temperature=temperature,
        )
        top = select_top_n(scores, n=profile.portfolio_size)

        # Stage 4 (live only): LLM second-level analyzer.
        memos: list[MarksMemo] = []
        if self.second_level_analyzer is not None and top:
            top, memos = self._llm_filter(top, as_of, temperature)
        self.last_memos = memos

        if not top:
            logger.warning(
                f"{as_of}: no Marks candidates qualified — staying in cash"
            )
            self._record(
                as_of,
                len(universe),
                with_data,
                len(survivors),
                0,
                temperature,
                profile.deployed_fraction,
                [],
                [],
                0,
            )
            return {}

        # Equal-weight within the deployed fraction. Residual = cash.
        per_position = profile.deployed_fraction / len(top)
        # Respect per-name cap (rare for the equal-weight path; matters
        # only when posture's profile has a tight max_single).
        cap = profile.max_single_position_pct / 100.0
        weight = min(per_position, cap)
        weights = {s.ticker: weight for s in top}

        self._record(
            as_of,
            len(universe),
            with_data,
            len(survivors),
            len(scores),
            temperature,
            profile.deployed_fraction,
            list(weights.keys()),
            top,
            len(memos),
        )
        if self.decision_logger is not None:
            self._log_decisions(as_of, top, scores, memos, temperature)
        median_score = sorted(s.total_score for s in top)[len(top) // 2]
        logger.info(
            f"{as_of}: selected {len(top)} stocks "
            f"(top score {top[0].total_score:.2f}, median {median_score:.2f}, "
            f"posture {temperature.posture})"
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
        top: list[MarksScore],
        as_of: date,
        temperature: TemperatureAssessment,
    ) -> tuple[list[MarksScore], list[MarksMemo]]:
        """Ask the LLM for second-level analysis. Drop REJECT/SELL."""
        kept: list[MarksScore] = []
        memos: list[MarksMemo] = []
        analyzer = self.second_level_analyzer
        assert analyzer is not None
        for s in top:
            stock_data = {
                "ticker": s.ticker,
                "as_of": as_of.isoformat(),
                "price": s.price,
                "market_cap": s.market_cap,
                "pe": s.pe,
                "earnings_yield_pct": s.earnings_yield_pct,
                "fcf_yield_pct": s.fcf_yield_pct,
                "dividend_yield_pct": s.dividend_yield_pct,
                "debt_to_equity": s.debt_to_equity,
                "market_temperature": {
                    "score": temperature.score,
                    "posture": temperature.posture,
                    "votes": dict(temperature.votes),
                    "median_pe": temperature.signals.median_pe,
                    "frac_negative_ni": temperature.signals.frac_negative_ni,
                    "median_de": temperature.signals.median_de,
                    "median_yield_pct": temperature.signals.median_yield_pct,
                },
            }
            try:
                memo = analyzer.analyze(
                    stock_data=stock_data,
                    portfolio_state={
                        "as_of": as_of.isoformat(),
                        "posture": temperature.posture,
                    },
                )
            except Exception as exc:
                logger.warning(
                    f"{as_of} {s.ticker}: second-level analyzer failed ({exc}); "
                    "keeping quant-only verdict"
                )
                kept.append(s)
                continue
            memos.append(memo)
            if memo.decision in ("BUY", "HOLD", "ADD"):
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
        temperature: TemperatureAssessment,
        deployed_fraction: float,
        selected: list[str],
        top: list[MarksScore],
        memos_collected: int,
    ) -> None:
        self.selection_history.append(
            MarksSelection(
                as_of=as_of,
                universe_size=universe_size,
                candidates_with_data=with_data,
                candidates_after_quality=survivors,
                candidates_after_screen=after_screen,
                posture=temperature.posture,
                temperature_score=temperature.score,
                deployed_fraction=deployed_fraction,
                selected_tickers=selected,
                top_scores=top,
                memos_collected=memos_collected,
            )
        )

    def _log_decisions(
        self,
        as_of: date,
        top: list[MarksScore],
        all_scores: list[MarksScore],
        memos: list[MarksMemo],
        temperature: TemperatureAssessment,
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
                            f"posture {temperature.posture}",
                            f"earnings yield {s.earnings_yield_pct:.2f}%",
                            f"FCF yield {s.fcf_yield_pct:.2f}%",
                            f"dividend yield {s.dividend_yield_pct:.2f}%",
                            f"D/E {s.debt_to_equity:.2f}",
                            f"score {s.total_score:.2f}",
                        ],
                        criteria_values={
                            "posture": temperature.posture,
                            "temperature_score": temperature.score,
                            "pe": round(s.pe, 4),
                            "earnings_yield_pct": round(s.earnings_yield_pct, 4),
                            "fcf_yield_pct": round(s.fcf_yield_pct, 4),
                            "dividend_yield_pct": round(s.dividend_yield_pct, 4),
                            "debt_to_equity": round(s.debt_to_equity, 4),
                            "total_score": round(s.total_score, 4),
                            "net_income": s.net_income,
                            "market_cap": s.market_cap,
                            "price": s.price,
                        },
                        confidence=(
                            memo.confidence
                            if memo is not None
                            else max(0.0, min(1.0, s.total_score / 20.0))
                        ),
                        entry_price=s.price,
                        target_price=None,
                        exit_trigger=(
                            "; ".join(
                                f"trim at {p:.2f}"
                                for p in memo.scaling_out_plan.trim_at_price_levels
                            )
                            if memo is not None
                            and memo.scaling_out_plan.trim_at_price_levels
                            else (
                                "cycle peak signals OR thesis break OR "
                                "fundamental balance-sheet deterioration"
                            )
                        ),
                        rationale=(
                            memo.thesis_en
                            if memo is not None
                            else (
                                f"Marks {temperature.posture}: "
                                f"earnings yield {s.earnings_yield_pct:.1f}%, "
                                f"FCF yield {s.fcf_yield_pct:.1f}%, "
                                f"D/E {s.debt_to_equity:.2f}"
                            )
                        ),
                    )
                )
            except Exception as exc:
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
                "posture": sel.posture,
                "temperature_score": sel.temperature_score,
                "deployed_fraction": sel.deployed_fraction,
                "selected_tickers": list(sel.selected_tickers),
                "memos_collected": sel.memos_collected,
                "top_score": (
                    sel.top_scores[0].total_score if sel.top_scores else None
                ),
                "median_score": (
                    sorted(s.total_score for s in sel.top_scores)[
                        len(sel.top_scores) // 2
                    ]
                    if sel.top_scores
                    else None
                ),
            }
            for sel in self.selection_history
        ]


__all__ = ["HowardMarks", "MarksSelection"]
