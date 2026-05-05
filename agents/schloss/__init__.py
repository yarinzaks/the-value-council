"""Walter Schloss — diversified deep-value agent."""

from .deep_value import SchlossSelection, WalterSchloss
from .filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MAX_PB,
    DEFAULT_MIN_MARKET_CAP_USD,
    DEFAULT_MIN_YEARS_PUBLIC,
    FilterResult,
    book_value_per_share,
    debt_to_equity,
    filter_candidates,
    passes_filters,
    price_to_book,
    years_public,
)
from .ranking import SchlossScore, score_candidates, select_top_n

__all__ = [
    "DEFAULT_MAX_DE",
    "DEFAULT_MAX_PB",
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_MIN_YEARS_PUBLIC",
    "FilterResult",
    "SchlossScore",
    "SchlossSelection",
    "WalterSchloss",
    "book_value_per_share",
    "debt_to_equity",
    "filter_candidates",
    "passes_filters",
    "price_to_book",
    "score_candidates",
    "select_top_n",
    "years_public",
]
