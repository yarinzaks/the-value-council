"""Peter Lynch "Growth At a Reasonable Price" strategy.

Plug-in to ``core.backtest.Strategy`` — implements playbook §4-§6:

  1. Quality gates — earnings consistency (≥ 8/10 yrs), D/E ≤ 0.5,
     positive FCF, market cap floor.
  2. Compute trailing 5-yr EPS CAGR + dividend yield.
  3. Heuristic-classify into one of three quant-friendly Lynch
     categories (Slow Grower / Stalwart / Fast Grower).
  4. Compute PEG (PEGY for Slow Growers); enforce category-specific
     PEG ceiling.
  5. Rank by PEG ascending.
  6. Take top ``portfolio_size`` (default 30 — Lynch's broad-
     diversification posture, scaled for our $10K paper book).
  7. Equal-weight, annual rebalance.

The optional ``category_classifier`` (:class:`CategoryClassifier`)
is consulted in live mode for top candidates. It can:
  * Reclassify (e.g., a "Stalwart" by quant signature is actually
    a Cyclical at trough)
  * Veto (REJECT)
  * Add the bilingual two-minute drill + exit triggers

Backtest runs WITHOUT the LLM — same lookahead-bias rationale as
Buffett's moat analyzer. Documented in
``run_full_market_validation.py``.
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

from .category_classifier import CategoryClassifier, LynchMemo
from .filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    apply_quality_gates,
)
from .ranking import LynchScore, score_candidates, select_top_n

logger = get_logger("agents.lynch.garp")

#: Default position count — Lynch ran 1,400 in Magellan; we target
#: the lower end of his §6.1 range (30-50) for our $10K paper book.
DEFAULT_PORTFOLIO_SIZE: int = 30


@dataclass
class LynchSelection:
    """Audit record per rebalance."""

    as_of: date
    universe_size: int
    candidates_with_data: int
    candidates_after_quality: int
    candidates_after_screen: int
    selected_tickers: list[str] = field(default_factory=list)
    top_scores: list[LynchScore] = field(default_factory=list)
    memos_collected: int = 0


class PeterLynch(Strategy):
    """Growth At a Reasonable Price.

    Args:
        edgar_cache: EDGAR XBRL cache (required for trailing EPS CAGR).
        portfolio_size: Target number of holdings. Default 30
            (Lynch's broad diversification, scaled).
        min_market_cap: Market-cap floor. Default $300M (lets Lynch's
            small/mid-cap focus through).
        max_de: D/E ceiling. Default 0.5.
        category_classifier: Optional LLM-backed classifier. When
            provided (live mode), each top candidate is classified
            into the full 6-category space; REJECT memos drop the
            candidate.
        decision_logger: Optional audit trail.
    """

    name = "peter_lynch"

    def __init__(
        self,
        *,
        edgar_cache: EdgarCache,
        portfolio_size: int = DEFAULT_PORTFOLIO_SIZE,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
        max_de: float = DEFAULT_MAX_DE,
        category_classifier: CategoryClassifier | None = None,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if portfolio_size <= 0:
            raise ValueError(f"portfolio_size must be positive; got {portfolio_size}")
        if min_market_cap <= 0:
            raise ValueError(f"min_market_cap must be positive; got {min_market_cap}")
        if max_de <= 0:
            raise ValueError(f"max_de must be positive; got {max_de}")
        if edgar_cache is None:
            raise ValueError("edgar_cache is required for the Lynch strategy")
        self.edgar_cache = edgar_cache
        self.portfolio_size = portfolio_size
        self.min_market_cap = min_market_cap
        self.max_de = max_de
        self.category_classifier = category_classifier
        self.decision_logger = decision_logger
        self.selection_history: list[LynchSelection] = []
        self.last_memos: list[LynchMemo] = []

    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
    ) -> dict[str, float]:
        logger.info(
            f"{as_of}: starting Lynch GARP scan over {len(universe)} candidates"
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
            cache=self.edgar_cache,
            as_of=as_of,
            min_market_cap=self.min_market_cap,
            max_de=self.max_de,
        )

        # Stage 2: PEG-based scoring + category classification.
        scores = score_candidates(
            survivors,
            as_of=as_of,
            edgar_cache=self.edgar_cache,
        )
        top = select_top_n(scores, n=self.portfolio_size)

        # Stage 3 (live only): LLM category re-classification + veto.
        memos: list[LynchMemo] = []
        if self.category_classifier is not None and top:
            top, memos = self._llm_filter(top, as_of)
        self.last_memos = memos

        if not top:
            logger.warning(
                f"{as_of}: no Lynch candidates qualified — staying in cash"
            )
            self._record(
                as_of, len(universe), with_data, len(survivors), 0, [], [], 0
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
            len(memos),
        )
        if self.decision_logger is not None:
            self._log_decisions(as_of, top, scores, memos)
        median_peg = sorted(s.peg for s in top)[len(top) // 2]
        logger.info(
            f"{as_of}: selected {len(top)} stocks "
            f"(top PEG {top[0].peg:.2f}, median {median_peg:.2f})"
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
        top: list[LynchScore],
        as_of: date,
    ) -> tuple[list[LynchScore], list[LynchMemo]]:
        """Ask the LLM to classify + verify each top candidate.

        Memos for non-rejected candidates are returned alongside the
        kept scores.
        """
        kept: list[LynchScore] = []
        memos: list[LynchMemo] = []
        classifier = self.category_classifier
        assert classifier is not None
        for s in top:
            stock_data = {
                "ticker": s.ticker,
                "as_of": as_of.isoformat(),
                "price": s.price,
                "market_cap": s.market_cap,
                "pe": s.pe,
                "growth_rate_5yr_pct": s.growth_rate_5yr_pct,
                "growth_rate_3yr_pct": s.growth_rate_3yr_pct,
                "growth_acceleration_pct": s.growth_acceleration_pct,
                "dividend_yield_pct": s.dividend_yield_pct,
                "peg": s.peg,
                "pegy": s.pegy,
                "debt_to_equity": s.debt_to_equity,
                "heuristic_category": s.lynch_category,
            }
            try:
                memo = classifier.classify(
                    stock_data=stock_data,
                    portfolio_state={"as_of": as_of.isoformat()},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"{as_of} {s.ticker}: classifier failed ({exc}); "
                    "keeping quant-only verdict"
                )
                kept.append(s)
                continue
            memos.append(memo)
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
        top: list[LynchScore],
        memos_collected: int,
    ) -> None:
        self.selection_history.append(
            LynchSelection(
                as_of=as_of,
                universe_size=universe_size,
                candidates_with_data=with_data,
                candidates_after_quality=survivors,
                candidates_after_screen=after_screen,
                selected_tickers=selected,
                top_scores=top,
                memos_collected=memos_collected,
            )
        )

    def _log_decisions(
        self,
        as_of: date,
        top: list[LynchScore],
        all_scores: list[LynchScore],
        memos: list[LynchMemo],
    ) -> None:
        timestamp = f"{as_of.isoformat()}T00:00:00+00:00"
        selected_set = {s.ticker for s in top}
        watch = [s for s in all_scores if s.ticker not in selected_set][
            : self.portfolio_size
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
                            f"{s.lynch_category}",
                            f"PEG {s.peg:.2f}",
                            f"PEGY {s.pegy:.2f}",
                            f"5yr EPS CAGR {s.growth_rate_5yr_pct:.1f}%",
                            f"P/E {s.pe:.2f}",
                            f"yield {s.dividend_yield_pct:.1f}%",
                            f"D/E {s.debt_to_equity:.2f}",
                        ],
                        criteria_values={
                            "lynch_category": s.lynch_category,
                            "pe": round(s.pe, 4),
                            "growth_rate_5yr_pct": round(s.growth_rate_5yr_pct, 4),
                            "growth_rate_3yr_pct": (
                                round(s.growth_rate_3yr_pct, 4)
                                if s.growth_rate_3yr_pct is not None
                                else None
                            ),
                            "growth_acceleration_pct": (
                                round(s.growth_acceleration_pct, 4)
                                if s.growth_acceleration_pct is not None
                                else None
                            ),
                            "dividend_yield_pct": round(s.dividend_yield_pct, 4),
                            "peg": round(s.peg, 4),
                            "pegy": round(s.pegy, 4),
                            "peg_zone": s.peg_zone,
                            "debt_to_equity": round(s.debt_to_equity, 4),
                            "suggested_position_size_pct": s.suggested_position_size_pct,
                            "net_income": s.net_income,
                            "market_cap": s.market_cap,
                            "price": s.price,
                        },
                        confidence=(
                            memo.confidence
                            if memo is not None
                            else max(0.0, min(1.0, 1.0 - s.peg / 2.0))
                        ),
                        entry_price=s.price,
                        target_price=None,
                        exit_trigger=(
                            "; ".join(memo.exit_triggers)
                            if memo is not None and memo.exit_triggers
                            else (
                                "PEG > 1.5 OR earnings growth decelerates "
                                "below 15% OR thesis-specific story break"
                            )
                        ),
                        rationale=(
                            memo.thesis_en
                            if memo is not None
                            else (
                                f"Lynch {s.lynch_category}: PEG {s.peg:.2f}, "
                                f"5yr CAGR {s.growth_rate_5yr_pct:.1f}%, "
                                f"P/E {s.pe:.2f}, yield {s.dividend_yield_pct:.1f}%"
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
                "selected_tickers": list(sel.selected_tickers),
                "memos_collected": sel.memos_collected,
                "top_peg": (
                    sel.top_scores[0].peg if sel.top_scores else None
                ),
                "median_peg": (
                    sorted(s.peg for s in sel.top_scores)[len(sel.top_scores) // 2]
                    if sel.top_scores
                    else None
                ),
            }
            for sel in self.selection_history
        ]


__all__ = ["DEFAULT_PORTFOLIO_SIZE", "LynchSelection", "PeterLynch"]
