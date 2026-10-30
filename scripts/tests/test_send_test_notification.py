"""The diagnosis must not print the thing it is diagnosing.

This script runs as a GitHub Actions step on a public repository, so its
stdout and stderr are public. The chat id it needs to tell the reader
about is kept in a secret, which means the one piece of information the
reader is missing is the one piece that may not be printed. The way out
is to send it through Telegram — to the chat it identifies, where only
its owner can read it — and the tests below hold that line.

Exit status is the other contract. Every branch here leaves
``TELEGRAM_CHAT_ID`` wrong, including the branch that successfully
delivers a message, so every branch must fail the step. A green run on a
broken configuration is how the next real notification goes missing
without anyone noticing.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.live.notify import ChannelCheck, TelegramConfig
from scripts.send_test_notification import _explain

TOKEN = "123456:AAHsupersecrettokenvalue"
CHAT = "987654321"
OTHER = "555666777"
CONFIG = TelegramConfig(token=TOKEN, chat_id=CHAT)
MESSAGE = "Benjamin Graham קנה SEB"


def a_check(chats: tuple[str, ...], *, configured: bool) -> Any:
    def check(config: TelegramConfig) -> ChannelCheck:
        return ChannelCheck(
            bot="@Value_Council_Bot", chats=chats, configured_found=configured
        )

    return check


def never_sends(*args: Any) -> None:
    raise AssertionError("nothing should have been sent on this branch")


def test_a_bot_nobody_opened_becomes_an_instruction(capsys):
    """The likeliest first-run fault, and the only one the user must fix.

    Telegram does not let a bot write first, so there is nothing to send
    and nothing to guess — only something for a human to do on a phone.
    """
    code = _explain(
        CONFIG, MESSAGE, check=a_check((), configured=False), send=never_sends
    )
    captured = capsys.readouterr()

    assert code == 1
    assert "send it any message" in captured.err
    assert "@Value_Council_Bot" in captured.err, "name the bot to open"


def test_a_wrong_chat_id_is_delivered_to_the_chat_that_does_exist(capsys):
    sent: list[tuple[Any, ...]] = []
    code = _explain(
        CONFIG,
        MESSAGE,
        check=a_check((OTHER,), configured=False),
        send=lambda *args: sent.append(args),
    )
    captured = capsys.readouterr()

    assert code == 1, "the secret is still wrong, so the step still fails"
    assert len(sent) == 1
    _config, chat_id, text = sent[0]
    assert chat_id == OTHER, "sent to the chat that exists, not the configured one"
    assert MESSAGE in text, "the real notification, not a bare ping"
    assert OTHER in text, "the id has to reach its owner somehow"

    assert OTHER not in captured.err, "a chat id reached a public log"
    assert OTHER not in captured.out, "a chat id reached a public log"


def test_a_matching_chat_id_sends_the_reader_back_to_the_refusal(capsys):
    """Nothing to re-send: the chat id was never the problem."""
    code = _explain(
        CONFIG, MESSAGE, check=a_check((CHAT,), configured=True), send=never_sends
    )

    assert code == 1
    assert "is right" in capsys.readouterr().err


def test_several_chats_are_reported_rather_than_guessed_between(capsys):
    code = _explain(
        CONFIG,
        MESSAGE,
        check=a_check((OTHER, "111222333"), configured=False),
        send=never_sends,
    )
    captured = capsys.readouterr()

    assert code == 1
    assert "2 chats" in captured.err
    assert OTHER not in captured.err
    assert "111222333" not in captured.err


def test_a_diagnosis_that_itself_fails_is_reported_not_raised(capsys):
    """A bad token fails the check too, and must not become a traceback.

    A traceback would print the chained urllib error, and that names the
    request URL — which is where the token lives.
    """

    def check(config: TelegramConfig) -> ChannelCheck:
        raise RuntimeError("getMe returned 401: Unauthorized")

    code = _explain(CONFIG, MESSAGE, check=check, send=never_sends)
    captured = capsys.readouterr()

    assert code == 1
    assert "Unauthorized" in captured.err
    assert TOKEN not in captured.err


def test_a_refusal_from_the_one_chat_is_reported(capsys):
    def refuses(*args: Any) -> None:
        raise RuntimeError("sendMessage returned 403: Forbidden")

    code = _explain(
        CONFIG, MESSAGE, check=a_check((OTHER,), configured=False), send=refuses
    )
    captured = capsys.readouterr()

    assert code == 1
    assert "Forbidden" in captured.err
    assert OTHER not in captured.err


@pytest.mark.parametrize(
    "chats,configured",
    [((), False), ((OTHER,), False), ((CHAT,), True), ((OTHER, "111"), False)],
)
def test_every_branch_fails_the_step(chats, configured):
    """Including the one that delivers a message.

    Parametrised over the branches rather than asserted once per test so
    that a branch added later without a status is caught here.
    """
    code = _explain(
        CONFIG,
        MESSAGE,
        check=a_check(chats, configured=configured),
        send=lambda *args: None,
    )
    assert code == 1
