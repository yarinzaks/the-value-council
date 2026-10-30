"""The notifier's contract is mostly about what it must NOT do.

It runs after a trading run has executed and persisted, so the only harm
it can cause is to that run's exit status or to the secrecy of a token.
Both are covered here: every failure path ends in a log line rather than
an exception, and the log line may not carry the credential — urllib
puts the request URL in its error messages, and for Telegram the token
is in the URL.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from email.message import Message
from typing import Any

import pytest

from core.live.notify import (
    REDACTED,
    TelegramConfig,
    TelegramSendError,
    check_channel,
    clean_company_name,
    format_result,
    format_trade,
    notify_run,
    read_config,
    redact,
    send_message,
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


# ------------------------------------------- a refusal a reader can act on
#
# The first live run of this notifier failed, and the only thing the log
# said was "HTTPError". The status and Telegram's own sentence were both
# in hand and both thrown away, so the single most likely cause — a chat
# id guessed from the wrong place — was indistinguishable from a bad
# token. Everything below exists so that cannot happen twice, under the
# standing constraint that none of it may print the credential.

CONFIG = TelegramConfig(token=TOKEN, chat_id=CHAT)


class FakeResponse:
    """The file-like object ``urlopen`` returns, with a canned body."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def raising(error: BaseException) -> Any:
    def urlopen(request: Any, timeout: float | None = None) -> FakeResponse:
        raise error

    return urlopen


def returning(body: str) -> Any:
    def urlopen(request: Any, timeout: float | None = None) -> FakeResponse:
        return FakeResponse(body)

    return urlopen


def an_http_error(code: int, body: str) -> urllib.error.HTTPError:
    """A refusal shaped the way urllib hands one over.

    The URL carries the token, exactly as the real one does — that is
    the whole reason this class cannot be allowed to reach a log.
    """
    return urllib.error.HTTPError(
        url=f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        code=code,
        msg="Bad Request",
        hdrs=Message(),
        fp=io.BytesIO(body.encode("utf-8")),
    )


def test_redact_removes_both_credentials():
    text = f"POST https://api.telegram.org/bot{TOKEN}/sendMessage to {CHAT}"
    cleaned = redact(text, CONFIG)

    assert TOKEN not in cleaned
    assert "supersecret" not in cleaned
    assert CHAT not in cleaned, "the chat id is held in a secret too"
    assert cleaned.count(REDACTED) == 2


def test_redact_leaves_a_short_chat_id_alone():
    """A two-character id would match digits all over a message.

    Substituting it would corrupt the text it was meant to protect, and
    a malformed secret is a configuration error rather than a licence to
    mangle every number in a report.
    """
    config = TelegramConfig(token=TOKEN, chat_id="42")
    assert redact("sold 42 shares at 42", config) == "sold 42 shares at 42"


def test_a_refusal_reports_the_status_and_telegrams_own_words(monkeypatch):
    """The fix for the failed run: 400 and "chat not found" ARE the diagnosis.

    Neither the exception type nor the status alone separates a wrong
    chat id from a wrong token, and those have different fixes.
    """
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        raising(
            an_http_error(
                400,
                '{"ok":false,"error_code":400,'
                '"description":"Bad Request: chat not found"}',
            )
        ),
    )

    with pytest.raises(TelegramSendError) as caught:
        send_message(CONFIG, CHAT, "hello")

    message = str(caught.value)
    assert "400" in message
    assert "chat not found" in message
    assert TOKEN not in message, "the url in the HTTPError reached the message"
    assert "supersecret" not in message


def test_a_bad_token_is_named_as_unauthorised(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        raising(an_http_error(401, '{"ok":false,"description":"Unauthorized"}')),
    )

    with pytest.raises(TelegramSendError) as caught:
        send_message(CONFIG, CHAT, "hello")

    assert "401" in str(caught.value)
    assert "Unauthorized" in str(caught.value)
    assert TOKEN not in str(caught.value)


def test_an_error_body_that_is_not_json_still_reports_something(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", raising(an_http_error(502, "<html>bad gateway"))
    )

    with pytest.raises(TelegramSendError) as caught:
        send_message(CONFIG, CHAT, "hello")

    assert "502" in str(caught.value)
    assert "bad gateway" in str(caught.value)


def test_an_unreachable_telegram_is_distinguished_from_a_refusal(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        raising(urllib.error.URLError("nodename nor servname provided")),
    )

    with pytest.raises(TelegramSendError) as caught:
        send_message(CONFIG, CHAT, "hello")

    assert "could not reach telegram" in str(caught.value)
    assert "nodename" in str(caught.value)


