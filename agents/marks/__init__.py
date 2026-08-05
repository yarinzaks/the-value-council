"""Howard Marks agent — cycle-aware value with LLM second-level analysis.

Public surface::

    from agents.marks import (
        HowardMarks,                     # main Strategy
        MarksScore, MarksSelection,      # data records
        MarksMemo, SecondLevelAnalyzer,  # LLM layer
        score_candidates, select_top_n,  # ranking primitives
        assess_market_temperature,       # cycle-positioning
        TemperatureAssessment, Posture,
        passes_quality_gates,            # filters
    )

Backtest entrypoint::

    .venv/bin/python -m agents.marks.run_full_market_validation
"""

from __future__ import annotations

from agents.marks.cycle_value import HowardMarks, MarksSelection
from agents.marks.filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    FilterResult,
    apply_quality_gates,
    debt_to_equity,
    passes_quality_gates,
)
from agents.marks.ranking import (
    MarksScore,
    score_candidates,
    select_top_n,
)
from agents.marks.second_level import (
    AssetType,
    CyclePhase,
    IDontKnowCheck,
    MarketTemperatureScore,
    MarksDecision,
    MarksMemo,
    PostureRecommendation,
    RiskAdjustedReturnAnalysis,
    ScalingOutPlan,
    Scenario,
    SecondLevelAnalyzer,
    SecondLevelThinking,
)
from agents.marks.temperature import (
    Posture,
    PostureProfile,
    TemperatureAssessment,
    TemperatureSignals,
    assess_market_temperature,
    profile_for,
)

__all__ = [
    "DEFAULT_MAX_DE",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "AssetType",
    "CyclePhase",
    "FilterResult",
    "HowardMarks",
    "IDontKnowCheck",
    "MarketTemperatureScore",
    "MarksDecision",
    "MarksMemo",
    "MarksScore",
    "MarksSelection",
    "Posture",
    "PostureProfile",
    "PostureRecommendation",
    "RiskAdjustedReturnAnalysis",
    "ScalingOutPlan",
    "Scenario",
    "SecondLevelAnalyzer",
    "SecondLevelThinking",
    "TemperatureAssessment",
    "TemperatureSignals",
    "apply_quality_gates",
    "assess_market_temperature",
    "debt_to_equity",
    "passes_quality_gates",
    "profile_for",
    "score_candidates",
    "select_top_n",
]
