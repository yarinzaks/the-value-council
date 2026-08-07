"""Magic Formula strategy — Greenblatt's mechanical Mode A.

Implements the strategy described in Section 4.2 of the Greenblatt
playbook, plugged into the existing :class:`core.backtest.Strategy`
framework.

Signature behavior:

1. At each rebalance date, fetch point-in-time financials for every
   ticker in the universe.
2. Apply universe filters (size, sector, EBIT positive,
   earnings-recency).
3. Compute Earnings Yield and Return on Capital for survivors.
4. Combine the two ranks (sum); take the top N (default 30).
5. Equal-weight the selected tickers.

The 1-year holding period is enforced naturally by setting the
backtest runner's ``rebalance_freq`` to ``"annual"``.
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

from .exits import DEFAULT_HOLDING_PERIOD_DAYS, build_targets
from .filters import DEFAULT_MIN_MARKET_CAP_USD, filter_candidates
from .ranking import MagicFormulaScore, score_candidates

logger = get_logger("agents.greenblatt.magic_formula")


@dataclass
class MagicFormulaSelection:
    """Audit trail of one rebalance: what was screened, scored, picked."""

    as_of: date
    universe_size: int
    candidates_with_data: int
    candidates_after_filters: int
    candidates_scored: int
    selected_tickers: list[str]
    top_scores: list[MagicFormulaScore]
    # Full ranked list (after filters + scoring). Populated for live
    # adapters that need to derive a watchlist from positions
    # immediately below the buy threshold.
    all_scores: list[MagicFormulaScore] = field(default_factory=list)


class MagicFormula(Strategy):
    """Greenblatt's Magic Formula as an executable backtest strategy.

    Configuration parameters mirror the playbook:

    Args:
        portfolio_size: Number of stocks to hold (Greenblatt's book
            recommends 20-30; default 30).
        min_market_cap: Minimum market cap in USD ($1B per the
            playbook's retirement-account guidance).
        earnings_recency_days: Reject stocks whose filing landed within
            this many days of the rebalance.
    """

    name = "greenblatt_magic_formula"

    def __init__(
        self,
        *,
        portfolio_size: int = 30,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
        earnings_recency_days: int = 7,
        holding_period_days: int = DEFAULT_HOLDING_PERIOD_DAYS,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if portfolio_size <= 0:
            raise ValueError(f"portfolio_size must be positive; got {portfolio_size}")
        if min_market_cap <= 0:
            raise ValueError(f"min_market_cap must be positive; got {min_market_cap}")
        self.portfolio_size = portfolio_size
        self.min_market_cap = min_market_cap
        self.earnings_recency_days = earnings_recency_days
        self.holding_period_days = holding_period_days
        self.decision_logger = decision_logger
        self.selection_history: list[MagicFormulaSelection] = []

    # ------------------------------------------------------------------
    # Strategy.select
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
        """Choose target weights for ``as_of``.

        The runner provides the universe and the point-in-time
        fundamentals lookup. We do all the rest.

        Which universe arrives matters for how the result should be
        read: the S&P 500 backtests pass a survivorship-bias-free one,
        the live runner passes :class:`FullMarketUniverse`, which is
        not. Selection here is identical either way — the difference
        lands in the historical return, not in the picks.
        """
        logger.info(
            f"{as_of}: starting Magic Formula selection over {len(universe)} candidates"
        )

        # 1. Pull financials and current market caps for the universe.
        candidates: list[tuple[PointInTimeFinancials | None, float | None]] = []
        for ticker in universe:
            fin = fundamentals.get(ticker)
            mcap = self._market_cap(ticker, fin, prices)
            candidates.append((fin, mcap))

        with_data = sum(1 for fin, _ in candidates if fin is not None)

        # 2. Apply filters.
        survivors = filter_candidates(
            candidates,
            as_of=as_of,
            min_market_cap=self.min_market_cap,
            earnings_recency_days=self.earnings_recency_days,
        )

        # 3. Score & rank.
        scores = score_candidates(survivors)

        # 4. Apply the holding period before taking the top N. The
        #    formula's edge is the ranking; the one-year hold is what
        #    lets it survive the stretches where it lags. Without it the
        #    book churned 28% in three trading days.
        ranked = [s.ticker for s in scores]
        chosen = build_targets(
            ranked,
            held,
            as_of,
            portfolio_size=self.portfolio_size,
            holding_period_days=self.holding_period_days,
        )
        by_ticker = {s.ticker: s for s in scores}
        top = [by_ticker[t] for t in chosen if t in by_ticker]
        # A protected holding that no longer scores today (it dropped out
        # of the filtered universe) still has to keep its slot, so pad
        # the count even though we have no fresh score object for it.
        unscored = [t for t in chosen if t not in by_ticker]
        if unscored:
            logger.info(
                f"{as_of}: {len(unscored)} held name(s) no longer scoring but "
                f"still inside the holding period: {', '.join(sorted(unscored))}"
            )

        # 5. Equal weighting.
        if not top and not unscored:
            logger.warning(f"{as_of}: no Magic Formula candidates qualified — staying in cash")
            self._record(as_of, len(universe), with_data, len(survivors), 0, [], [], scores)
            return {}

        # Weight over every chosen slot, scored or not — an unscored
        # holding still occupies a slot and must not have its weight
        # redistributed to the others.
        weight = 1.0 / len(chosen)
        weights = {t: weight for t in chosen}
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
            self._log_decisions(as_of, top, scores, prices)
        logger.info(
            f"{as_of}: selected {len(top)} stocks "
            f"(top combined rank: {top[0].combined_rank}, top EY: {top[0].earnings_yield:.4f})"
        )
        return weights

    def _log_decisions(
        self,
        as_of: date,
        top: list[MagicFormulaScore],
        all_scores: list[MagicFormulaScore],
        prices: PriceLookup,
    ) -> None:
        """Persist a Decision per scored candidate.

        BUY for the selected top-N; WATCH for the next portfolio_size
        candidates that scored but didn't make the cut. We deliberately
        DO NOT log REJECTs from the filter pipeline — that would be
        thousands of entries per rebalance with little signal value.
        """
        timestamp = f"{as_of.isoformat()}T00:00:00+00:00"
        selected_set = {s.ticker for s in top}
        # Up to portfolio_size near-misses get WATCH status
        watch = [
            s
            for s in all_scores
            if s.ticker not in selected_set
        ][: self.portfolio_size]
        for s in top + watch:
            decision_type = "BUY" if s.ticker in selected_set else "WATCH"
            criteria_values: dict[str, float | int | str | None] = {
                "earnings_yield": round(s.earnings_yield, 6),
                "return_on_capital": round(s.return_on_capital, 6),
                "ey_rank": s.ey_rank,
                "roc_rank": s.roc_rank,
                "combined_rank": s.combined_rank,
                "market_cap": s.market_cap,
                "enterprise_value": s.enterprise_value,
                "invested_capital": s.invested_capital,
            }
            try:
                self.decision_logger.log(
                    make_decision(
                        ticker=s.ticker,
                        decision=decision_type,
                        agent=self.name,
                        timestamp=timestamp,
                        criteria_met=[
                            f"EY rank #{s.ey_rank}",
                            f"ROC rank #{s.roc_rank}",
                            f"combined rank #{s.combined_rank}",
                        ],
                        criteria_values=criteria_values,
                        confidence=max(0.0, min(1.0, 1.0 - (s.combined_rank / 200))),
                        entry_price=prices.get(s.ticker),
                        exit_trigger="annual rotation OR rank slips out of top-N",
                        rationale=(
                            f"Magic Formula rank {s.combined_rank}: "
                            f"EY={s.earnings_yield:.3f}, ROC={s.return_on_capital:.3f}"
                        ),
                    )
                )
            except Exception as exc:
                logger.warning(f"decision log failed for {s.ticker}: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _market_cap(
        ticker: str,
        fin: PointInTimeFinancials | None,
        prices: PriceLookup,
    ) -> float | None:
        """Compute market cap = price × shares outstanding.

        The price lookup returns the most recent price on or before the
        rebalance date; shares outstanding comes from the most recent
        filing as of the same date — this is the genuinely PIT-correct
        market cap.
        """
        if fin is None or fin.shares_outstanding is None:
            return None
        price = prices.get(ticker)
        if price is None or price <= 0:
            return None
        return price * fin.shares_outstanding

    def _record(
        self,
        as_of: date,
        universe_size: int,
        with_data: int,
        survivors: int,
        scored: int,
        selected: list[str],
        top: list[MagicFormulaScore],
        all_scores: list[MagicFormulaScore] | None = None,
    ) -> None:
        self.selection_history.append(
            MagicFormulaSelection(
                as_of=as_of,
                universe_size=universe_size,
                candidates_with_data=with_data,
                candidates_after_filters=survivors,
                candidates_scored=scored,
                selected_tickers=selected,
                top_scores=top,
                all_scores=list(all_scores or []),
            )
        )

    def selections_to_records(self) -> list[dict[str, Any]]:
        """Flatten the selection history into JSON-friendly records."""
        records: list[dict[str, Any]] = []
        for sel in self.selection_history:
            records.append(
                {
                    "as_of": sel.as_of.isoformat(),
                    "universe_size": sel.universe_size,
                    "candidates_with_data": sel.candidates_with_data,
                    "candidates_after_filters": sel.candidates_after_filters,
                    "candidates_scored": sel.candidates_scored,
                    "selected_tickers": list(sel.selected_tickers),
                    "top_score_combined_rank": sel.top_scores[0].combined_rank
                    if sel.top_scores
                    else None,
                    "top_score_earnings_yield": sel.top_scores[0].earnings_yield
                    if sel.top_scores
                    else None,
                    "top_score_roc": sel.top_scores[0].return_on_capital
                    if sel.top_scores
                    else None,
                }
            )
        return records


__all__ = ["MagicFormula", "MagicFormulaSelection"]
