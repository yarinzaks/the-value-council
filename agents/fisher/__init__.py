"""Philip Fisher agent — quality growth + 15-point + scuttlebutt.

Public surface::

    from agents.fisher import (
        PhilipFisher,                       # main Strategy
        FisherScore, FisherSelection,       # data records
        FisherMemo, ScuttlebuttAnalyzer,    # LLM layer
        score_candidates, select_top_n,     # ranking primitives
        score_quality, QualityScore,        # quant 5-point check
        passes_quality_gates,               # filters
    )

Backtest entrypoint::

    .venv/bin/python -m agents.fisher.run_full_market_validation
"""

from __future__ import annotations

from agents.fisher.filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    FilterResult,
    apply_quality_gates,
    debt_to_equity,
    passes_quality_gates,
)
from agents.fisher.quality_growth import (
    DEFAULT_MAX_PORTFOLIO_SIZE,
    FisherSelection,
    PhilipFisher,
)
from agents.fisher.quality_score import (
    DEFAULT_MARGIN_TREND_FLOOR_BPS,
    DEFAULT_MAX_SHARE_DILUTION_PCT_5YR,
    DEFAULT_MIN_OPERATING_MARGIN_PCT,
    DEFAULT_MIN_RD_TO_REVENUE_PCT,
    DEFAULT_MIN_REVENUE_CAGR_PCT,
    QualityScore,
    margin_trend_5yr_bps,
    operating_margin_pct,
    rd_to_revenue_pct,
    revenue_cagr_5yr_pct,
    score_quality,
    share_count_change_5yr_pct,
)
from agents.fisher.ranking import (
    TIER_A_MAX_PE,
    TIER_A_POSITION_PCT,
    TIER_B_MAX_PE,
    TIER_B_POSITION_PCT,
    FisherScore,
    Tier,
    score_candidates,
    select_top_n,
)
from agents.fisher.scuttlebutt import (
    FifteenPointsScore,
    FisherDecision,
    FisherMemo,
    HoldingPeriod,
    PatentStrength,
    PointVerdict,
    ScuttlebuttAnalyzer,
    ScuttlebuttAssessment,
    ScuttlebuttResearch,
    TierClassification,
    ValuationAssessment,
)

__all__ = [
    "DEFAULT_MARGIN_TREND_FLOOR_BPS",
    "DEFAULT_MAX_DE",
    "DEFAULT_MAX_PORTFOLIO_SIZE",
    "DEFAULT_MAX_SHARE_DILUTION_PCT_5YR",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_MIN_OPERATING_MARGIN_PCT",
    "DEFAULT_MIN_RD_TO_REVENUE_PCT",
    "DEFAULT_MIN_REVENUE_CAGR_PCT",
    "TIER_A_MAX_PE",
    "TIER_A_POSITION_PCT",
    "TIER_B_MAX_PE",
    "TIER_B_POSITION_PCT",
    "FifteenPointsScore",
    "FilterResult",
    "FisherDecision",
    "FisherMemo",
    "FisherScore",
    "FisherSelection",
    "HoldingPeriod",
    "PatentStrength",
    "PhilipFisher",
    "PointVerdict",
    "QualityScore",
    "ScuttlebuttAnalyzer",
    "ScuttlebuttAssessment",
    "ScuttlebuttResearch",
    "Tier",
    "TierClassification",
    "ValuationAssessment",
    "apply_quality_gates",
    "debt_to_equity",
    "margin_trend_5yr_bps",
    "operating_margin_pct",
    "passes_quality_gates",
    "rd_to_revenue_pct",
    "revenue_cagr_5yr_pct",
    "score_candidates",
    "score_quality",
    "select_top_n",
    "share_count_change_5yr_pct",
]
