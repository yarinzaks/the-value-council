"""Prompt templates for the Gemini LLM.

The investment memo prompt is the workhorse: it takes a playbook
(role description + methodology), a stock data dump, and the agent's
current portfolio state, and asks the model to produce a structured
JSON memo.

Prompts are kept here (not in code) so they can be tuned without code
changes and reviewed by non-engineers.
"""

from __future__ import annotations

INVESTMENT_MEMO_SCHEMA: dict[str, str] = {
    "decision": "BUY | SELL | HOLD | PASS",
    "confidence": "float 0.0-1.0",
    "thesis": "1-3 paragraph investment thesis written in the investor's voice",
    "key_metrics_passed": "list[str] — playbook criteria that passed",
    "concerns": "list[str] — playbook criteria that failed or are uncertain",
    "position_size_pct": "float 0.0-100.0 — % of portfolio to allocate (0 if not BUY)",
    "expected_holding": "string — e.g., '6-12 months', '3-5 years', 'multi-decade'",
    "exit_triggers": "list[str] — conditions that would cause a SELL decision",
}
"""The expected JSON schema for an investment memo. Documented for prompt
context and used by the InvestmentMemo Pydantic model in gemini_client."""


INVESTMENT_MEMO_SYSTEM: str = """\
You are role-playing as a legendary value investor. Your task is to evaluate \
a single stock against the investor's documented methodology and produce a \
structured investment memo as JSON.

Adhere strictly to the playbook. Reject opportunities that fail any \
non-negotiable criterion, even if other metrics look attractive. Speak in \
the voice of the investor — terse, principled, evidence-based. Never \
invent data: if a metric is missing from the input, state that and \
factor the missing data into your confidence score.

Output ONLY a valid JSON object matching the schema given in the user \
message. No prose before or after, no markdown fences. Every field is \
mandatory.
"""


INVESTMENT_MEMO_USER_TEMPLATE: str = """\
# Playbook (your methodology)

{playbook}

---

# Stock data

{stock_data}

---

# Current portfolio state

{portfolio_state}

---

# Required output schema

Return EXACTLY a JSON object with these keys:

- decision (string, one of: "BUY", "SELL", "HOLD", "PASS")
- confidence (number, 0.0 to 1.0)
- thesis (string, 1-3 paragraphs)
- key_metrics_passed (array of strings)
- concerns (array of strings)
- position_size_pct (number, 0.0 to 100.0)
- expected_holding (string)
- exit_triggers (array of strings)

Begin.
"""


def render_investment_memo_prompt(
    playbook: str, stock_data: str, portfolio_state: str
) -> str:
    """Render the user-facing portion of the investment memo prompt."""
    return INVESTMENT_MEMO_USER_TEMPLATE.format(
        playbook=playbook,
        stock_data=stock_data,
        portfolio_state=portfolio_state,
    )


__all__ = [
    "INVESTMENT_MEMO_SCHEMA",
    "INVESTMENT_MEMO_SYSTEM",
    "INVESTMENT_MEMO_USER_TEMPLATE",
    "render_investment_memo_prompt",
]
