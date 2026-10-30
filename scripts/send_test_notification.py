"""Send one Telegram notification, to prove the channel works.

Why this exists
~~~~~~~~~~~~~~~

The notifier only speaks when an agent trades, which on Graham's cadence
is roughly one day in six. Waiting for that to find out whether the
credentials are right, whether the bot was ever opened from the phone,
and whether the message reads well is a slow way to learn any of it.

What it sends is a real message, not a ping. It builds the trade from
the agent's own book and the day's journal entry, and runs it through
the same ``notify_run`` the live runner calls — so a message that
arrives proves the format, the enrichment and the plumbing together. A
first line marks it as a test, because a message that looks exactly like
a trade should not be mistaken for one.

Usage::

    .venv/bin/python -m scripts.send_test_notification
    .venv/bin/python -m scripts.send_test_notification --agent benjamin_graham

Needs ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` in the
environment. Without them it says so and exits non-zero, rather than
reporting success for a message nobody received.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from core.backtest.decision_logger import DecisionLogger
from core.live.notify import notify_run, read_config
from core.paths import decisions_dir, portfolios_dir

DEFAULT_AGENT = "benjamin_graham"

#: The slug becomes a path segment under the data root, so it is
#: validated rather than trusted. The workflow that calls this takes the
#: value as a dispatch input; triggering it needs write access, but a
#: value that reaches `open()` is worth constraining whatever its source.
_SLUG = re.compile(r"\A[a-z0-9_]{1,64}\Z")


@dataclass
class _Trade:
    """Shaped like ``TradeRecord``; built from a real holding."""

    ticker: str
    side: str
    shares: float
    price: float
    gross_value: float
    cost_paid: float = 0.0
    realized_pnl_usd: float = 0.0


@dataclass
class _Portfolio:
    total_nav: float
    cumulative_return_pct: float


@dataclass
class _Result:
    agent: str
    trades: list[Any] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False
    portfolio: Any = None


def _book(agent: str) -> dict[str, Any]:
    path = portfolios_dir() / f"{agent}.json"
    if not path.exists():
        raise SystemExit(f"no portfolio for {agent} at {path}")
    return dict(json.loads(path.read_text()))


def _latest_decisions(agent: str) -> tuple[date, dict[str, Any]]:
    """The most recent day the agent logged anything, and that day's rows."""
    root = decisions_dir() / agent
    files = sorted(root.glob("*.json")) if root.exists() else []
    if not files:
        return date.today(), {}
    day = date.fromisoformat(files[-1].stem)
    rows = DecisionLogger(root=decisions_dir()).read_day(agent, day)
    return day, {str(r.ticker).upper(): r for r in rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    args = parser.parse_args(argv)

    if not _SLUG.match(args.agent):
        raise SystemExit(f"not an agent slug: {args.agent!r}")

    if read_config(os.environ) is None:
        print(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not both set; "
            "nothing was sent.",
            file=sys.stderr,
        )
        return 1

    book = _book(args.agent)
    positions = list(book.get("positions") or [])
    if not positions:
        raise SystemExit(f"{args.agent} holds nothing to describe")

    day, decisions = _latest_decisions(args.agent)

    # The largest holding, so the figures are recognisable.
    held = max(positions, key=lambda p: abs(float(p.get("shares") or 0.0)))
    ticker = str(held["ticker"]).upper()
    shares = float(held.get("shares") or 0.0)
    price = float(held.get("entry_price") or held.get("current_price") or 0.0)

    trade = _Trade(
        ticker=ticker,
        side="BUY",
        shares=shares,
        price=price,
        gross_value=shares * price,
    )
    result = _Result(
        agent=args.agent,
        trades=[trade],
        portfolio=_Portfolio(
            total_nav=float(book.get("total_nav") or 0.0),
            cumulative_return_pct=float(book.get("cumulative_return_pct") or 0.0),
        ),
    )

    sent = notify_run(
        [result],
        as_of=day,
        env=os.environ,
        # The agent under test, whatever it is — not the live watch list.
        agents=frozenset({args.agent}),
        agent_names={args.agent: f"[בדיקה] {args.agent.replace('_', ' ').title()}"},
        decisions_for=lambda _agent, _day: decisions,
    )
    if sent == 0:
        print("notify_run sent nothing — see the log above", file=sys.stderr)
        return 1
    print(f"sent {sent} message(s) for {args.agent} ({ticker}, {day})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
