"""Seth Klarman "Margin of Safety" strategy.

Plug-in to ``core.backtest.Strategy``. Implements playbook §3-§7:

  1. Quality gates (light — the MoS floor is the real safety net).
  2. Conservative DCF on FCF + 30% MoS hard floor (:mod:`valuation`,
     :mod:`ranking`).
  3. **Cash-as-residual sizing** — the unique Klarman move
     (playbook §4.4 / §6.3). Cash position is OUTPUT of the
     opportunity set, not an input target.
  4. Equal-weight inside the deployed fraction; residual = cash.

Cash-as-residual logic per playbook §4.4:

    < 3 candidates  → cash 50-70% (deploy ≤ 50%)
    3-7 candidates  → cash 25-50% (deploy 50-75%)
    8-15 candidates → cash 10-25% (deploy 75-90%)
    15+ candidates  → cash 5-10%  (deploy 90-95%)

LLM downside analyzer (:mod:`downside`) is consulted in live mode for
each top candidate. REJECT/SELL/TRIM/WATCH verdicts veto the buy.
Backtest mode runs WITHOUT the LLM (lookahead bias + quota — see
``run_full_market_validation.py``).
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

from .downside import DownsideAnalyzer, KlarmanMemo
from .filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    apply_quality_gates,
)
from .ranking import KlarmanScore, score_candidates, select_top_n
from .valuation import DEFAULT_MIN_MARGIN_OF_SAFETY_PCT

logger = get_logger("agents.klarman.margin_of_safety")


#: Maximum portfolio size per playbook §6.1. Klarman ran 20-50; we
#: target the lower end of that range scaled to our $10K paper book.
DEFAULT_MAX_PORTFOLIO_SIZE: int = 20

#: Per-name cap per playbook §6.1.
DEFAULT_MAX_POSITION_PCT: float = 8.0


@dataclass(frozen=True)
class DeploymentDecision:
    """How wide and how much to deploy given the qualifying count."""

    portfolio_size: int
    deployed_fraction: float


def _deployment_for(qualifying_count: int) -> DeploymentDecision:
    """Cash-as-residual sizing per playbook §4.4."""
    if qualifying_count < 3:
        # < 3 compelling: deploy ≤ 50%; size = qualifying_count or 0.
        return DeploymentDecision(
            portfolio_size=qualifying_count, deployed_fraction=0.40
        )
    if qualifying_count <= 7:
        # 3-7 compelling: deploy 50-75%; equal-weight across all.
        return DeploymentDecision(
            portfolio_size=qualifying_count, deployed_fraction=0.65
        )
    if qualifying_count <= 15:
        # 8-15 compelling: deploy 75-90%; cap size at qualifying.
        return DeploymentDecision(
            portfolio_size=qualifying_count, deployed_fraction=0.85
        )
    # 15+ compelling — rare regime.
    return DeploymentDecision(
        portfolio_size=DEFAULT_MAX_PORTFOLIO_SIZE, deployed_fraction=0.92
    )


@dataclass
class KlarmanSelection:
    """Audit record per rebalance."""

    as_of: date
    universe_size: int
    candidates_with_data: int
    candidates_after_quality: int
    candidates_after_screen: int
    deployed_fraction: float
    selected_tickers: list[str] = field(default_factory=list)
    top_scores: list[KlarmanScore] = field(default_factory=list)
    memos_collected: int = 0


class SethKlarman(Strategy):
    """Margin-of-Safety strategy with cash-as-residual sizing.

    Args:
        edgar_cache: EDGAR XBRL cache (required for FCF history).
        min_market_cap: Market-cap floor. Default $500M.
        max_de: D/E ceiling. Default 0.7.
        min_mos_pct: MoS floor. Default 30% (playbook §4.2).
        max_portfolio_size: Cap on positions. Default 20.
        max_position_pct: Per-name cap in %. Default 8.
        downside_analyzer: Optional LLM analyzer. Live mode only.
        decision_logger: Optional audit trail.
    """

    name = "seth_klarman"

    def __init__(
        self,
        *,
        edgar_cache: EdgarCache,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
        max_de: float = DEFAULT_MAX_DE,
        min_mos_pct: float = DEFAULT_MIN_MARGIN_OF_SAFETY_PCT,
        max_portfolio_size: int = DEFAULT_MAX_PORTFOLIO_SIZE,
        max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
        downside_analyzer: DownsideAnalyzer | None = None,
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
        if max_position_pct <= 0 or max_position_pct > 100:
            raise ValueError(
                f"max_position_pct must be in (0, 100]; got {max_position_pct}"
            )
        if edgar_cache is None:
            raise ValueError("edgar_cache is required for the Klarman strategy")
        self.edgar_cache = edgar_cache
        self.min_market_cap = min_market_cap
        self.max_de = max_de
        self.min_mos_pct = min_mos_pct
        self.max_portfolio_size = max_portfolio_size
        self.max_position_pct = max_position_pct
        self.downside_analyzer = downside_analyzer
        self.decision_logger = decision_logger
        self.selection_history: list[KlarmanSelection] = []
        self.last_memos: list[KlarmanMemo] = []

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
            f"{as_of}: starting Klarman MoS scan over {len(universe)} candidates"
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

        # Stage 2: DCF + MoS rank (drops below MoS floor).
        scores = score_candidates(
            survivors,
            as_of=as_of,
            edgar_cache=self.edgar_cache,
            min_mos_pct=self.min_mos_pct,
        )

        # Stage 3: cash-as-residual deployment decision.
        qualifying = len(scores)
        deployment = _deployment_for(qualifying)
        portfolio_size = min(deployment.portfolio_size, self.max_portfolio_size)
        top = select_top_n(scores, n=portfolio_size) if portfolio_size > 0 else []

        logger.info(
            f"{as_of}: {qualifying} qualifying → "
            f"size={portfolio_size}, deploy={deployment.deployed_fraction:.0%}"
        )

        # Stage 4 (live only): LLM downside analyzer.
        memos: list[KlarmanMemo] = []
        if self.downside_analyzer is not None and top:
            top, memos = self._llm_filter(top, as_of)
        self.last_memos = memos

        if not top:
            logger.warning(
                f"{as_of}: no Klarman candidates qualified — staying in cash"
            )
            self._record(
                as_of,
                len(universe),
                with_data,
                len(survivors),
                qualifying,
                deployment.deployed_fraction,
                [],
                [],
                0,
            )
            return {}

        # Equal-weight inside deployed fraction; respect per-name cap.
        per_position = deployment.deployed_fraction / len(top)
        cap = self.max_position_pct / 100.0
        weight = min(per_position, cap)
        weights = {s.ticker: weight for s in top}

        self._record(
            as_of,
            len(universe),
            with_data,
            len(survivors),
            qualifying,
            deployment.deployed_fraction,
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
        top: list[KlarmanScore],
        as_of: date,
    ) -> tuple[list[KlarmanScore], list[KlarmanMemo]]:
        kept: list[KlarmanScore] = []
        memos: list[KlarmanMemo] = []
        analyzer = self.downside_analyzer
        assert analyzer is not None
        for s in top:
            stock_data = {
                "ticker": s.ticker,
                "as_of": as_of.isoformat(),
                "price": s.price,
                "market_cap": s.market_cap,
                "intrinsic_value_usd": s.intrinsic_value_usd,
                "intrinsic_value_per_share": s.intrinsic_value_per_share,
                "margin_of_safety_pct": s.margin_of_safety_pct,
                "avg_fcf_5yr_usd": s.avg_fcf_usd,
                "growth_rate_pct": s.growth_rate_pct,
                "discount_rate_pct": s.discount_rate_pct,
                "debt_to_equity": s.debt_to_equity,
                "valuation_notes": list(s.valuation_notes),
            }
            try:
                memo = analyzer.analyze(
                    stock_data=stock_data,
                    portfolio_state={"as_of": as_of.isoformat()},
                )
            except Exception as exc:
                logger.warning(
                    f"{as_of} {s.ticker}: downside analyzer failed ({exc}); "
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
        deployed_fraction: float,
        selected: list[str],
        top: list[KlarmanScore],
        memos_collected: int,
    ) -> None:
        self.selection_history.append(
            KlarmanSelection(
                as_of=as_of,
                universe_size=universe_size,
                candidates_with_data=with_data,
                candidates_after_quality=survivors,
                candidates_after_screen=after_screen,
                deployed_fraction=deployed_fraction,
                selected_tickers=selected,
                top_scores=top,
                memos_collected=memos_collected,
            )
        )

    def _log_decisions(
        self,
        as_of: date,
        top: list[KlarmanScore],
        all_scores: list[KlarmanScore],
        memos: list[KlarmanMemo],
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
                            f"MoS {s.margin_of_safety_pct:.1f}%",
                            f"5yr avg FCF ${s.avg_fcf_usd:,.0f}",
                            f"D/E {s.debt_to_equity:.2f}",
                        ],
                        criteria_values={
                            "intrinsic_value_usd": round(s.intrinsic_value_usd, 0),
                            "intrinsic_value_per_share": round(
                                s.intrinsic_value_per_share, 2
                            ),
                            "margin_of_safety_pct": round(
                                s.margin_of_safety_pct, 2
                            ),
                            "avg_fcf_usd": round(s.avg_fcf_usd, 0),
                            "growth_rate_pct": round(s.growth_rate_pct, 2),
                            "discount_rate_pct": round(s.discount_rate_pct, 2),
                            "debt_to_equity": round(s.debt_to_equity, 2),
                            "net_income": s.net_income,
                            "market_cap": s.market_cap,
                            "price": s.price,
                        },
                        confidence=(
                            memo.confidence
                            if memo is not None
                            else min(
                                1.0, max(0.0, s.margin_of_safety_pct / 70.0)
                            )
                        ),
                        entry_price=s.price,
                        target_price=s.intrinsic_value_per_share,
                        exit_trigger=(
                            "; ".join(memo.reverse_triggers)
                            if memo is not None and memo.reverse_triggers
                            else (
                                "scaling out as price approaches intrinsic value; "
                                "thesis break OR balance-sheet deterioration"
                            )
                        ),
                        rationale=(
                            memo.thesis_en
                            if memo is not None
                            else (
                                f"Klarman MoS: {s.margin_of_safety_pct:.1f}% "
                                f"discount to conservative DCF intrinsic, "
                                f"5yr FCF ${s.avg_fcf_usd / 1e6:.0f}M, "
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
                "deployed_fraction": sel.deployed_fraction,
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


__all__ = [
    "DEFAULT_MAX_PORTFOLIO_SIZE",
    "DEFAULT_MAX_POSITION_PCT",
    "DeploymentDecision",
    "KlarmanSelection",
    "SethKlarman",
    "_deployment_for",
]
