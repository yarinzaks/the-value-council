"""Abstract base for every value-investor agent.

Concrete agents (Graham, Buffett, Lynch, ...) inherit from this class
in later sessions. The shape is stable — the runtime expects every
agent to expose ``name``, ``playbook_path``, and a ``run`` method.

This file is intentionally thin: the discriminating logic lives in
each agent's playbook and screener rules, not in this base class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from core.data.unified_fetcher import UnifiedFetcher
from core.llm.gemini_client import GeminiClient
from core.logger import get_logger
from core.portfolio import Portfolio


class Agent(ABC):
    """Base class for a value-investor paper-trading agent.

    Subclasses must set the class attribute ``name`` (matches the
    folder under ``agents/``) and implement :meth:`run` — typically
    this means: screen the universe, enrich top candidates, ask the
    LLM for memos, and execute trades through :class:`Portfolio`.
    """

    name: str = "base"

    def __init__(
        self,
        fetcher: UnifiedFetcher | None = None,
        llm: GeminiClient | None = None,
    ) -> None:
        self.logger = get_logger(f"agents.{self.name}")
        self.fetcher = fetcher or UnifiedFetcher()
        self.llm = llm or GeminiClient()
        self.portfolio = Portfolio.load(self.name)

    @property
    def playbook_path(self) -> Path:
        """Filesystem path to this agent's playbook markdown."""
        return Path(__file__).resolve().parent / self.name / "playbook.md"

    @property
    def playbook(self) -> str:
        """Read the playbook markdown."""
        return self.playbook_path.read_text(encoding="utf-8")

    @abstractmethod
    def run(self) -> dict[str, Any]:
        """Execute one full evaluation cycle.

        Implementations vary by playbook but should:
            1. Build or load the screening universe.
            2. Apply playbook-specific screen rules.
            3. Enrich top N candidates via the fetcher.
            4. Ask the LLM for an investment memo per candidate.
            5. Apply BUY/SELL decisions to the portfolio.
            6. Persist portfolio and return a summary dict.
        """


__all__ = ["Agent"]
