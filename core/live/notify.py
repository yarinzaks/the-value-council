"""Telegram notifications for a finished paper-trading run.

Where this hangs, and why
~~~~~~~~~~~~~~~~~~~~~~~~~

On :class:`~core.live.runner.AgentRunResult`, after a run has executed
and persisted everything — not inside the trade loop. A notification is
the least important thing the runner does, and the loop it would sit in
is the most important: a raised exception, a hung socket or a rate limit
there could abort a run that had already moved money. Reading finished
results instead means the worst a broken notifier can do is fail to
describe work that already happened correctly.

Everything here therefore degrades to silence rather than to an error.
No credentials configured is the normal state of a fresh checkout, not a
fault, so it returns without touching the network. A refusal from
Telegram is logged and swallowed.

What gets sent
~~~~~~~~~~~~~~

One message per agent per run, listing that run's actions. A run where
the watched agent did nothing sends nothing, so silence carries meaning:
the agent held. An agent that errored is reported, because a book that
stopped trading for a bad reason looks exactly like a book that chose to
hold, and the difference is the whole point of watching.

Telegram will not let a bot open a conversation, so the chat has to be
started from the phone before the first message can arrive. Nothing in
this repository can arrange that, but :func:`check_channel` can tell
whether it has happened — which is the difference between "your chat id
is wrong" and "you have not opened the bot yet", two faults that produce
the same refusal.

Nothing here may print a credential. The bot token is a path segment of
every request URL, so ``urllib``'s own error messages carry it, and the
chat id is kept in a secret; both are stripped by :func:`redact`, which
every reported string goes through.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from core.logger import get_logger
from core.text import titlecase

logger = get_logger("core.live.notify")

#: Telegram's send endpoint. The bot token goes in the path, which is why
#: nothing in this module logs a URL.
API_ROOT = "https://api.telegram.org"

#: What a bot token looks like: a numeric bot id, a colon, then the
#: secret. The bounds are deliberately loose — Telegram documents no
#: lengths, and this pattern is here to recognise a value that is not a
#: token at all, not to validate one that is.
_TOKEN_SHAPE = re.compile(r"\A[0-9]{5,}:[A-Za-z0-9_-]{20,}\Z")

#: A chat id is a signed integer — negative for groups and channels — or
#: an ``@name`` for a public channel.
_CHAT_SHAPE = re.compile(r"\A(-?[0-9]{5,}|@[A-Za-z0-9_]{4,})\Z")

#: Characters that cannot occur in a bot token. One of these turning up
#: can be named in a report: by definition it is not part of a real
#: token, so naming it discloses nothing.
_NOT_IN_TOKEN = re.compile(r"[^A-Za-z0-9_:-]")

#: Seconds to wait on the HTTP call. A notification is never worth
#: holding a run open for, and the runner has already finished its work
#: by the time this is called.
TIMEOUT_SECONDS = 10

#: Which books to report on. One agent today, by request: Graham leads
#: the live book and is the one being followed closely. A set rather
#: than a constant so adding a second is a one-line change here.
WATCHED_AGENTS: frozenset[str] = frozenset({"benjamin_graham"})


class _Result(Protocol):
    """The part of ``AgentRunResult`` this module reads.

    Structural, so the tests need no runner and this module needs no
    import from it — the dependency would be circular.
    """

    agent: str
    error: str | None
    skipped: bool

    @property
    def trades(self) -> list[Any]: ...


class TelegramSendError(Exception):
    """A refusal from Telegram, named without its URL.

    ``urllib.error.HTTPError`` puts the request URL in its message, and
    for this API the bot token is a path segment of that URL — so the
    obvious exception type is the one that cannot be raised here.
    """


@dataclass(frozen=True)
class TelegramConfig:
    """Bot credentials. Never logged, never included in an exception."""

    token: str
    chat_id: str


def read_config(env: Mapping[str, str]) -> TelegramConfig | None:
    """Credentials from the environment, or ``None`` when unconfigured.

    Both halves are required: a token without a chat id has nowhere to
    send, and a chat id without a token cannot authenticate. Returning
    ``None`` for a partial configuration keeps the failure at startup
    rather than producing a 401 on every run.
    """
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return None
    return TelegramConfig(token=token, chat_id=chat_id)


#: What a redacted secret is replaced with. Visible on purpose: a line
#: that silently dropped the value would read as though Telegram had
#: returned nothing there.
REDACTED = "[redacted]"


def redact(text: str, config: TelegramConfig) -> str:
    """``text`` with the credentials removed, for a log line or stdout.

    Every string this module reports passes through here. ``urllib``
    names the request URL in its error messages, and for this API the bot
    token is a path segment of that URL — so a line that looks like a
    bare status can carry the credential into the CI log of a public
    repository. Redacting where the text is produced, rather than
    trusting each call site to remember, means a log line added later
    cannot leak it by omission.

    The chat id goes the same way and for the same reason: it is kept in
    a secret, and a public log is public. It is only substituted when it
    is long enough to be unambiguous, so a short or malformed value
    cannot turn every digit in the text into a redaction.
    """
    for secret in (config.token.strip(), config.chat_id.strip()):
        if len(secret) >= 5:
            text = text.replace(secret, REDACTED)
    return text


def shape_complaints(config: TelegramConfig) -> list[str]:
    """Structural faults in the credentials, described without quoting them.

    A 404 from ``getMe`` means the token did not resolve to a bot, and
    ``getMe`` takes no arguments — so the token is the whole fault and
    the chat id is not implicated at all. What it does not say is *how*
    the token is wrong, and measured against the live API the common
    ways all turn out to be shape rather than value: a wrong-but-
    well-formed token answers 401, while the ``bot`` prefix copied in
    from the URL, the secret's own name pasted along with its value, and
    a value with no colon all answer 404.

    Shape is checkable with no network and no disclosure. A length, a
    missing colon, or a character that cannot occur in a token are facts
    about the value rather than the value, and only characters outside a
    token's own alphabet are ever printed — those cannot have come from
    a real one.
    """
    complaints: list[str] = []
    token = config.token.strip()
    chat_id = config.chat_id.strip()

    if token.lower().startswith("bot"):
        complaints.append(
            "TELEGRAM_BOT_TOKEN starts with 'bot'. That prefix belongs to "
            "the request URL, not to the secret — store the token alone."
        )
    if "://" in token or "api.telegram.org" in token:
        complaints.append(
            "TELEGRAM_BOT_TOKEN contains a URL. Store only the value "
            "BotFather sent, not the address it gets placed into."
        )
    if "=" in token:
        complaints.append(
            "TELEGRAM_BOT_TOKEN contains an '='. A secret holds the value "
            "alone; the NAME=value form belongs in a .env file, and the "
            "name must not be part of what is stored here."
        )

    if ":" not in token:
        complaints.append(
            "TELEGRAM_BOT_TOKEN has no ':' in it. A bot token is a numeric "
            f"id, a colon, then the secret — this value is {len(token)} "
            "characters long with no colon anywhere in it."
        )
    elif not _TOKEN_SHAPE.match(token):
        head, _, tail = token.partition(":")
        digits = "all digits" if head.isdigit() else "not all digits"
        complaints.append(
            "TELEGRAM_BOT_TOKEN is not shaped like a bot token: "
            f"{len(head)} character(s) before the colon ({digits}) and "
            f"{len(tail)} after it, where a token has roughly 10 and 35."
        )

    stray = sorted(set(_NOT_IN_TOKEN.findall(token)))
    if stray:
        shown = " ".join(repr(character) for character in stray)
        complaints.append(
            f"TELEGRAM_BOT_TOKEN contains {len(stray)} kind(s) of character "
            f"that cannot appear in a bot token: {shown}"
        )

    if ":" in chat_id:
        complaints.append(
            "TELEGRAM_CHAT_ID contains a ':', which is what a bot token "
            "looks like and a chat id never does. Check whether the two "
            "secrets were put in each other's boxes."
        )
    elif not _CHAT_SHAPE.match(chat_id):
        complaints.append(
            "TELEGRAM_CHAT_ID is not shaped like a chat id: a chat id is "
            "digits, negative for a group, or an @name for a public "
            f"channel — this value is {len(chat_id)} characters long."
        )

    return complaints


#: What ends the trade, per sleeve, from the playbook's §7.1 rather than
#: from the decision log's English prose. Translating that prose would be
#: guesswork; the mode is a recorded enum, so the Hebrew is written once
#: against the rule it describes.
_PLAN_BY_MODE: Mapping[str, str] = {
    "DEFENSIVE": (
        "מוכר כשהמחיר מגיע למספר גרהם. "
        "יוצא גם אם P/E או P/B חוזרים מעל הרף, או שהיסודות מתדרדרים."
    ),
    "NET_NET": (
        "מוכר כשהמחיר מגיע ל-NCAV המלא — רווח של כ-50% מקניית ⅔. "
        "יוצא גם אחרי שנתיים בלי התקדמות, או אם היסודות מתדרדרים."
    ),
}

#: The name of the level each sleeve sells at.
_TARGET_LABEL_BY_MODE: Mapping[str, str] = {
    "DEFENSIVE": "מספר גרהם",
    "NET_NET": "NCAV למניה",
}

#: Where in ``criteria_values`` each sleeve records that level.
#:
#: The sell target reached ``target_price`` only recently, and every
#: Defensive row written before that carries nothing in the field while
#: recording the Graham Number here — 1,336 rows of it. Reading the
#: agent's own figure from where the agent put it keeps those messages
#: whole without the formatter computing a price of its own.
#:
#: Keyed on the same modes as the two mappings above, and a test holds
#: the three key sets identical: a mode present in one and missing from
#: another renders half a plan.
_TARGET_KEY_BY_MODE: Mapping[str, str] = {
    "DEFENSIVE": "graham_number",
    "NET_NET": "ncav_per_share",
}

#: A key whose presence in the same row marks it as written after that
#: sleeve's level was recorded under the right name.
#:
#: The Defensive path once wrote ``pe * pb`` — a dimensionless product —
#: under the key ``graham_number``, which is a price per share. 1,031
#: journal rows still hold it that way, and reading one as a sell target
#: renders "$6.72" against a stock bought at $4,337. ``pe_times_pb``
#: arrived in the same change that fixed the key, which makes its
#: presence an exact witness: of all 1,336 Defensive rows, every row
#: without it has ``graham_number == pe * pb`` to the cent, and every
#: row with it carries the real sqrt(22.5 x EPS x BVPS) — checked
#: against the point-in-time financials for the row's own date.
#:
#: A sleeve absent from this mapping needs no witness. Net-net is
#: absent because it always recorded ``target_price`` directly, so its
#: fallback never fires on a row old enough to be suspect.
_FALLBACK_WITNESS_BY_MODE: Mapping[str, str] = {
    "DEFENSIVE": "pe_times_pb",
}


def _money(value: float) -> str:
    return f"${value:,.2f}"


def format_trade(trade: Any) -> str:
    """One line for one executed trade.

    Plain text, deliberately: Telegram's Markdown and HTML modes both
    need escaping, and a ticker or an agent name that happened to carry
    a reserved character would either break the message or be silently
    dropped by the parser. There is nothing here worth that risk.
    """
    side = str(getattr(trade, "side", "?")).upper()
    ticker = str(getattr(trade, "ticker", "?"))
    shares = float(getattr(trade, "shares", 0.0) or 0.0)
    price = float(getattr(trade, "price", 0.0) or 0.0)
    gross = float(getattr(trade, "gross_value", 0.0) or 0.0)
    line = f"{side} {ticker}  {shares:,.4f} @ {_money(price)}  = {_money(gross)}"

    realized = getattr(trade, "realized_pnl_usd", None)
    if side == "SELL" and realized is not None:
        realized = float(realized)
        sign = "+" if realized >= 0 else "−"
        line += f"  (realised {sign}{_money(abs(realized))})"
    return line


#: The state-of-incorporation suffix EDGAR appends to a registered name
#: — "SEABOARD CORP /DE/". It identifies the filing entity and means
#: nothing to a reader of a trade notification.
_STATE_SUFFIX = re.compile(r"\s*/[A-Z]{2}(?:/)?\s*$")


def clean_company_name(raw: str) -> str:
    """A registered name as a reader would write it.

    EDGAR stores names shouted and suffixed. Case is only folded when the
    stored name is entirely upper case: some are already mixed
    ("Ingredion Inc"), and title-casing those would be a round trip
    through a worse spelling.
    """
    name = _STATE_SUFFIX.sub("", raw).strip()
    if name and name == name.upper():
        name = titlecase(name)
    return name


def _company_line(ticker: str, *, names: Mapping[str, str], industry: str | None) -> str:
    """Who the company is, in one line.

    Name and industry, both quoted from filings — the company's own
    registered name and the SEC's classification of its primary
    business. Nothing here is generated prose, so nothing here can be
    wrong in the way a generated sentence can.
    """
    raw = names.get(ticker.upper(), "").strip()
    parts = [clean_company_name(raw) if raw else "", (industry or "").strip()]
    body = " · ".join(p for p in parts if p)
    return body or ticker


def format_action(
    trade: Any,
    decision: Any | None,
    *,
    actor: str,
    names: Mapping[str, str],
    industry: str | None,
) -> str:
    """One executed trade, with what triggered it and what ends it.

    The trade record carries the fill; everything a reader actually wants
    — which test the company passed, at what level the position is meant
    to close — is in the decision the same run wrote to the journal. The
    two are joined on the ticker rather than restated, so the message
    cannot disagree with the audit trail.

    ``decision`` may be ``None``: a trade without a matching journal
    entry still reports the fill rather than being dropped.
    """
    side = str(getattr(trade, "side", "?")).upper()
    ticker = str(getattr(trade, "ticker", "?"))
    shares = float(getattr(trade, "shares", 0.0) or 0.0)
    price = float(getattr(trade, "price", 0.0) or 0.0)
    gross = float(getattr(trade, "gross_value", 0.0) or 0.0)

    verb = "קנה" if side == "BUY" else "מכר"
    lines = [
        # The actor is passed in, never hard-coded: this module is the
        # channel for every agent, and the subscription list is one set.
        # A fixed name here would have had Buffett's trades announced
        # under Graham's.
        f"{actor} {verb} {ticker}",
        _company_line(ticker, names=names, industry=industry),
        "",
        f"{'קנייה' if side == 'BUY' else 'מכירה'}   "
        f"{shares:,.4f} @ {_money(price)}  =  {_money(gross)}",
    ]

    realized = getattr(trade, "realized_pnl_usd", None)
    if side == "SELL" and realized is not None:
        realized = float(realized)
        word = "רווח" if realized >= 0 else "הפסד"
        lines.append(f"{word} מומש   {_money(abs(realized))}")

    if decision is None:
        return "\n".join(lines)

    met = list(getattr(decision, "criteria_met", None) or [])
    # The mode is logged as an enum, which is what makes the plan below
    # a lookup instead of a translation.
    values = dict(getattr(decision, "criteria_values", None) or {})
    mode = str(values.get("mode") or "")
    target = getattr(decision, "target_price", None)
    if target is None:
        # The level the agent recorded, not one derived here — and only
        # from a row that recorded it under the right name.
        key = _TARGET_KEY_BY_MODE.get(mode)
        witness = _FALLBACK_WITNESS_BY_MODE.get(mode)
        if key is not None and (witness is None or witness in values):
            try:
                target = float(values[key])
            except (KeyError, TypeError, ValueError):
                target = None

    if met:
        # Drop the bookkeeping entries — "mode=DEFENSIVE" is already the
        # heading for the plan, and repeating it reads as a criterion.
        shown = [c for c in met if not str(c).lower().startswith("mode=")]
        if shown:
            # The field holds different things on the way in and on the
            # way out. A buy lists the tests the company passed; an exit
            # row carries the reason it was closed ("left today's target
            # list"), so labelling both "what it spotted" would describe
            # a sale by the criteria that justified the purchase.
            heading = "מה זיהה" if side == "BUY" else "למה יצא"
            lines += ["", heading] + [f"  {c}" for c in shown]

    if side == "BUY" and target is not None:
        target = float(target)
        label = _TARGET_LABEL_BY_MODE.get(mode, "יעד")
        upside = ((target / price) - 1.0) * 100.0 if price else None
        plan = [
            "",
            "התוכנית",
            f"  יעד מכירה   {_money(target)}  ({label})"
            + (f"  {upside:+.1f}%" if upside is not None else ""),
        ]
        rule = _PLAN_BY_MODE.get(mode)
        if rule:
            plan.append(f"  {rule}")
        lines += plan

    return "\n".join(lines)


def format_result(
    result: _Result,
    *,
    as_of: date,
    display_name: str | None = None,
    decisions: Mapping[str, Any] | None = None,
    names: Mapping[str, str] | None = None,
    industry: Callable[[str], str | None] | None = None,
) -> str | None:
    """The message for one agent's run, or ``None`` when there is nothing to say.

    Nothing to say means the agent neither traded nor failed. Sending
    "no change" after every run would train the reader to ignore the
    channel, which costs exactly the attention the channel exists to buy.

    ``decisions`` maps an upper-case ticker to the journal entry the same
    run wrote for it; ``names`` and ``industry`` supply the company line.
    All three are injected so the formatter touches no disk and the tests
    need no fixtures — and so a missing lookup degrades to a shorter
    message rather than to no message.
    """
    # Derived from the slug rather than read from the dashboard registry:
    # that registry is TypeScript, and duplicating its labels in Python
    # would give two places to change a name and one of them to forget.
    # Derived from the slug rather than read from the dashboard registry:
    # that registry is TypeScript, and duplicating its labels in Python
    # would give two places to change a name and one of them to forget.
    # It also means a new agent needs no entry here at all.
    actor = display_name or result.agent.replace("_", " ").title()
    name = actor
    trades = list(result.trades or [])

    if result.error:
        # Reported even though it is not an action: a book that stopped
        # trading because of a fault is indistinguishable, from the
        # outside, from a book that decided to hold.
        return f"{name} — {as_of.isoformat()}\n\nההרצה נכשלה: {result.error}"

    if not trades:
        return None

    by_ticker = dict(decisions or {})
    name_map = dict(names or {})
    industry_of = industry or (lambda _ticker: None)

    blocks = [
        format_action(
            trade,
            by_ticker.get(str(getattr(trade, "ticker", "")).upper()),
            actor=actor,
            names=name_map,
            industry=industry_of(str(getattr(trade, "ticker", ""))),
        )
        for trade in trades
    ]

    # Date only. Each action block names the agent itself, so that a
    # message Telegram truncates still says who acted.
    header = as_of.isoformat()
    body = "\n\n———\n\n".join(blocks)

    portfolio = getattr(result, "portfolio", None)
    footer = ""
    if portfolio is not None:
        nav = getattr(portfolio, "total_nav", None)
        ret = getattr(portfolio, "cumulative_return_pct", None)
        if nav is not None and ret is not None:
            footer = (
                f"\n\n———\n\nהתיק   {_money(float(nav))}  "
                f"({float(ret):+.2f}% מההתחלה)"
            )
    return f"{header}\n\n{body}{footer}"


def _decisions_for(agent: str, as_of: date) -> Mapping[str, Any]:
    """The journal entries this run wrote for ``agent``, keyed by ticker.

    Imported inside the function: ``core.live.runner`` imports this
    module, and the decision logger reaches back into the live package,
    so a module-level import closes a cycle. Failure returns an empty
    mapping — a message without the plan is worth more than no message.
    """
    try:
        from core.backtest.decision_logger import DecisionLogger
        from core.paths import decisions_dir

        rows = DecisionLogger(root=decisions_dir()).read_day(agent, as_of)
    except Exception as exc:  # noqa: BLE001 — enrichment is optional
        logger.debug(f"could not read decisions for {agent}: {type(exc).__name__}")
        return {}
    # Last writer wins per ticker. A day with both a SELL and a later BUY
    # of one name is not something Graham does, and if it happened the
    # trade list would carry both fills anyway.
    return {str(r.ticker).upper(): r for r in rows}


def _company_names() -> Mapping[str, str]:
    """Ticker → registered company name, or empty when unavailable.

    Reads the cache file and never fetches. ``load_or_fetch`` refreshes
    from SEC once its TTL expires, which would put a 10,000-name download
    on whichever run happened to notify first — a notifier has no
    business doing network work beyond the message it sends. A cold cache
    costs the company line, not the message.
    """
    try:
        import json as _json

        from core.data.company_names import DEFAULT_CACHE_PATH

        if not DEFAULT_CACHE_PATH.exists():
            return {}
        return dict(_json.loads(DEFAULT_CACHE_PATH.read_text()))
    except Exception as exc:  # noqa: BLE001 — enrichment is optional
        logger.debug(f"could not load company names: {type(exc).__name__}")
        return {}


def _industry_title(ticker: str) -> str | None:
    """The SEC's classification of the company's primary business."""
    try:
        from core.data.sic_codes import industry_title

        return industry_title(ticker)
    except Exception as exc:  # noqa: BLE001 — enrichment is optional
        logger.debug(f"could not resolve industry for {ticker}: {type(exc).__name__}")
        return None


def _explanation(parsed: Any, raw: str, config: TelegramConfig) -> str:
    """Telegram's own account of a refusal, or the body when it gave none."""
    if isinstance(parsed, Mapping):
        description = parsed.get("description")
        if isinstance(description, str) and description.strip():
            return redact(description.strip(), config)
    return redact(raw[:200].strip() or "no explanation given", config)


