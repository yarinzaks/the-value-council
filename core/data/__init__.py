"""Unified data layer.

Re-exports the core models and the abstract base so callers can import
everything they need from ``core.data``::

    from core.data import StockSnapshot, UnifiedFetcher
"""

from .base import DataSource, retry_network
from .models import (
    FilingExcerpt,
    Fundamentals,
    InsiderTransaction,
    NewsItem,
    Quote,
    StockSnapshot,
)

__all__ = [
    "DataSource",
    "FilingExcerpt",
    "Fundamentals",
    "InsiderTransaction",
    "NewsItem",
    "Quote",
    "StockSnapshot",
    "retry_network",
]
