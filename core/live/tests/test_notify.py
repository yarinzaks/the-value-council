"""The notifier's contract is mostly about what it must NOT do.

It runs after a trading run has executed and persisted, so the only harm
it can cause is to that run's exit status or to the secrecy of a token.
Both are covered here: every failure path ends in a log line rather than
an exception, and the log line may not carry the credential — urllib
puts the request URL in its error messages, and for Telegram the token
is in the URL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from core.live.notify import (
    clean_company_name,
    TelegramConfig,
    format_result,
    format_trade,
    notify_run,
    read_config,
)

AS_OF = date(2026, 10, 30)
TOKEN = "123456:AAHsupersecrettokenvalue"
CHAT = "987654321"
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT}


@dataclass
class FakeTrade:
    ticker: str
    side: str
    shares: float
    price: float
    gross_value: float
    cost_paid: float = 0.0
    realized_pnl_usd: float = 0.0


@dataclass
class FakePortfolio:
    total_nav: float = 11_934.0
    cumulative_return_pct: float = 19.34


@dataclass
class FakeResult:
    agent: str
    trades: list[Any] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False
    portfolio: Any = field(default_factory=FakePortfolio)


def a_buy(ticker="HOG", shares=74.375, price=25.95):
    return FakeTrade(
        ticker=ticker,
        side="BUY",
        shares=shares,
        price=price,
        gross_value=shares * price,
    )


def a_sell(ticker="UFPI", realized=326.08):
    return FakeTrade(
        ticker=ticker,
        side="SELL",
        shares=9.0,
        price=104.5,
        gross_value=940.5,
        realized_pnl_usd=realized,
    )


class Recorder:
    """A send that records instead of sending."""

    def __init__(self, fail: BaseException | None = None) -> None:
        self.calls: list[tuple[TelegramConfig, str]] = []
        self.fail = fail

    def __call__(self, config: TelegramConfig, text: str) -> None:
        self.calls.append((config, text))
        if self.fail is not None:
            raise self.fail


# --------------------------------------------------------------- the config


def test_unconfigured_is_not_an_error():
    assert read_config({}) is None


@pytest.mark.parametrize(
    "env",
    [
        {"TELEGRAM_BOT_TOKEN": TOKEN},
        {"TELEGRAM_CHAT_ID": CHAT},
        {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": CHAT},
        {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "   "},
    ],
)
def test_half_a_configuration_is_no_configuration(env):
    # A token with nowhere to send would 400 on every run; better to be
    # off than to be noisily broken.
    assert read_config(env) is None


def test_whitespace_is_trimmed():
    # A value pasted into a secrets field often carries a newline.
    config = read_config({"TELEGRAM_BOT_TOKEN": f" {TOKEN}\n", "TELEGRAM_CHAT_ID": f" {CHAT} "})
    assert config is not None
    assert config.token == TOKEN
    assert config.chat_id == CHAT


# ---------------------------------------------------------------- formatting


def test_a_buy_names_side_ticker_shares_and_price():
    line = format_trade(a_buy())
    assert "BUY" in line
    assert "HOG" in line
    assert "25.95" in line


def test_a_sell_reports_realised_profit():
    line = format_trade(a_sell(realized=326.08))
    assert "SELL" in line
    assert "326.08" in line


def test_a_losing_sell_is_not_reported_as_a_gain():
    line = format_trade(a_sell(realized=-168.90))
    assert "168.90" in line
    assert "+$168.90" not in line


def test_a_buy_carries_no_realised_figure():
    # There is nothing realised on the way in, and printing 0.00 would
    # read as a break-even round trip.
    assert "realised" not in format_trade(a_buy())


def test_a_run_with_no_trades_says_nothing():
    # Silence is the signal that the agent held.
    assert format_result(FakeResult(agent="benjamin_graham"), as_of=AS_OF) is None


def test_a_run_with_trades_names_the_agent_and_the_date():
    text = format_result(
        FakeResult(agent="benjamin_graham", trades=[a_buy()]), as_of=AS_OF
    )
    assert text is not None
    assert "Benjamin Graham" in text
    assert "2026-10-30" in text


def test_the_message_carries_nav_and_return():
    text = format_result(
        FakeResult(agent="benjamin_graham", trades=[a_buy()]), as_of=AS_OF
    )
    assert "11,934.00" in text
    assert "+19.34%" in text


def test_a_failed_run_is_reported_even_though_it_is_not_an_action():
    # A book that stopped trading because of a fault looks, from outside,
    # exactly like a book that chose to hold.
    text = format_result(
        FakeResult(agent="benjamin_graham", error="EDGAR cache missing"), as_of=AS_OF
    )
    assert text is not None
    assert "EDGAR cache missing" in text


def test_an_error_is_reported_even_when_there_were_trades():
    text = format_result(
        FakeResult(agent="benjamin_graham", trades=[a_buy()], error="partial failure"),
        as_of=AS_OF,
    )
    assert "partial failure" in text


# ------------------------------------------------------------------ sending


def test_nothing_is_sent_without_credentials():
    send = Recorder()
    sent = notify_run(
        [FakeResult(agent="benjamin_graham", trades=[a_buy()])],
        as_of=AS_OF,
        env={},
        send=send,
    )
    assert sent == 0
    assert send.calls == [], "attempted to send without credentials"


def test_an_unwatched_agent_is_ignored():
    send = Recorder()
    sent = notify_run(
        [FakeResult(agent="warren_buffett", trades=[a_buy()])],
        as_of=AS_OF,
        env=ENV,
        send=send,
    )
    assert sent == 0
    assert send.calls == []


def test_the_watched_agent_is_sent():
    send = Recorder()
    sent = notify_run(
        [FakeResult(agent="benjamin_graham", trades=[a_buy(), a_sell()])],
        as_of=AS_OF,
        env=ENV,
        send=send,
    )
    assert sent == 1
    assert len(send.calls) == 1
    config, text = send.calls[0]
    assert config.chat_id == CHAT
    assert "HOG" in text and "UFPI" in text


def test_a_skipped_run_sends_nothing():
    # The day had already been completed; the run was a replay.
    send = Recorder()
    sent = notify_run(
        [FakeResult(agent="benjamin_graham", trades=[a_buy()], skipped=True)],
        as_of=AS_OF,
        env=ENV,
        send=send,
    )
    assert sent == 0


def test_watching_a_second_agent_is_one_argument():
    send = Recorder()
    sent = notify_run(
        [
            FakeResult(agent="benjamin_graham", trades=[a_buy()]),
            FakeResult(agent="benjamin_graham_2", trades=[a_buy()]),
        ],
        as_of=AS_OF,
        env=ENV,
        agents=frozenset({"benjamin_graham", "benjamin_graham_2"}),
        send=send,
    )
    assert sent == 2


def test_a_send_failure_does_not_propagate():
    # The whole reason this module hangs off finished results: a run that
    # already moved money must not be failed by a notification.
    send = Recorder(fail=RuntimeError("telegram is down"))
    sent = notify_run(
        [FakeResult(agent="benjamin_graham", trades=[a_buy()])],
        as_of=AS_OF,
        env=ENV,
        send=send,
    )
    assert sent == 0


def test_one_failure_does_not_stop_the_next_message():
    class FailFirst(Recorder):
        def __call__(self, config, text):
            self.calls.append((config, text))
            if len(self.calls) == 1:
                raise RuntimeError("transient")

    send = FailFirst()
    sent = notify_run(
        [
            FakeResult(agent="benjamin_graham", trades=[a_buy()]),
            FakeResult(agent="benjamin_graham_2", trades=[a_buy()]),
        ],
        as_of=AS_OF,
        env=ENV,
        agents=frozenset({"benjamin_graham", "benjamin_graham_2"}),
        send=send,
    )
    assert sent == 1
    assert len(send.calls) == 2


def test_a_failure_does_not_log_the_token():
    """The security-critical one.

    ``urllib`` puts the request URL in its exception messages, and for
    Telegram the bot token is a path segment of that URL. Logging the
    exception's text would publish the credential into the CI log of a
    public repository.

    Captured through a loguru sink, not ``caplog`` or ``capsys``. This
    project logs through loguru, which neither propagates to the stdlib
    root logger nor writes to the stream ``capsys`` swaps in — its sink
    is bound to the real stderr at import. Both of those come back empty,
    and an empty log makes every assertion about its contents pass
    without testing anything. The "says something" assertion below is
    what caught that, and is the reason it is first.
    """
    from loguru import logger as loguru_logger

    messages: list[str] = []
    sink_id = loguru_logger.add(lambda m: messages.append(str(m)), level="DEBUG")
    send = Recorder(
        fail=RuntimeError(f"HTTP 401 for https://api.telegram.org/bot{TOKEN}/sendMessage")
    )
    try:
        notify_run(
            [FakeResult(agent="benjamin_graham", trades=[a_buy()])],
            as_of=AS_OF,
            env=ENV,
            send=send,
        )
    finally:
        loguru_logger.remove(sink_id)
    logged = "".join(messages)

    # It must say something, or a misconfiguration is invisible — and an
    # empty log would make the secrecy checks below vacuous.
    assert "benjamin_graham" in logged
    assert TOKEN not in logged, "the bot token reached the log"
    assert "supersecret" not in logged


# ------------------------------------------------- the enriched message


@dataclass
class FakeDecision:
    """The journal fields the message reads."""

    ticker: str = "SEB"
    criteria_met: list[str] = field(default_factory=list)
    criteria_values: dict[str, Any] = field(default_factory=dict)
    target_price: float | None = None
    exit_trigger: str | None = None


NAMES = {
    "SEB": "SEABOARD CORP /DE/",
    "HOG": "HARLEY-DAVIDSON, INC.",
    "INGR": "Ingredion Inc",
    "AAPL": "Apple Inc.",
}


def industries(ticker: str) -> str | None:
    return {
        "SEB": "Wholesale-Farm Product Raw Materials",
        "HOG": "Motorcycles, Bicycles & Parts",
        "AAPL": "Electronic Computers",
    }.get(ticker.upper())


def a_defensive_decision(ticker="SEB", target=7936.58):
    return FakeDecision(
        ticker=ticker,
        criteria_met=[
            "P/E=7.52 (≤ 15.0)",
            "P/B=0.71 (≤ 1.5)",
            "mode=DEFENSIVE",
        ],
        criteria_values={"mode": "DEFENSIVE", "graham_number": target},
        target_price=target,
    )


def rendered(result, decisions=None):
    return format_result(
        result,
        as_of=AS_OF,
        decisions=decisions or {},
        names=NAMES,
        industry=industries,
    )


def test_the_company_line_carries_a_clean_name_and_the_industry():
    text = rendered(
        FakeResult(agent="benjamin_graham", trades=[a_buy(ticker="SEB", price=3871.21)]),
        {"SEB": a_defensive_decision()},
    )
    assert "Seaboard Corp" in text, "the EDGAR name was not cleaned"
    assert "SEABOARD CORP" not in text
    assert "/DE/" not in text
    assert "Wholesale-Farm Product Raw Materials" in text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SEABOARD CORP /DE/", "Seaboard Corp"),
        ("UNIVERSAL CORP /VA/", "Universal Corp"),
        ("HARLEY-DAVIDSON, INC.", "Harley-Davidson, Inc."),
        # Already mixed case: title-casing would be a round trip through
        # a worse spelling.
        ("Ingredion Inc", "Ingredion Inc"),
        ("Apple Inc.", "Apple Inc."),
    ],
)
def test_clean_company_name(raw, expected):
    assert clean_company_name(raw) == expected


def test_a_buy_lists_what_the_company_passed():
    text = rendered(
        FakeResult(agent="benjamin_graham", trades=[a_buy(ticker="SEB")]),
        {"SEB": a_defensive_decision()},
    )
    assert "מה זיהה" in text
    assert "P/E=7.52 (≤ 15.0)" in text


def test_the_mode_marker_is_not_shown_as_a_criterion():
    # "mode=DEFENSIVE" is bookkeeping; it heads the plan already.
    text = rendered(
        FakeResult(agent="benjamin_graham", trades=[a_buy(ticker="SEB")]),
        {"SEB": a_defensive_decision()},
    )
    assert "mode=DEFENSIVE" not in text


def test_a_buy_states_the_sell_target_and_the_upside():
    text = rendered(
        FakeResult(agent="benjamin_graham", trades=[a_buy(ticker="SEB", price=3871.21)]),
        {"SEB": a_defensive_decision(target=7936.58)},
    )
    assert "התוכנית" in text
    assert "7,936.58" in text
    assert "מספר גרהם" in text
    assert "+105.0%" in text


def test_the_plan_names_the_rule_for_the_sleeve():
    text = rendered(
        FakeResult(agent="benjamin_graham", trades=[a_buy(ticker="SEB")]),
        {"SEB": a_defensive_decision()},
    )
    assert "מוכר כשהמחיר מגיע למספר גרהם" in text


def test_a_net_net_buy_gets_its_own_rule_and_label():
    decision = FakeDecision(
        ticker="SEB",
        criteria_met=["P/NCAV=0.50 (≤ 0.67)"],
        criteria_values={"mode": "NET_NET"},
        target_price=100.0,
    )
    text = rendered(
        FakeResult(agent="benjamin_graham", trades=[a_buy(ticker="SEB", price=50.0)]),
        {"SEB": decision},
    )
    assert "NCAV למניה" in text
    assert "NCAV המלא" in text


def test_a_sell_does_not_label_the_exit_reason_as_what_it_spotted():
    """The field holds different things in each direction.

    An exit row records why the position was closed — "left today's
    target list" — so heading it "what it spotted" would describe a sale
    by the reasoning that justified the purchase.
    """
    decision = FakeDecision(ticker="UFPI", criteria_met=["left today's target list"])
    text = rendered(
        FakeResult(agent="benjamin_graham", trades=[a_sell(ticker="UFPI")]),
        {"UFPI": decision},
    )
    assert "למה יצא" in text
    assert "מה זיהה" not in text


def test_a_sell_states_no_buy_target():
    # The plan block describes where a position is meant to close; on the
    # way out there is nothing left to wait for.
    decision = a_defensive_decision(ticker="HOG")
    text = rendered(
        FakeResult(agent="benjamin_graham", trades=[a_sell(ticker="HOG")]),
        {"HOG": decision},
    )
    assert "התוכנית" not in text


def test_the_agent_is_named_from_the_result_not_hard_coded():
    """The requirement that this serve every agent.

    An earlier draft wrote "גרהם" into the action line, which would have
    announced Buffett's trades under Graham's name.
    """
    text = rendered(FakeResult(agent="warren_buffett", trades=[a_buy(ticker="AAPL")]))
    assert "Warren Buffett" in text
    assert "גרהם" not in text
    assert "Apple Inc." in text
    assert "Electronic Computers" in text


def test_a_trade_with_no_journal_entry_still_reports_the_fill():
    # Enrichment is optional; losing it must not lose the message.
    text = rendered(FakeResult(agent="benjamin_graham", trades=[a_buy(ticker="HOG")]))
    assert text is not None
    assert "HOG" in text
    assert "מה זיהה" not in text


def test_an_unknown_ticker_falls_back_to_the_symbol():
    text = rendered(FakeResult(agent="benjamin_graham", trades=[a_buy(ticker="ZZZZ")]))
    assert "ZZZZ" in text


def test_several_trades_are_separated():
    text = rendered(
        FakeResult(
            agent="benjamin_graham",
            trades=[a_buy(ticker="SEB"), a_sell(ticker="HOG")],
        ),
        {"SEB": a_defensive_decision()},
    )
    assert text.count("———") >= 2, "the blocks and the footer should be separated"
    assert "SEB" in text and "HOG" in text


def test_notify_run_passes_the_lookups_through():
    send = Recorder()
    sent = notify_run(
        [FakeResult(agent="benjamin_graham", trades=[a_buy(ticker="SEB")])],
        as_of=AS_OF,
        env=ENV,
        send=send,
        decisions_for=lambda agent, day: {"SEB": a_defensive_decision()},
        company_names=NAMES,
        industry=industries,
    )
    assert sent == 1
    _, text = send.calls[0]
    assert "Seaboard Corp" in text
    assert "7,936.58" in text
