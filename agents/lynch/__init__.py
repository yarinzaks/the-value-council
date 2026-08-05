"""Peter Lynch agent — GARP with PEG ranking + LLM category classification.

Public surface::

    from agents.lynch import (
        PeterLynch,                       # main Strategy
        LynchScore, LynchSelection,       # data records
        LynchMemo, CategoryClassifier,    # LLM layer
        score_candidates, select_top_n,   # ranking primitives
        peg_for, trailing_eps_cagr_pct,   # PEG math
        passes_quality_gates,             # filters
        heuristic_classify,               # quant category classifier
    )

Backtest entrypoint::

    .venv/bin/python -m agents.lynch.run_full_market_validation
"""

from __future__ import annotations

from agents.lynch.category_classifier import (
    FAST_GROWER_MAX_GROWTH_PCT,
    FAST_GROWER_MIN_GROWTH_PCT,
    SLOW_GROWER_MAX_GROWTH_PCT,
    SLOW_GROWER_MIN_YIELD_PCT,
    STALWART_MAX_GROWTH_PCT,
    STALWART_MIN_GROWTH_PCT,
    STALWART_MIN_MARKET_CAP_USD,
    AssetPlayData,
    CategoryClassifier,
    CategorySpecificData,
    CyclicalData,
    FastGrowerData,
    FundamentalsCheck,
    LynchCategory,
    LynchDecision,
    LynchMemo,
    TurnaroundData,
    heuristic_classify,
)
from agents.lynch.filters import (
    DEFAULT_EARNINGS_HISTORY_YEARS,
    DEFAULT_EARNINGS_MIN_POSITIVE_YEARS,
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    FilterResult,
    apply_quality_gates,
    debt_to_equity,
    dividend_yield_pct,
    has_consistent_earnings,
    latest_free_cash_flow,
    passes_quality_gates,
)
from agents.lynch.garp import (
    DEFAULT_PORTFOLIO_SIZE,
    LynchSelection,
    PeterLynch,
)
from agents.lynch.peg import (
    PEG_BUY,
    PEG_FLOOR,
    PEG_HOLD,
    PEG_STRONG_BUY,
    PegResult,
    acceleration_pct,
    peg_buy_zone,
    peg_for,
    peg_ratio,
    pegy_ratio,
    trailing_eps_cagr_pct,
)
from agents.lynch.ranking import (
    FAST_GROWER_MAX_PEG,
    SLOW_GROWER_MAX_PEG,
    STALWART_MAX_PEG,
    UNIVERSAL_MAX_PEG,
    LynchScore,
    score_candidates,
    select_top_n,
)

__all__ = [
    "DEFAULT_EARNINGS_HISTORY_YEARS",
    "DEFAULT_EARNINGS_MIN_POSITIVE_YEARS",
    "DEFAULT_MAX_DE",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_PORTFOLIO_SIZE",
    "FAST_GROWER_MAX_GROWTH_PCT",
    "FAST_GROWER_MAX_PEG",
    "FAST_GROWER_MIN_GROWTH_PCT",
    "PEG_BUY",
    "PEG_FLOOR",
    "PEG_HOLD",
    "PEG_STRONG_BUY",
    "SLOW_GROWER_MAX_GROWTH_PCT",
    "SLOW_GROWER_MAX_PEG",
    "SLOW_GROWER_MIN_YIELD_PCT",
    "STALWART_MAX_GROWTH_PCT",
    "STALWART_MAX_PEG",
    "STALWART_MIN_GROWTH_PCT",
    "STALWART_MIN_MARKET_CAP_USD",
    "UNIVERSAL_MAX_PEG",
    "AssetPlayData",
    "CategoryClassifier",
    "CategorySpecificData",
    "CyclicalData",
    "FastGrowerData",
    "FilterResult",
    "FundamentalsCheck",
    "LynchCategory",
    "LynchDecision",
    "LynchMemo",
    "LynchScore",
    "LynchSelection",
    "PegResult",
    "PeterLynch",
    "TurnaroundData",
    "acceleration_pct",
    "apply_quality_gates",
    "debt_to_equity",
    "dividend_yield_pct",
    "has_consistent_earnings",
    "heuristic_classify",
    "latest_free_cash_flow",
    "passes_quality_gates",
    "peg_buy_zone",
    "peg_for",
    "peg_ratio",
    "pegy_ratio",
    "score_candidates",
    "select_top_n",
    "trailing_eps_cagr_pct",
]
