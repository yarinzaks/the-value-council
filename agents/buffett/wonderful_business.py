"""Warren Buffett "Wonderful Business at a Fair Price" strategy.

Plug-in to ``core.backtest.Strategy`` — implements playbook §4 + §6:

  1. Quality gates (Berkshire Acquisition Criteria — six hard filters).
  2. Compute Owner Earnings 5-yr avg + DCF intrinsic value.
  3. Rank survivors by margin of safety (15% MoS minimum).
  4. Take top ``portfolio_size`` (Buffett: 6-10 concentrated).
  5. Equal-weight, annual rebalance.

The optional ``moat_analyzer`` (:class:`MoatAnalyzer`) is consulted
for each top-ranked candidate WHEN provided. In backtest mode it's
``None`` — see ``run_full_market_validation.py`` for the rationale
(lookahead bias + LLM cost). In live mode it's wired through
``core.live.runner``.

Concentration: Buffett targets 6-10 positions. We default to 8.
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
    DEFAULT_MIN_AVG_ROE_PCT,
    DEFAULT_MIN_MARKET_CAP_USD,
    apply_quality_gates,
)
from .moat_analyzer import BuffettMemo, MoatAnalyzer
from .ranking import (
    DEFAULT_MIN_MARGIN_OF_SAFETY_PCT,
    BuffettScore,
    score_candidates,
    select_top_n,
)

logger = get_logger("agents.buffett.wonderful_business")

#: Default concentration target — Buffett's lower-bound portfolio size
#: for the equity book, scaled to our $10K paper-portfolio context.
DEFAULT_PORTFOLIO_SIZE: int = 8


@dataclass
class BuffettSelection:
    """Audit record per rebalance."""

    as_of: date
    universe_size: int
    candidates_with_data: int
    candidates_after_quality: int
    candidates_after_screen: int
    selected_tickers: list[str] = field(default_factory=list)
    top_scores: list[BuffettScore] = field(default_factory=list)
    memos_collected: int = 0  # how many LLM memos were generated


class WarrenBuffett(Strategy):
    """Wonderful business at a fair price.

    Args:
        edgar_cache: EDGAR XBRL cache (required for owner earnings +
            ROE history).
        portfolio_size: Target number of holdings. Default 8 (Buffett:
            6-10).
        min_market_cap: Market cap floor. Default $5B per playbook
            §4.1 #1.
        min_avg_roe_pct: 5-yr avg ROE floor in %. Default 15% per
            §4.1 #3.
        max_de: Debt/equity ceiling. Default 0.5 per §4.1 #3.
        min_mos_pct: Margin-of-safety floor in %. Default 15% per
            §12.1 step 8.
        moat_analyzer: Optional LLM-backed qualitative analyzer. When
            ``None`` (backtest), the strategy is purely quantitative.
            When provided (live), each top candidate is sent for
            moat verification — REJECT memos drop the candidate from
            the buy list.
        decision_logger: Optional audit trail.
    """

    name = "warren_buffett"

    def __init__(
        self,
        *,
        edgar_cache: EdgarCache,
        portfolio_size: int = DEFAULT_PORTFOLIO_SIZE,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
        min_avg_roe_pct: float = DEFAULT_MIN_AVG_ROE_PCT,
        max_de: float = DEFAULT_MAX_DE,
        min_mos_pct: float = DEFAULT_MIN_MARGIN_OF_SAFETY_PCT,
        moat_analyzer: MoatAnalyzer | None = None,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if portfolio_size <= 0:
            raise ValueError(f"portfolio_size must be positive; got {portfolio_size}")
        if min_market_cap <= 0:
            raise ValueError(f"min_market_cap must be positive; got {min_market_cap}")
        if max_de <= 0:
            raise ValueError(f"max_de must be positive; got {max_de}")
        if edgar_cache is None:
            raise ValueError("edgar_cache is required for the Buffett strategy")
        self.edgar_cache = edgar_cache
        self.portfolio_size = portfolio_size
        self.min_market_cap = min_market_cap
        self.min_avg_roe_pct = min_avg_roe_pct
        self.max_de = max_de
        self.min_mos_pct = min_mos_pct
        self.moat_analyzer = moat_analyzer
        self.decision_logger = decision_logger
        self.selection_history: list[BuffettSelection] = []
        self.last_memos: list[BuffettMemo] = []

    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
    ) -> dict[str, float]:
        logger.info(
            f"{as_of}: starting Buffett scan over {len(universe)} candidates"
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

        # Stage 1: Berkshire Acquisition Criteria (six hard gates).
        survivors = apply_quality_gates(
            triples,
            cache=self.edgar_cache,
            as_of=as_of,
            min_market_cap=self.min_market_cap,
            min_avg_roe_pct=self.min_avg_roe_pct,
            max_de=self.max_de,
        )

        # Stage 2: DCF intrinsic value + MoS rank.
        scores = score_candidates(
            survivors,
            as_of=as_of,
            edgar_cache=self.edgar_cache,
            min_mos_pct=self.min_mos_pct,
        )
        top = select_top_n(scores, n=self.portfolio_size)

        # Stage 3 (live only): LLM moat verification.
        memos: list[BuffettMemo] = []
        if self.moat_analyzer is not None and top:
            top, memos = self._llm_filter(top, as_of)
        self.last_memos = memos

        if not top:
            logger.warning(
                f"{as_of}: no Buffett candidates qualified — staying in cash"
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
        median_mos = sorted(s.margin_of_safety_pct for s in top)[len(top) // 2]
        logger.info(
            f"{as_of}: selected {len(top)} stocks "
            f"(top MoS {top[0].margin_of_safety_pct:.1f}%, "
            f"median {median_mos:.1f}%)"
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
        top: list[BuffettScore],
        as_of: date,
    ) -> tuple[list[BuffettScore], list[BuffettMemo]]:
        """Ask the LLM to verify each top candidate. Drop REJECTs.

        Memos for non-rejected candidates are returned alongside the
        kept scores so the live runner can persist them.
        """
        kept: list[BuffettScore] = []
        memos: list[BuffettMemo] = []
        analyzer = self.moat_analyzer
        assert analyzer is not None  # narrow Optional
        for s in top:
            stock_data = {
                "ticker": s.ticker,
                "as_of": as_of.isoformat(),
                "price": s.price,
                "market_cap": s.market_cap,
                "intrinsic_value_usd": s.intrinsic_value_usd,
                "intrinsic_value_per_share": s.intrinsic_value_per_share,
                "margin_of_safety_pct": s.margin_of_safety_pct,
                "owner_earnings_5yr_avg_usd": s.avg_owner_earnings_usd,
                "growth_rate_pct": s.growth_rate_pct,
                "discount_rate_pct": s.discount_rate_pct,
                "avg_roe_5yr_pct": s.avg_roe_5yr_pct,
                "debt_to_equity": s.debt_to_equity,
                "valuation_notes": list(s.valuation_notes),
            }
            try:
                memo = analyzer.analyze(
                    stock_data=stock_data,
                    portfolio_state={"as_of": as_of.isoformat()},
                )
            except Exception as exc:  # noqa: BLE001 — LLM transients
                logger.warning(
                    f"{as_of} {s.ticker}: moat analyzer failed ({exc}); "
                    "keeping quant-only verdict"
                )
                kept.append(s)
                continue
            memos.append(memo)
            if memo.decision in ("BUY", "HOLD"):
                kept.append(s)
            else:
                logger.info(
                    f"{as_of} {s.ticker}: LLM verdict {memo.decision} — "
                    f"dropped from buy list"
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
        top: list[BuffettScore],
        memos_collected: int,
    ) -> None:
        self.selection_history.append(
            BuffettSelection(
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
        top: list[BuffettScore],
        all_scores: list[BuffettScore],
        memos: list[BuffettMemo],
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
                            f"market cap ${s.market_cap:,.0f}",
                            f"5-yr avg ROE {s.avg_roe_5yr_pct:.1f}%",
                            f"D/E {s.debt_to_equity:.2f}",
                            f"MoS {s.margin_of_safety_pct:.1f}%",
                            f"OE 5yr avg ${s.avg_owner_earnings_usd:,.0f}",
                        ],
                        criteria_values={
                            "intrinsic_value_usd": round(s.intrinsic_value_usd, 0),
                            "intrinsic_value_per_share": round(
                                s.intrinsic_value_per_share, 2
                            ),
                            "margin_of_safety_pct": round(
                                s.margin_of_safety_pct, 2
                            ),
                            "avg_owner_earnings_usd": round(
                                s.avg_owner_earnings_usd, 0
                            ),
                            "growth_rate_pct": round(s.growth_rate_pct, 2),
                            "discount_rate_pct": round(s.discount_rate_pct, 2),
                            "avg_roe_5yr_pct": round(s.avg_roe_5yr_pct, 2),
                            "debt_to_equity": round(s.debt_to_equity, 2),
                            "net_income": s.net_income,
                            "market_cap": s.market_cap,
                            "price": s.price,
                        },
                        confidence=(
                            memo.confidence
                            if memo is not None
                            else min(1.0, max(0.0, s.margin_of_safety_pct / 50.0))
                        ),
                        entry_price=s.price,
                        target_price=s.intrinsic_value_per_share,
                        exit_trigger=(
                            "; ".join(memo.exit_triggers)
                            if memo is not None and memo.exit_triggers
                            else (
                                "moat erosion OR management failure OR "
                                "extreme overvaluation > 1.5x intrinsic"
                            )
                        ),
                        rationale=(
                            memo.thesis_en
                            if memo is not None
                            else (
                                f"Buffett wonderful business: MoS "
                                f"{s.margin_of_safety_pct:.1f}%, "
                                f"5yr ROE {s.avg_roe_5yr_pct:.1f}%, "
                                f"D/E {s.debt_to_equity:.2f}, OE "
                                f"${s.avg_owner_earnings_usd / 1e6:.0f}M"
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
                "top_mos_pct": (
                    sel.top_scores[0].margin_of_safety_pct
                    if sel.top_scores
                    else None
                ),
                "median_mos_pct": (
                    sorted(s.margin_of_safety_pct for s in sel.top_scores)[
                        len(sel.top_scores) // 2
                    ]
                    if sel.top_scores
                    else None
                ),
            }
            for sel in self.selection_history
        ]


__all__ = ["BuffettSelection", "DEFAULT_PORTFOLIO_SIZE", "WarrenBuffett"]
