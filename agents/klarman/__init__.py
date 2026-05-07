"""Seth Klarman agent — risk-first MoS with LLM downside analysis.

Public surface::

    from agents.klarman import (
        SethKlarman,                    # main Strategy
        KlarmanScore, KlarmanSelection, # data records
        KlarmanMemo, DownsideAnalyzer,  # LLM layer
        score_candidates, select_top_n, # ranking primitives
        intrinsic_value,                # conservative DCF
        passes_quality_gates,           # filters
    )

Backtest entrypoint::

    .venv/bin/python -m agents.klarman.run_full_market_validation
"""

from __future__ import annotations

from agents.klarman.downside import (
    AssetType,
    CapitalStructurePosition,
    Catalyst,
    CatalystStrength,
    DiversificationDimension,
    DownsideAnalyzer,
    FailureScenario,
    KlarmanDecision,
    KlarmanMemo,
    ScalingOutPlan,
    ValuationMethod,
)
from agents.klarman.filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    FilterResult,
    apply_quality_gates,
    debt_to_equity,
    passes_quality_gates,
)
from agents.klarman.margin_of_safety import (
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_MAX_PORTFOLIO_SIZE,
    DeploymentDecision,
    KlarmanSelection,
    SethKlarman,
    _deployment_for,
)
from agents.klarman.ranking import (
    KlarmanScore,
    score_candidates,
    select_top_n,
)
from agents.klarman.valuation import (
    DEFAULT_DCF_YEARS,
    DEFAULT_DISCOUNT_RATE_PCT,
    DEFAULT_FCF_AVG_YEARS,
    DEFAULT_MAX_GROWTH_PCT,
    DEFAULT_MIN_MARGIN_OF_SAFETY_PCT,
    DEFAULT_TERMINAL_MULTIPLE,
    FreeCashFlowRecord,
    IntrinsicValueResult,
    historical_fcf,
    intrinsic_value,
    margin_of_safety_pct,
)

__all__ = [
    "AssetType",
    "CapitalStructurePosition",
    "Catalyst",
    "CatalystStrength",
    "DEFAULT_DCF_YEARS",
    "DEFAULT_DISCOUNT_RATE_PCT",
    "DEFAULT_FCF_AVG_YEARS",
    "DEFAULT_MAX_DE",
    "DEFAULT_MAX_GROWTH_PCT",
    "DEFAULT_MAX_POSITION_PCT",
    "DEFAULT_MAX_PORTFOLIO_SIZE",
    "DEFAULT_MIN_MARGIN_OF_SAFETY_PCT",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_TERMINAL_MULTIPLE",
    "DeploymentDecision",
    "DiversificationDimension",
    "DownsideAnalyzer",
    "FailureScenario",
    "FilterResult",
    "FreeCashFlowRecord",
    "IntrinsicValueResult",
    "KlarmanDecision",
    "KlarmanMemo",
    "KlarmanScore",
    "KlarmanSelection",
    "ScalingOutPlan",
    "SethKlarman",
    "ValuationMethod",
    "_deployment_for",
    "apply_quality_gates",
    "debt_to_equity",
    "historical_fcf",
    "intrinsic_value",
    "margin_of_safety_pct",
    "passes_quality_gates",
    "score_candidates",
    "select_top_n",
]