def _refusal(exc: urllib.error.HTTPError, config: TelegramConfig) -> str:
    """The explanation inside an error response.

    ``HTTPError`` is both an exception and the response it describes, so
    the body is readable from it — and the body is where Telegram puts
    the only text that tells a wrong chat id apart from a wrong token.
    """
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — an unreadable body is not a new failure
        return "no response body"
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    return _explanation(parsed, raw, config)


def _call(
    config: TelegramConfig,
    method: str,
    *,
    body: Mapping[str, Any] | None = None,
) -> Any:
    """One Telegram API call, returning its ``result``.

    Every failure becomes a :class:`TelegramSendError` carrying the
    status and Telegram's own ``description``, because that sentence is
    the whole of the diagnosis: "chat not found", "Unauthorized" and
    "bot was blocked by the user" are three different problems with three
    different fixes, and neither the exception type nor the status alone
    tells them apart. Reporting only the type cost a failed run to learn.
    """
    url = f"{API_ROOT}/bot{config.token}/{method}"
    data = json.dumps(dict(body)).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"content-type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # ``from None`` deliberately: a chained HTTPError would put the
        # request URL, and so the token, into any traceback printed from
        # the exception this raises.
        raise TelegramSendError(
            f"{method} returned {exc.code}: {_refusal(exc, config)}"
        ) from None
    except urllib.error.URLError as exc:
        raise TelegramSendError(
            f"{method} could not reach telegram: {redact(str(exc.reason), config)}"
        ) from None

    try:
        parsed = json.loads(raw)
    except ValueError:
        raise TelegramSendError(
            f"{method} returned something other than JSON: "
            f"{redact(raw[:200], config)}"
        ) from None
    # A 200 with ``ok: false`` is a real Telegram answer, not a transport
    # fault, and it carries the same description an error status would.
    if not isinstance(parsed, Mapping) or not parsed.get("ok"):
        raise TelegramSendError(
            f"{method} refused: {_explanation(parsed, raw, config)}"
        )
    return parsed.get("result")


