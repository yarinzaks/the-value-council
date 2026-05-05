"""Greenblatt's Magic Formula — first quantitative agent of The Value Council.

Public API::

    from agents.greenblatt import MagicFormula

    strategy = MagicFormula(portfolio_size=30)
    runner = BacktestRunner(config)
    result = runner.run(strategy)

The implementation tracks the playbook in ``playbook.md`` precisely;
see ``README.md`` for the documented design choices and trade-offs.
"""

from .filters import (
    DEFAULT_MIN_MARKET_CAP_USD,
    EXCLUDED_SIC_RANGES,
    FilterResult,
    filter_candidates,
    is_excluded_sector,
    passes_filters,
)
from .magic_formula import MagicFormula, MagicFormulaSelection
from .ranking import (
    MagicFormulaScore,
    compute_earnings_yield,
    compute_enterprise_value,
    compute_invested_capital,
    compute_return_on_capital,
    score_candidates,
    select_top_n,
)

__all__ = [
    "DEFAULT_MIN_MARKET_CAP_USD",
    "EXCLUDED_SIC_RANGES",
    "FilterResult",
    "MagicFormula",
    "MagicFormulaScore",
    "MagicFormulaSelection",
    "compute_earnings_yield",
    "compute_enterprise_value",
    "compute_invested_capital",
    "compute_return_on_capital",
    "filter_candidates",
    "is_excluded_sector",
    "passes_filters",
    "score_candidates",
    "select_top_n",
]
