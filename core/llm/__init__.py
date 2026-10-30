"""LLM layer — Claude client and prompt templates."""

from .claude_client import ClaudeClient, InvestmentMemo
from .prompts import (
    INVESTMENT_MEMO_SCHEMA,
    INVESTMENT_MEMO_SYSTEM,
    INVESTMENT_MEMO_USER_TEMPLATE,
)

__all__ = [
    "INVESTMENT_MEMO_SCHEMA",
    "INVESTMENT_MEMO_SYSTEM",
    "INVESTMENT_MEMO_USER_TEMPLATE",
    "ClaudeClient",
    "InvestmentMemo",
]
