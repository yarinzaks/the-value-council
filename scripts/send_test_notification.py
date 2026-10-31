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

When the send is refused it diagnoses itself. A Telegram refusal names
the fault but not the fix, and the two likely faults — a wrong chat id,
and a bot the owner has never opened — produce the same one. So it asks
Telegram which chats the bot may actually write to, and if exactly one
exists it delivers the message there with the correct id inside it. The
id travels through Telegram rather than through this output on purpose:
this runs in the CI log of a public repository, and nothing here prints
a chat id.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from core.backtest.decision_logger import DecisionLogger
from core.live.notify import (
    ChannelCheck,
    TelegramConfig,
    check_channel,
    choose_decisions,
    notify_run,
    read_config,
    send_message,
    shape_complaints,
)
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


def _market_value(position: Mapping[str, Any]) -> float:
    """What the line is worth today, falling back to what it cost.

    ``current_price`` is written by the close run and so is absent on a
    position opened but not yet marked. The entry price is the honest
    stand-in: it is what the line was worth when it was the only figure
    there was.
    """
    shares = abs(float(position.get("shares") or 0.0))
    price = float(position.get("current_price") or position.get("entry_price") or 0.0)
    return shares * price


def _book(agent: str) -> dict[str, Any]:
    path = portfolios_dir() / f"{agent}.json"
    if not path.exists():
        raise SystemExit(f"no portfolio for {agent} at {path}")
    return dict(json.loads(path.read_text()))


def _latest_decisions(agent: str) -> tuple[date, dict[str, Any]]:
    """The most recent day the agent logged anything, and that day's rows.

    Joined through :func:`choose_decisions`, the same resolver the live
    notifier uses. Building the mapping here instead is how this script
    came to disagree with the thing it exists to rehearse: the days it
    happened to read had no execution receipts in them, so it showed a
    plan that a real trading day would not have carried.
    """
    root = decisions_dir() / agent
    files = sorted(root.glob("*.json")) if root.exists() else []
    if not files:
        return date.today(), {}
    day = date.fromisoformat(files[-1].stem)
    rows = DecisionLogger(root=decisions_dir()).read_day(agent, day)
    return day, choose_decisions(rows)


def _explain(
    config: TelegramConfig,
    text: str,
    *,
    check: Callable[[TelegramConfig], ChannelCheck] = check_channel,
    send: Callable[[TelegramConfig, str, str], None] = send_message,
    shape: Callable[[TelegramConfig], list[str]] = shape_complaints,
) -> int:
    """Report which end is broken, and deliver the message if a chat exists.

    Always returns non-zero. Even the best outcome here — the message
    delivered to a chat the configuration does not name — leaves
    ``TELEGRAM_CHAT_ID`` wrong, and a run that exits 0 on a broken
    configuration is how the next scheduled notification goes missing
    quietly.

    Prints no chat id and no token under any branch. The reader needs a
    chat id, so it is sent inside the Telegram message, where only its
    owner can read it; faults in the credentials themselves are reported
    as shape — a length, a missing colon — rather than as a value.
    """
    # Shape first, and no further. A token that is not token-shaped
    # cannot be used to ask Telegram anything, so a channel check would
    # only restate the same refusal with a different method name.
    complaints = shape(config)
    if complaints:
        print("the credentials are the wrong shape:", file=sys.stderr)
        for complaint in complaints:
            print(f"  - {complaint}", file=sys.stderr)
        return 1

    try:
        found = check(config)
    except Exception as exc:  # noqa: BLE001 — the diagnosis is best-effort
        print(f"could not ask telegram anything: {exc}", file=sys.stderr)
        print(
            "the credentials are shaped right, so the token is either "
            "revoked or belongs to no bot. Send /token to BotFather and "
            "store what it replies in TELEGRAM_BOT_TOKEN.",
            file=sys.stderr,
        )
        return 1

    print(f"the token works — the bot is {found.bot}", file=sys.stderr)

    if found.configured_found:
        print(
            "TELEGRAM_CHAT_ID names a chat this bot can see, so the chat id "
            "is right and the refusal above is the whole fault.",
            file=sys.stderr,
        )
        return 1

    if not found.chats:
        print(
            f"no chat has written to {found.bot} in the last 24 hours, and "
            "telegram does not let a bot write first. Open that bot on your "
            "phone, send it any message, then run this again.",
            file=sys.stderr,
        )
        return 1

    if len(found.chats) > 1:
        print(
            f"{len(found.chats)} chats have written to {found.bot} and "
            "TELEGRAM_CHAT_ID matches none of them. Which one is yours is "
            "not something this script should guess.",
            file=sys.stderr,
        )
        return 1

    chat_id = found.chats[0]
    try:
        send(
            config,
            chat_id,
            f"{text}\n\n———\n\n"
            "ה-TELEGRAM_CHAT_ID שמוגדר כרגע שגוי, ולכן ההודעה הזאת נשלחה "
            "לצ'אט היחיד שכתב לבוט.\n"
            f"המזהה הנכון: {chat_id}\n"
            "GitHub → Settings → Secrets and variables → Actions → "
            "TELEGRAM_CHAT_ID",
        )
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        print(
            f"the one chat that exists refused as well: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        "TELEGRAM_CHAT_ID is wrong. The message went to the one chat that "
        "has written to the bot, and it carries the id to put in the secret "
        "— on your phone, not in this log.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    args = parser.parse_args(argv)

    if not _SLUG.match(args.agent):
        raise SystemExit(f"not an agent slug: {args.agent!r}")

    config = read_config(os.environ)
    if config is None:
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

    # The largest holding by market value, so the figures are the ones
    # the dashboard puts at the top of the book. Ranking on share count
    # instead answered a different question: Graham holds 94 shares of a
    # $19 name and 0.41 of a $3,876 one, so the count picked the
    # smallest line and called it the largest.
    held = max(positions, key=_market_value)
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

    # ``notify_run`` may not raise — it runs after a real trading run and
    # must not turn a completed one into a failure — so it logs the cause
    # and returns a count. That leaves a caller with no cause at all,
    # which is how one failed run produced the single word "HTTPError".
    # Wrapping the real sender keeps that contract and keeps the reason.
    attempted: list[str] = []
    failures: list[str] = []

    def send(cfg: TelegramConfig, text: str) -> None:
        attempted.append(text)
        try:
            send_message(cfg, cfg.chat_id, text)
        except Exception as exc:
            failures.append(str(exc))
            raise

    sent = notify_run(
        [result],
        as_of=day,
        env=os.environ,
        # The agent under test, whatever it is — not the live watch list.
        agents=frozenset({args.agent}),
        agent_names={args.agent: f"[בדיקה] {args.agent.replace('_', ' ').title()}"},
        decisions_for=lambda _agent, _day: decisions,
        send=send,
    )
    if sent:
        print(f"sent {sent} message(s) for {args.agent} ({ticker}, {day})")
        return 0

    if not attempted:
        print(
            f"nothing was sent: {args.agent} produced no message to send.",
            file=sys.stderr,
        )
        return 1

    print(f"telegram refused: {failures[-1]}", file=sys.stderr)
    return _explain(config, attempted[-1])


if __name__ == "__main__":
    raise SystemExit(main())
