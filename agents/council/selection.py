"""What the Council buys, and everything that stops it.

Until now this package could say when *not* to buy — a regime dial, hard
position limits, a lifetime punch card, the filings of what is held —
and had no way to say what to buy at all. A human supplied that half.
With the human removed the agent had risk controls and no selection
rule, so it sat in cash indefinitely. This is the missing half.

The rule
--------

It buys what its members already agree on. A name held independently by
several of the other agents has cleared several different value
doctrines — Graham's balance sheet, Greenblatt's returns on capital,
Schloss's discount to book — and agreement across tests that do not
share a premise is worth more than any one of them. That is what a
council is, and it is why this agent is not a twelfth screen: it owns no
opinion about a company that its members have not already formed.

Everything after that is a veto. Consensus proposes; the doctrine
disposes.

  1. Agreement    held by at least MIN_AGREEMENT of the eleven
  2. Regime       fewer than 2 of 4 dials risk-on blocks new entries
  3. Filings      a terminal form or a critical 8-K item vetoes
  4. News         a critical headline in the last week vetoes
  5. Punch card   twenty entries in its lifetime, and no more
  6. Limits       position cap and cash floor size what survives

What this is not
----------------

It is not an alpha thesis, and it is not presented as one. It is a
disciplined way to act on work already done, with more ways to say no
than yes. The expected outcome is a concentrated book that changes
rarely and spends long stretches in cash, which is what the doctrine
asked for in the first place.

Nothing here reads a price chart. A name enters because other doctrines
concluded it was cheap and nothing since has contradicted them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from agents.council.limits import MAX_POSITION_AT_ENTRY, MIN_CASH
from core.logger import get_logger

logger = get_logger("agents.council.selection")

#: How many of the eleven must hold a name before it is a candidate.
#: Two is a coincidence often enough to matter; three independent value
#: doctrines landing on the same company is a signal. Above four the
#: candidate set empties out — the agents are deliberately different.
MIN_AGREEMENT: int = 3

#: Risk-on dials required before a new position may be opened. The
#: regime module scores four and never counts an unreadable one as
#: risk-on, so a FRED outage tightens this rather than loosening it.
MIN_RISK_ON_DIALS: int = 2

#: How far back the news veto looks.
NEWS_LOOKBACK = timedelta(days=7)

#: Headline terms that stop an entry outright. Deliberately short and
#: unambiguous: each names an event that changes what a company is,
#: not an opinion about its price. A longer list would start vetoing
#: on sentiment, which is not what this gate is for.
CRITICAL_HEADLINE_TERMS: tuple[str, ...] = (
    "bankruptcy",
    "chapter 11",
    "chapter 7",
    "delisting",
    "delisted",
    "accounting fraud",
    "restatement",
    "sec investigation",
    "going concern",
    "receivership",
    "liquidation",
)


@dataclass
class Veto:
    """Why a candidate did not become a position."""

    ticker: str
    gate: str
    detail: str


@dataclass
class Proposal:
    """The outcome of one selection, kept whole for the journal."""

    weights: dict[str, float] = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)
    vetoes: list[Veto] = field(default_factory=list)
    entries_used: int = 0
    note: str = ""


def agreement(
    books: Mapping[str, Sequence[str]], *, exclude: str = "the_council"
) -> dict[str, int]:
    """How many agents hold each ticker.

    Args:
        books: Agent slug -> the tickers it holds.
        exclude: The Council's own slug, so its existing book does not
            vote for itself and manufacture its own consensus.
    """
    counts: dict[str, int] = {}
    for slug, tickers in books.items():
        if slug == exclude:
            continue
        for t in {x.upper() for x in tickers}:
            counts[t] = counts.get(t, 0) + 1
    return counts


def news_veto(items: Sequence[Any]) -> str | None:
    """The term that blocks an entry, or None.

    Matches on the headline only. Article bodies are not fetched and
    summaries arrive from three sources in three shapes, so a match
    there would be neither consistent nor checkable.
    """
    for item in items:
        title = (getattr(item, "title", "") or "").lower()
        for term in CRITICAL_HEADLINE_TERMS:
            if term in title:
                return term
    return None


def propose(
    *,
    as_of: date,
    books: Mapping[str, Sequence[str]],
    held: Sequence[str] = (),
    risk_on_dials: int | None = None,
    entries_remaining: int = 20,
    filings_flagged: Mapping[str, str] | None = None,
    news_for=None,
    min_agreement: int = MIN_AGREEMENT,
) -> Proposal:
    """Run the gates and return target weights.

    Args:
        as_of: The decision date.
        books: What every agent holds, for the agreement count.
        held: What the Council already holds. Existing positions are
            kept through a risk-off regime; the dial blocks entries, not
            ownership.
        risk_on_dials: How many of the four read risk-on. ``None`` means
            the dial could not be read, which blocks entries — an
            unreadable regime is not a green one.
        entries_remaining: Punch card. Zero ends new entries for good.
        filings_flagged: Ticker -> why its filings disqualify it.
        news_for: ``(ticker, as_of) -> list[NewsItem]``, or None to skip
            the news gate. Passed in rather than constructed so this
            function stays pure and testable.
        min_agreement: Override for the agreement threshold.

    Returns:
        A :class:`Proposal`. ``weights`` covers holds and new entries
        alike; the residual is cash.
    """
    proposal = Proposal()
    holding = [t.upper() for t in held]
    flagged = {k.upper(): v for k, v in (filings_flagged or {}).items()}

    counts = agreement(books)
    candidates = sorted(
        (t for t, n in counts.items() if n >= min_agreement),
        key=lambda t: (-counts[t], t),
    )
    proposal.candidates = candidates

    # Holdings are re-examined on the same gates that let them in, minus
    # agreement: an agent that sells is expressing its own doctrine's
    # exit rule, not a verdict the Council must copy.
    keep: list[str] = []
    for t in holding:
        if t in flagged:
            proposal.vetoes.append(Veto(t, "filings", flagged[t]))
            continue
        keep.append(t)

    entries_allowed = max(0, entries_remaining)
    if risk_on_dials is None:
        proposal.note = "regime unreadable — no new entries"
        entries_allowed = 0
    elif risk_on_dials < MIN_RISK_ON_DIALS:
        proposal.note = (
            f"{risk_on_dials}/4 dials risk-on, below {MIN_RISK_ON_DIALS}"
            " — no new entries"
        )
        entries_allowed = 0

    opened: list[str] = []
    for t in candidates:
        if len(opened) >= entries_allowed:
            break
        if t in keep:
            continue
        if t in flagged:
            proposal.vetoes.append(Veto(t, "filings", flagged[t]))
            continue
        if news_for is not None:
            term = news_veto(news_for(t, as_of))
            if term:
                proposal.vetoes.append(Veto(t, "news", f"headline mentions {term!r}"))
                continue
        opened.append(t)

    book = keep + opened
    proposal.entries_used = len(opened)

    if not book:
        proposal.weights = {}
        return proposal

    # Equal weight, capped at the entry limit, with the cash floor left
    # untouched. Sizing by conviction would need a conviction score and
    # the Council has none: it did not form the opinions it is acting on.
    investable = 1.0 - MIN_CASH
    each = min(MAX_POSITION_AT_ENTRY, investable / len(book))
    proposal.weights = {t: each for t in sorted(book)}

    logger.info(
        f"council proposal {as_of}: {len(candidates)} candidates, "
        f"{len(book)} positions ({len(opened)} new), "
        f"{len(proposal.vetoes)} vetoed"
    )
    return proposal