def send_message(config: TelegramConfig, chat_id: str, text: str) -> None:
    """Send ``text`` to one chat. Raises :class:`TelegramSendError`.

    The chat is a parameter rather than ``config.chat_id`` so that a
    diagnosis can reach a chat the configuration does not name — which
    is the interesting case, because a chat id that is merely wrong
    delivers nowhere at all and so cannot report itself.
    """
    _call(
        config,
        "sendMessage",
        body={
            "chat_id": chat_id,
            "text": text,
            # The dashboard link would be the only thing worth previewing
            # and it is behind a password, so a preview card would show a
            # login prompt in the chat.
            "disable_web_page_preview": True,
        },
    )


def _post(config: TelegramConfig, text: str) -> None:
    """Send one message to the configured chat; the caller swallows."""
    send_message(config, config.chat_id, text)


@dataclass(frozen=True)
class ChannelCheck:
    """What Telegram says about this configuration.

    ``chats`` holds real chat ids. They are credential-adjacent — kept in
    a secret, and a public CI log is public — so they are meant to be
    handed to :func:`send_message`, never printed. :func:`redact` removes
    the configured one from any text; the others have to be kept out of
    reports by hand.
    """

    bot: str
    chats: tuple[str, ...]
    configured_found: bool


#: Where an update carries the chat it happened in. ``my_chat_member`` is
#: included because pressing Start produces one, and a user who has only
#: ever pressed Start is exactly the case worth detecting.
_CHAT_BEARING = ("message", "edited_message", "channel_post", "my_chat_member")


