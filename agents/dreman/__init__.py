"""David Dreman 4-metric contrarian agent."""

from agents.dreman.contrarian import DavidDreman, DremanSelection
from agents.dreman.filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    DEFAULT_MIN_QUALIFYING_METRICS,
    DEFAULT_QUINTILE,
    apply_quality_gates,
    debt_to_equity,
    dividend_yield,
    passes_quality_gates,
    pb_ratio,
    pcf_ratio,
    pe_ratio,
    quintile_thresholds,
)
from agents.dreman.ranking import DremanScore, score_candidates, select_top_n

__all__ = [
    "DEFAULT_MAX_DE",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_MIN_QUALIFYING_METRICS",
    "DEFAULT_QUINTILE",
    "DavidDreman",
    "DremanScore",
    "DremanSelection",
    "apply_quality_gates",
    "debt_to_equity",
    "dividend_yield",
    "passes_quality_gates",
    "pb_ratio",
    "pcf_ratio",
    "pe_ratio",
    "quintile_thresholds",
    "score_candidates",
    "select_top_n",
]
