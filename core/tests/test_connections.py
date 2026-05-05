"""Sanity-check every external API.

Run from the project root::

    python -m core.tests.test_connections

Each provider is hit with a single, low-cost call. The script prints a
green check or red cross per source and exits 0 on full success, 1 on
any failure, 2 if ``.env`` is missing.

This is *not* a pytest test — it talks to live services and is meant to
be run by humans (or CI with secrets). Pytest skips ``test_connections``
because of the ``main()`` guard at module load.
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass

# ANSI color codes — kept tiny and dependency-free.
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"

CHECK = f"{GREEN}✓{RESET}"
CROSS = f"{RED}✗{RESET}"
DASH = f"{YELLOW}—{RESET}"


@dataclass
class Probe:
    name: str
    description: str
    runner: Callable[[], str]
    required: bool = True


def _probe_gemini() -> str:
    from core.llm.gemini_client import GeminiClient

    client = GeminiClient()
    # Minimal call — short prompt, no playbook, just verify auth + format.
    memo = client.generate_investment_memo(
        playbook=(
            "You are a test harness. Always return decision=PASS, confidence=0.0, "
            "and short text. The stock is AAPL."
        ),
        stock_data={"ticker": "AAPL", "test": True},
        portfolio_state={"cash_usd": 10000, "positions": []},
    )
    return f"decision={memo.decision} confidence={memo.confidence:.2f}"


def _probe_fmp() -> str:
    from core.data.fmp_source import FMPSource

    quote = FMPSource().get_quote("AAPL")
    return f"AAPL ${quote.price:.2f}"


def _probe_finnhub() -> str:
    from core.data.finnhub_source import FinnhubSource

    quote = FinnhubSource().get_quote("AAPL")
    return f"AAPL ${quote.price:.2f}"


def _probe_alpha_vantage() -> str:
    from core.data.alpha_vantage_source import AlphaVantageSource

    quote = AlphaVantageSource().get_quote("AAPL")
    return f"AAPL ${quote.price:.2f}"


def _probe_marketaux() -> str:
    from core.data.marketaux_source import MarketauxSource

    items = MarketauxSource().get_news("AAPL", limit=1)
    return f"{len(items)} item(s)"


def _probe_edgar() -> str:
    from core.data.edgar_source import EdgarSource

    filings = EdgarSource().get_filings("AAPL", form_type="10-K", limit=1)
    return f"{len(filings)} 10-K filing(s)"


def _probe_yfinance() -> str:
    from core.data.yfinance_source import YFinanceSource

    quote = YFinanceSource().get_quote("AAPL")
    return f"AAPL ${quote.price:.2f}"


PROBES: list[Probe] = [
    Probe("Gemini", "LLM reasoning", _probe_gemini),
    Probe("FMP", "Financial Modeling Prep", _probe_fmp),
    Probe("Finnhub", "real-time quotes + news", _probe_finnhub),
    Probe("AlphaVantage", "news + sentiment", _probe_alpha_vantage),
    Probe("MarketAux", "news aggregator", _probe_marketaux),
    Probe("EDGAR", "SEC filings", _probe_edgar),
    Probe("yfinance", "global market data (no key)", _probe_yfinance, required=False),
]


def main() -> int:
    print(f"\n{BOLD}The Value Council — connection test{RESET}\n")

    # Pre-flight: settings load.
    try:
        from core.config import get_settings

        settings = get_settings()
        print(
            f"{CHECK} settings loaded (log_level={settings.log_level}, "
            f"tase_enabled={settings.tase_enabled})"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"{CROSS} {BOLD}configuration error{RESET}\n")
        print(str(exc))
        return 2

    print()

    failures: list[str] = []
    for probe in PROBES:
        label = f"{BOLD}{probe.name:<14}{RESET} ({probe.description})"
        try:
            detail = probe.runner()
            print(f"  {CHECK} {label}  {DASH} {detail}")
        except Exception as exc:  # noqa: BLE001
            mark = CROSS if probe.required else f"{YELLOW}!{RESET}"
            print(f"  {mark} {label}  {DASH} {type(exc).__name__}: {exc}")
            if probe.required:
                failures.append(probe.name)
            if "--verbose" in sys.argv or "-v" in sys.argv:
                traceback.print_exc()

    print()
    if failures:
        print(f"{RED}{len(failures)} required source(s) failed: {', '.join(failures)}{RESET}")
        return 1
    print(f"{GREEN}{BOLD}All required sources OK.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
