"""LLM layer — Gemini client and prompt templates."""

from .gemini_client import GeminiClient, InvestmentMemo
from .prompts import (
    INVESTMENT_MEMO_SCHEMA,
    INVESTMENT_MEMO_SYSTEM,
    INVESTMENT_MEMO_USER_TEMPLATE,
)

__all__ = [
    "GeminiClient",
    "InvestmentMemo",
    "INVESTMENT_MEMO_SCHEMA",
    "INVESTMENT_MEMO_SYSTEM",
    "INVESTMENT_MEMO_USER_TEMPLATE",
]