def check_channel(
    config: TelegramConfig,
    *,
    call: Callable[..., Any] | None = None,
) -> ChannelCheck:
    """Who the bot is, and which chats it is actually allowed to write to.

    Telegram will not let a bot open a conversation: the chat has to be
    started from the phone before anything can be delivered. So a send
    can fail for a reason that lives entirely outside this repository,
    and from the refusal alone a wrong chat id and a chat that was never
    started look the same.

    ``getUpdates`` separates them. The chats that have written to the bot
    are the chats it may write back to, so their presence or absence is
    the answer. It reaches back about 24 hours, which means an empty list
    says "nobody has written recently", not "nobody ever did".
    """
    api = call or _call

    me = api(config, "getMe")
    username = str(me.get("username") or "") if isinstance(me, Mapping) else ""

    updates = api(config, "getUpdates")
    chats: list[str] = []
    for update in updates if isinstance(updates, list) else []:
        if not isinstance(update, Mapping):
            continue
        for key in _CHAT_BEARING:
            payload = update.get(key)
            if not isinstance(payload, Mapping):
                continue
            chat = payload.get("chat")
            if not isinstance(chat, Mapping) or chat.get("id") is None:
                continue
            chat_id = str(chat["id"])
            if chat_id not in chats:
                chats.append(chat_id)

    return ChannelCheck(
        bot=f"@{username}" if username else "(unnamed bot)",
        chats=tuple(chats),
        configured_found=config.chat_id.strip() in chats,
    )


