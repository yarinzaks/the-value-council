"""Custom exception hierarchy for The Value Council.

All exceptions inherit from :class:`ValueCouncilError` so callers can
catch a single base type when they need to.
"""

from __future__ import annotations


class ValueCouncilError(Exception):
    """Base class for all Value Council exceptions."""


class ConfigError(ValueCouncilError):
    """Raised when required configuration is missing or invalid."""


class DataSourceError(ValueCouncilError):
    """Raised when a data source fails to return usable data."""

    def __init__(self, source: str, message: str) -> None:
        self.source = source
        super().__init__(f"[{source}] {message}")


class RateLimitError(DataSourceError):
    """Raised when a data source signals we have been rate-limited."""


class LLMError(ValueCouncilError):
    """Raised when the LLM call fails or returns an unparseable response."""


class PortfolioError(ValueCouncilError):
    """Raised on illegal portfolio operations (oversell, negative cash, ...)."""
