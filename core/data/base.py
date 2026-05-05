"""Abstract base class and shared helpers for data sources.

Each concrete source (FMP, Finnhub, ...) inherits from :class:`DataSource`
and implements the abstract ``get_quote`` and ``get_fundamentals``. Sources
may add their own methods for source-specific data (e.g., EDGAR filings,
news with sentiment).

Network calls are wrapped with a tenacity retry: 3 attempts, exponential
backoff, retries on connection errors and explicit :class:`RateLimitError`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import requests
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.exceptions import DataSourceError, RateLimitError
from core.logger import get_logger

from .models import Fundamentals, Quote


def _log_retry(retry_state: RetryCallState) -> None:
    logger = get_logger("core.data.base")
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        f"Retry {retry_state.attempt_number}/3 after {type(exc).__name__}: {exc}"
    )


retry_network = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((requests.RequestException, RateLimitError)),
    before_sleep=_log_retry,
    reraise=True,
)
"""Decorator: retry up to 3 times on network errors with exponential backoff."""


class DataSource(ABC):
    """Abstract base for all data source clients.

    Subclasses must declare a ``name`` class attribute and implement
    :meth:`get_quote` and :meth:`get_fundamentals`. Source-specific
    methods (filings, news, etc.) are added on subclasses.
    """

    name: str = "base"

    def __init__(self) -> None:
        self.logger = get_logger(f"core.data.{self.name}")

    # --- Public API ----------------------------------------------------------
    @abstractmethod
    def get_quote(self, ticker: str) -> Quote:
        """Return the latest quote for ``ticker``.

        Raises:
            DataSourceError: When the source cannot return data.
        """

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> Fundamentals:
        """Return current fundamental metrics for ``ticker``.

        Raises:
            DataSourceError: When the source cannot return data.
        """

    # --- Helpers -------------------------------------------------------------
    def _log_call(self, method: str, ticker: str, **extra: Any) -> None:
        """Emit a debug log line for an outgoing API call."""
        if extra:
            self.logger.debug(f"{method}({ticker!r}) extra={extra}")
        else:
            self.logger.debug(f"{method}({ticker!r})")

    def _check_response(self, response: requests.Response, ticker: str) -> None:
        """Translate HTTP errors into our exception hierarchy."""
        if response.status_code == 429:
            raise RateLimitError(self.name, f"rate limited on {ticker}")
        if response.status_code == 401 or response.status_code == 403:
            raise DataSourceError(
                self.name,
                f"auth failed ({response.status_code}) — check API key",
            )
        if not response.ok:
            raise DataSourceError(
                self.name,
                f"HTTP {response.status_code} for {ticker}: {response.text[:200]}",
            )


__all__ = ["DataSource", "retry_network"]