def notify_run(
    results: Iterable[_Result],
    *,
    as_of: date,
    env: Mapping[str, str],
    agents: frozenset[str] = WATCHED_AGENTS,
    agent_names: Mapping[str, str] | None = None,
    decisions_for: Callable[[str, date], Mapping[str, Any]] | None = None,
    company_names: Mapping[str, str] | None = None,
    industry: Callable[[str], str | None] | None = None,
    send: Callable[[TelegramConfig, str], None] | None = None,
) -> int:
    """Report the watched agents' actions. Returns the number of messages sent.

    Never raises. A notification that fails must not turn a completed run
    into a failed one, so every error path here ends in a log line.

    The four lookups default to the real sources and are injectable, so
    the tests exercise the formatting without a journal, a cache or a
    network, and a run whose journal or caches are unreadable still sends
    the fill rather than nothing.
    """
    config = read_config(env)
    if config is None:
        # The normal state of a checkout without credentials.
        logger.debug("telegram not configured; skipping notifications")
        return 0

    read_decisions = decisions_for or _decisions_for
    name_map = company_names if company_names is not None else _company_names()
    industry_of = industry or _industry_title

    sender = send or _post
    sent = 0
    for result in results:
        if result.agent not in agents:
            continue
        if getattr(result, "skipped", False):
            # The run was a no-op replay of a day already completed.
            continue
        text = format_result(
            result,
            as_of=as_of,
            display_name=(agent_names or {}).get(result.agent),
            decisions=read_decisions(result.agent, as_of),
            names=name_map,
            industry=industry_of,
        )
        if text is None:
            continue
        try:
            sender(config, text)
            sent += 1
        except Exception as exc:  # noqa: BLE001 — a notifier may not fail a run
            # Through `redact`, always. The text is worth having — the
            # status and Telegram's own "chat not found" are the whole
            # diagnosis, and the type name alone sent a reader back to
            # guessing — but a urllib message for a bad token is the
            # request URL, and the token is a path segment of it.
            logger.warning(
                f"telegram notification for {result.agent} failed: "
                f"{type(exc).__name__}: {redact(str(exc), config)}"
            )
    return sent


__all__ = [
    "API_ROOT",
    "REDACTED",
    "WATCHED_AGENTS",
    "ChannelCheck",
    "TelegramConfig",
    "TelegramSendError",
    "check_channel",
    "clean_company_name",
    "format_action",
    "format_result",
    "format_trade",
    "notify_run",
    "read_config",
    "redact",
    "send_message",
    "shape_complaints",
]
