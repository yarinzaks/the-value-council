"""Warren Buffett agent — quality + moats with LLM moat verification.

Public surface:

    from agents.buffett import (
        WarrenBuffett,                  # main Strategy
        BuffettScore, BuffettSelection, # data records
        BuffettMemo, MoatAnalyzer,      # LLM moat layer
        score_candidates, select_top_n, # ranking primitives
        intrinsic_value,                # DCF
        passes_quality_gates,           # filters
    )

Backtest entrypoint::

    .venv/bin/python -m agents.buffett.run_full_market_validation

Live mode wires :class:`WarrenBuffett` into ``core.live.runner`` with
a real :class:`MoatAnalyzer` so the LLM moat verification runs.
"""

from __future__ import annotations

from agents.buffett.filters import (
    DEFAULT_EARNINGS_HISTORY_YEARS,
    DEFAULT_MAX_DE,
    DEFAULT_MIN_AVG_ROE_PCT,
    DEFAULT_MIN_MARKET_CAP_USD,
    DEFAULT_OCF_HISTORY_YEARS,
    DEFAULT_ROE_AVG_YEARS,
    EXCLUDED_SIC2,
    FilterResult,
    apply_quality_gates,
    avg_roe_5yr,
    has_consistent_earnings,
    has_consistent_ocf,
    is_simple_business,
    passes_quality_gates,
)
from agents.buffett.moat_analyzer import (
    BuffettDecision,
    BuffettMemo,
    CrossReferenceSignals,
    MoatAnalyzer,
    MoatType,
)
from agents.buffett.owner_earnings import (
    DEFAULT_DCF_YEARS,
    DEFAULT_DISCOUNT_RATE_PCT,
    DEFAULT_OE_AVG_YEARS,
    DEFAULT_TERMINAL_MULTIPLE,
    IntrinsicValueResult,
    OwnerEarningsRecord,
    historical_owner_earnings,
    intrinsic_value,
    margin_of_safety_pct,
)
from agents.buffett.ranking import (
    DEFAULT_MIN_MARGIN_OF_SAFETY_PCT,
    BuffettScore,
    score_candidates,
    select_top_n,
)
from agents.buffett.wonderful_business import (
    DEFAULT_PORTFOLIO_SIZE,
    BuffettSelection,
    WarrenBuffett,
)

__all__ = [
    "DEFAULT_DCF_YEARS",
    "DEFAULT_DISCOUNT_RATE_PCT",
    "DEFAULT_EARNINGS_HISTORY_YEARS",
    "DEFAULT_MAX_DE",
    "DEFAULT_MIN_AVG_ROE_PCT",
    "DEFAULT_MIN_MARGIN_OF_SAFETY_PCT",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_OCF_HISTORY_YEARS",
    "DEFAULT_OE_AVG_YEARS",
    "DEFAULT_PORTFOLIO_SIZE",
    "DEFAULT_ROE_AVG_YEARS",
    "DEFAULT_TERMINAL_MULTIPLE",
    "EXCLUDED_SIC2",
    "BuffettDecision",
    "BuffettMemo",
    "BuffettScore",
    "BuffettSelection",
    "CrossReferenceSignals",
    "FilterResult",
    "IntrinsicValueResult",
    "MoatAnalyzer",
    "MoatType",
    "OwnerEarningsRecord",
    "WarrenBuffett",
    "apply_quality_gates",
    "avg_roe_5yr",
    "has_consistent_earnings",
    "has_consistent_ocf",
    "historical_owner_earnings",
    "intrinsic_value",
    "is_simple_business",
    "margin_of_safety_pct",
    "passes_quality_gates",
    "score_candidates",
    "select_top_n",
]
