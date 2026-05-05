"""Benjamin Graham strategy — classic Net-Net + Defensive Investor fallback.

Plug-in to ``core.backtest.Strategy`` — implements both deep-value
modes from the Graham playbook:

1. **Classic Net-Net** (Section 4.3): Price ≤ ⅔ × NCAV per share.
2. **Defensive Investor** (The Intelligent Investor, Ch. 14):
   P/E ≤ 15, P/B ≤ 1.5, current ratio ≥ 2.0.

The strategy runs Net-Net first. If fewer than ``net_net_fallback_threshold``
(default 10) Net-Net candidates qualify — which is the modern reality,
post-1970 — it falls back to the Defensive Investor screen for the
remaining slots. The two cohorts are reported separately in decision
logs so the analyst can see which mode populated the portfolio.

Both modes share the non-negotiables: positive trailing earnings,
D/E ≤ 1.0, market cap ≥ $500M, exclude share classes/preferreds.
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
from core.logger import get_logger

from .filters import (
    DEFAULT_DEFENSIVE_MAX_PB,
    DEFAULT_DEFENSIVE_MAX_PE,
    DEFAULT_DEFENSIVE_MIN_CURRENT_RATIO,
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    DEFAULT_NCAV_DISCOUNT_FACTOR,
    DEFAULT_NET_NET_FALLBACK_THRESHOLD,
    filter_candidates,
    filter_defensive_candidates,
)
from .ranking import (
    DefensiveScore,
    GrahamScore,
    score_candidates,
    score_defensive_candidates,
    select_top_n,
)

logger = get_logger("agents.graham.net_net")


@dataclass
class GrahamSelection:
    """Audit record per rebalance.

    ``net_net_count`` and ``defensive_count`` together describe how
    the portfolio was filled — useful for analyzing how often the
    classic Net-Net screen produces enough candidates vs. how often
    the Defensive Investor fallback takes over.
    """

    as_of: date
    universe_size: int
    candidates_with_data: int
    candidates_after_filters: int  # Net-Net survivors
    defensive_candidates: int = 0  # Defensive survivors (used for fallback)
    net_net_count: int = 0  # selected via Net-Net
    defensive_count: int = 0  # selected via Defensive fallback
    selected_tickers: list[str] = field(default_factory=list)
    top_scores: list[GrahamScore] = field(default_factory=list)
    top_defensive: list[DefensiveScore] = field(default_factory=list)


class BenjaminGraham(Strategy):
    """Graham Net-Net strategy.

    Args:
        portfolio_size: Target number of holdings (Graham: minimum 30).
        max_p_ncav: Maximum acceptable P/NCAV ratio. Default ⅔ per the
            playbook.
        max_de: D/E ceiling. Default 1.0.
        min_market_cap: Market-cap floor in USD.
    """

    name = "benjamin_graham"

    def __init__(
        self,
        *,
        portfolio_size: int = 30,
        max_p_ncav: float = DEFAULT_NCAV_DISCOUNT_FACTOR,
        max_de: float = DEFAULT_MAX_DE,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
        # Defensive Investor fallback config
        enable_defensive_fallback: bool = True,
        net_net_fallback_threshold: int = DEFAULT_NET_NET_FALLBACK_THRESHOLD,
        defensive_max_pe: float = DEFAULT_DEFENSIVE_MAX_PE,
        defensive_max_pb: float = DEFAULT_DEFENSIVE_MAX_PB,
        defensive_min_current_ratio: float = DEFAULT_DEFENSIVE_MIN_CURRENT_RATIO,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if portfolio_size <= 0:
            raise ValueError(f"portfolio_size must be positive; got {portfolio_size}")
        if max_p_ncav <= 0:
            raise ValueError(f"max_p_ncav must be positive; got {max_p_ncav}")
        if max_de <= 0:
            raise ValueError(f"max_de must be positive; got {max_de}")
        if min_market_cap <= 0:
            raise ValueError(f"min_market_cap must be positive; got {min_market_cap}")
        if net_net_fallback_threshold < 0:
            raise ValueError(
                f"net_net_fallback_threshold must be >= 0; "
                f"got {net_net_fallback_threshold}"
            )
        if defensive_max_pe <= 0:
            raise ValueError(f"defensive_max_pe must be positive; got {defensive_max_pe}")
        if defensive_max_pb <= 0:
            raise ValueError(f"defensive_max_pb must be positive; got {defensive_max_pb}")
        if defensive_min_current_ratio <= 0:
            raise ValueError(
                f"defensive_min_current_ratio must be positive; "
                f"got {defensive_min_current_ratio}"
            )
        self.portfolio_size = portfolio_size
        self.max_p_ncav = max_p_ncav
        self.max_de = max_de
        self.min_market_cap = min_market_cap
        self.enable_defensive_fallback = enable_defensive_fallback
        self.net_net_fallback_threshold = net_net_fallback_threshold
        self.defensive_max_pe = defensive_max_pe
        self.defensive_max_pb = defensive_max_pb
        self.defensive_min_current_ratio = defensive_min_current_ratio
        self.decision_logger = decision_logger
        self.selection_history: list[GrahamSelection] = []

    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
    ) -> dict[str, float]:
        logger.info(
            f"{as_of}: starting Graham selection over {len(universe)} candidates"
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

        # ---- Stage 1: classic Net-Net ---------------------------------
        net_net_survivors = filter_candidates(
            triples,
            as_of=as_of,
            max_p_ncav=self.max_p_ncav,
            max_de=self.max_de,
            min_market_cap=self.min_market_cap,
        )
        net_net_scores = score_candidates(net_net_survivors)
        net_net_top: list[GrahamScore] = select_top_n(
            net_net_scores, n=self.portfolio_size
        )

        # ---- Stage 2: Defensive Investor fallback ---------------------
        # If we don't have at least the threshold count of Net-Nets, fill
        # the remainder with Defensive Investor picks. This is the
        # post-1970 reality — classic Net-Nets are extremely rare.
        defensive_top: list[DefensiveScore] = []
        defensive_survivors_count = 0
        if (
            self.enable_defensive_fallback
            and len(net_net_top) < self.net_net_fallback_threshold
            and len(net_net_top) < self.portfolio_size
        ):
            slots_to_fill = self.portfolio_size - len(net_net_top)
            net_net_tickers = {s.ticker for s in net_net_top}
            # Exclude tickers already chosen by Net-Net so we don't double-count
            defensive_pool = [
                (fin, mcap, price)
                for (fin, mcap, price) in triples
                if fin is not None and fin.ticker not in net_net_tickers
            ]
            defensive_survivors = filter_defensive_candidates(
                defensive_pool,
                as_of=as_of,
                max_pe=self.defensive_max_pe,
                max_pb=self.defensive_max_pb,
                min_current_ratio=self.defensive_min_current_ratio,
                max_de=self.max_de,
                min_market_cap=self.min_market_cap,
            )
            defensive_survivors_count = len(defensive_survivors)
            defensive_scores = score_defensive_candidates(defensive_survivors)
            defensive_top = select_top_n(defensive_scores, n=slots_to_fill)

        all_selected_tickers: list[str] = [s.ticker for s in net_net_top] + [
            s.ticker for s in defensive_top
        ]

        if not all_selected_tickers:
            logger.warning(
                f"{as_of}: no Graham candidates qualified — staying in cash"
            )
            self._record(
                as_of=as_of,
                universe_size=len(universe),
                with_data=with_data,
                survivors=len(net_net_survivors),
                defensive_candidates=defensive_survivors_count,
                net_net_count=0,
                defensive_count=0,
                selected=[],
                top=[],
                top_defensive=[],
            )
            return {}

        weight = 1.0 / len(all_selected_tickers)
        weights = {t: weight for t in all_selected_tickers}
        self._record(
            as_of=as_of,
            universe_size=len(universe),
            with_data=with_data,
            survivors=len(net_net_survivors),
            defensive_candidates=defensive_survivors_count,
            net_net_count=len(net_net_top),
            defensive_count=len(defensive_top),
            selected=all_selected_tickers,
            top=net_net_top,
            top_defensive=defensive_top,
        )
        if self.decision_logger is not None:
            self._log_decisions(
                as_of, net_net_top, defensive_top, net_net_scores, prices
            )
        mode_summary = (
            f"Net-Net={len(net_net_top)}, Defensive={len(defensive_top)}"
        )
        logger.info(f"{as_of}: selected {len(all_selected_tickers)} stocks ({mode_summary})")
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
        *,
        as_of: date,
        universe_size: int,
        with_data: int,
        survivors: int,
        defensive_candidates: int,
        net_net_count: int,
        defensive_count: int,
        selected: list[str],
        top: list[GrahamScore],
        top_defensive: list[DefensiveScore],
    ) -> None:
        self.selection_history.append(
            GrahamSelection(
                as_of=as_of,
                universe_size=universe_size,
                candidates_with_data=with_data,
                candidates_after_filters=survivors,
                defensive_candidates=defensive_candidates,
                net_net_count=net_net_count,
                defensive_count=defensive_count,
                selected_tickers=selected,
                top_scores=top,
                top_defensive=top_defensive,
            )
        )

    def _log_decisions(
        self,
        as_of: date,
        net_net_top: list[GrahamScore],
        defensive_top: list[DefensiveScore],
        all_net_net_scores: list[GrahamScore],
        prices: PriceLookup,
    ) -> None:
        timestamp = f"{as_of.isoformat()}T00:00:00+00:00"
        selected_net_net = {s.ticker for s in net_net_top}

        # Net-Net BUYs
        for s in net_net_top:
            self._safe_log_net_net(s, "BUY", timestamp)

        # Defensive Investor BUYs
        for d in defensive_top:
            self._safe_log_defensive(d, "BUY", timestamp)

        # Net-Net runner-ups → WATCH (capped to portfolio_size)
        watch = [s for s in all_net_net_scores if s.ticker not in selected_net_net][
            : self.portfolio_size
        ]
        for s in watch:
            self._safe_log_net_net(s, "WATCH", timestamp)

    def _safe_log_net_net(
        self, s: GrahamScore, decision_type: str, timestamp: str
    ) -> None:
        try:
            self.decision_logger.log(
                make_decision(
                    ticker=s.ticker,
                    decision=decision_type,
                    agent=self.name,
                    timestamp=timestamp,
                    criteria_met=[
                        f"P/NCAV={s.p_ncav:.3f} (≤ {self.max_p_ncav:.3f})",
                        f"D/E={s.debt_to_equity:.2f} (≤ {self.max_de})",
                        "positive trailing net income",
                        "mode=NET_NET",
                    ],
                    criteria_values={
                        "mode": "NET_NET",
                        "p_ncav": round(s.p_ncav, 4),
                        "ncav_per_share": s.ncav_per_share,
                        "debt_to_equity": round(s.debt_to_equity, 4),
                        "net_income": s.net_income,
                        "market_cap": s.market_cap,
                        "price": s.price,
                    },
                    confidence=max(0.0, min(1.0, 1.0 - s.p_ncav)),
                    entry_price=s.price,
                    target_price=s.ncav_per_share,
                    exit_trigger="P/NCAV reverts toward 1.0× OR ~50% gain",
                    rationale=(
                        f"Graham Net-Net: P/NCAV {s.p_ncav:.3f}, "
                        f"NCAV/share {s.ncav_per_share:.2f}"
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"decision log failed for {s.ticker}: {exc}")

    def _safe_log_defensive(
        self, s: DefensiveScore, decision_type: str, timestamp: str
    ) -> None:
        try:
            self.decision_logger.log(
                make_decision(
                    ticker=s.ticker,
                    decision=decision_type,
                    agent=self.name,
                    timestamp=timestamp,
                    criteria_met=[
                        f"P/E={s.pe:.2f} (≤ {self.defensive_max_pe})",
                        f"P/B={s.pb:.2f} (≤ {self.defensive_max_pb})",
                        f"current ratio={s.current_ratio:.2f} "
                        f"(≥ {self.defensive_min_current_ratio})",
                        f"D/E={s.debt_to_equity:.2f} (≤ {self.max_de})",
                        "positive trailing net income",
                        "mode=DEFENSIVE",
                    ],
                    criteria_values={
                        "mode": "DEFENSIVE",
                        "pe": round(s.pe, 4),
                        "pb": round(s.pb, 4),
                        "current_ratio": round(s.current_ratio, 4),
                        "graham_number": round(s.composite, 4),
                        "debt_to_equity": round(s.debt_to_equity, 4),
                        "net_income": s.net_income,
                        "market_cap": s.market_cap,
                        "price": s.price,
                    },
                    # Confidence: how far below the Graham 22.5 ceiling we are
                    confidence=max(0.0, min(1.0, 1.0 - (s.composite / 22.5))),
                    entry_price=s.price,
                    target_price=None,
                    exit_trigger=(
                        "P/E or P/B revert above thresholds; "
                        "OR fundamentals deteriorate"
                    ),
                    rationale=(
                        f"Graham Defensive: P/E {s.pe:.2f}, P/B {s.pb:.2f}, "
                        f"CR {s.current_ratio:.2f}, "
                        f"Graham # {s.composite:.2f}"
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"decision log failed for {s.ticker}: {exc}")

    def selections_to_records(self) -> list[dict[str, Any]]:
        return [
            {
                "as_of": sel.as_of.isoformat(),
                "universe_size": sel.universe_size,
                "candidates_with_data": sel.candidates_with_data,
                "candidates_after_filters": sel.candidates_after_filters,
                "defensive_candidates": sel.defensive_candidates,
                "net_net_count": sel.net_net_count,
                "defensive_count": sel.defensive_count,
                "selected_tickers": list(sel.selected_tickers),
                "top_p_ncav": sel.top_scores[0].p_ncav if sel.top_scores else None,
                "median_p_ncav": (
                    sorted(s.p_ncav for s in sel.top_scores)[len(sel.top_scores) // 2]
                    if sel.top_scores
                    else None
                ),
                "top_graham_number": (
                    sel.top_defensive[0].composite if sel.top_defensive else None
                ),
                "median_graham_number": (
                    sorted(s.composite for s in sel.top_defensive)[
                        len(sel.top_defensive) // 2
                    ]
                    if sel.top_defensive
                    else None
                ),
            }
            for sel in self.selection_history
        ]


__all__ = ["BenjaminGraham", "GrahamSelection"]