def test_ok_false_is_a_refusal_even_with_a_200(monkeypatch):
    """Telegram answers some failures with a 200 and ``ok: false``.

    Treating the status alone as success would report a message nobody
    received, which is the one outcome this whole module exists to avoid.
    """
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        returning('{"ok":false,"description":"Forbidden: bot was blocked by the user"}'),
    )

    with pytest.raises(TelegramSendError) as caught:
        send_message(CONFIG, CHAT, "hello")

    assert "blocked by the user" in str(caught.value)


def test_a_successful_send_posts_the_text_to_the_named_chat(monkeypatch):
    requests: list[Any] = []

    def urlopen(request: Any, timeout: float | None = None) -> FakeResponse:
        requests.append(request)
        return FakeResponse('{"ok":true,"result":{"message_id":7}}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    send_message(CONFIG, "111222333", "hello")

    assert len(requests) == 1
    assert requests[0].method == "POST"
    body = json.loads(requests[0].data)
    assert body["chat_id"] == "111222333", "the chat is the argument, not the config"
    assert body["text"] == "hello"
    assert body["disable_web_page_preview"] is True


def test_a_failure_logs_why_and_not_only_the_type():
    """The log line that failed to say anything, now saying it.

    Paired with ``test_a_failure_does_not_log_the_token`` above: that one
    proves the credential stays out, this one proves the reason gets in.
    Either alone is satisfiable by a log line that is useless or unsafe.
    """
    from loguru import logger as loguru_logger

    messages: list[str] = []
    sink_id = loguru_logger.add(lambda m: messages.append(str(m)), level="DEBUG")
    try:
        notify_run(
            [FakeResult(agent="benjamin_graham", trades=[a_buy()])],
            as_of=AS_OF,
            env=ENV,
            send=Recorder(
                fail=TelegramSendError(
                    "sendMessage returned 400: Bad Request: chat not found"
                )
            ),
        )
    finally:
        loguru_logger.remove(sink_id)
    logged = "".join(messages)

    assert "benjamin_graham" in logged
    assert "400" in logged
    assert "chat not found" in logged


# --------------------------------------------- which chats actually exist


def a_channel(updates: Any, *, username: str = "Value_Council_Bot") -> Any:
    def call(config: TelegramConfig, method: str, **kwargs: Any) -> Any:
        if method == "getMe":
            return {"id": 123456, "username": username}
        if method == "getUpdates":
            return updates
        raise AssertionError(f"unexpected call to {method}")

    return call


def test_check_channel_names_the_bot_and_the_chats_that_wrote_to_it():
    found = check_channel(
        CONFIG,
        call=a_channel(
            [
                {"message": {"chat": {"id": int(CHAT)}}},
                {"message": {"chat": {"id": int(CHAT)}}},
                {"my_chat_member": {"chat": {"id": 555}}},
            ]
        ),
    )

    assert found.bot == "@Value_Council_Bot"
    assert found.chats == (CHAT, "555"), "each chat once, in the order seen"
    assert found.configured_found is True


def test_check_channel_counts_a_bot_nobody_opened_as_having_no_chats():
    """The fault Telegram reports identically to a wrong chat id.

    A bot may not write first, so a chat that was never started refuses
    exactly like one that does not exist. An empty update list is the
    only thing that separates them.
    """
    found = check_channel(CONFIG, call=a_channel([]))

    assert found.chats == ()
    assert found.configured_found is False
    assert found.bot == "@Value_Council_Bot"


def test_check_channel_knows_the_configured_chat_is_not_among_them():
    found = check_channel(
        CONFIG, call=a_channel([{"message": {"chat": {"id": 111222333}}}])
    )

    assert found.chats == ("111222333",)
    assert found.configured_found is False


def test_check_channel_survives_an_update_shape_it_does_not_know():
    """Telegram adds update kinds; an unknown one is not a crash."""
    found = check_channel(
        CONFIG,
        call=a_channel(
            [
                {"poll_answer": {"poll_id": "x"}},
                {"message": {"from": {"id": 9}}},
                "not a mapping at all",
                {"message": {"chat": {}}},
                {"message": {"chat": {"id": int(CHAT)}}},
            ]
        ),
    )

    assert found.chats == (CHAT,)
    assert found.configured_found is True
