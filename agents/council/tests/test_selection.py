"""The gates must each be able to say no, and say it for the right reason.

Every test injects its inputs — books, dials, filings, news — so nothing
here touches a network or a clock. The point of this agent is that it
refuses more often than it acts, and a test suite that only checked the
happy path would not notice if a veto quietly stopped working.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from agents.council.limits import MAX_POSITION_AT_ENTRY, MIN_CASH
from agents.council.selection import (
    MIN_AGREEMENT,
    Proposal,
    agreement,
    news_veto,
    propose,
)

AS_OF = date(2026, 8, 14)


@dataclass
class FakeNews:
    title: str


def books(**kw: list[str]) -> dict[str, list[str]]:
    return dict(kw)


def three_agree_on(ticker: str) -> dict[str, list[str]]:
    return books(
        benjamin_graham=[ticker],
        walter_schloss=[ticker],
        seth_klarman=[ticker],
        peter_lynch=["ZZZZ"],
    )


def run(**kw) -> Proposal:
    base = dict(as_of=AS_OF, books=three_agree_on("AAA"), risk_on_dials=4)
    return propose(**{**base, **kw})


# ------------------------------------------------------------ agreement


def test_counts_each_agent_once_per_ticker() -> None:
    counts = agreement(books(a=["AAA", "AAA"], b=["AAA"]))
    assert counts["AAA"] == 2


def test_mohnish_pabrai_does_not_vote_for_itself() -> None:
    """Otherwise its own book manufactures the consensus that justifies it."""
    counts = agreement(books(mohnish_pabrai=["AAA"], benjamin_graham=["AAA"]))
    assert counts["AAA"] == 1


def test_below_the_threshold_is_not_a_candidate() -> None:
    p = propose(
        as_of=AS_OF,
        books=books(benjamin_graham=["AAA"], walter_schloss=["AAA"]),
        risk_on_dials=4,
    )
    assert p.candidates == []
    assert p.weights == {}


def test_at_the_threshold_it_buys() -> None:
    p = run()
    assert p.candidates == ["AAA"]
    assert set(p.weights) == {"AAA"}
    assert p.entries_used == 1


# ---------------------------------------------------------------- regime


def test_a_risk_off_dial_blocks_new_entries() -> None:
    p = run(risk_on_dials=1)
    assert p.weights == {}
    assert "no new entries" in p.note


def test_an_unreadable_dial_blocks_too() -> None:
    """A FRED outage must not read as permission."""
    p = run(risk_on_dials=None)
    assert p.weights == {}
    assert "unreadable" in p.note


def test_risk_off_keeps_what_is_already_held() -> None:
    """The dial governs entries, not ownership."""
    p = run(risk_on_dials=0, held=["BBB"])
    assert set(p.weights) == {"BBB"}
    assert p.entries_used == 0


# --------------------------------------------------------------- filings


def test_a_flagged_filing_vetoes_an_entry() -> None:
    p = run(filings_flagged={"AAA": "Form 25 — delisting"})
    assert p.weights == {}
    assert [(v.gate, v.ticker) for v in p.vetoes] == [("filings", "AAA")]


def test_a_flagged_filing_also_exits_a_holding() -> None:
    """The gate that refuses entry cannot tolerate the same thing held."""
    p = run(books=books(), held=["BBB"], filings_flagged={"BBB": "Form 15"})
    assert p.weights == {}
    assert p.vetoes[0].gate == "filings"


# ------------------------------------------------------------------ news


@pytest.mark.parametrize(
    "headline",
    [
        "Acme files for Chapter 11 protection",
        "Nasdaq begins DELISTING proceedings",
        "Regulator opens SEC investigation into Acme",
        "Auditor flags going concern doubt",
    ],
)
def test_a_critical_headline_vetoes(headline: str) -> None:
    assert news_veto([FakeNews(headline)]) is not None


def test_ordinary_news_does_not_veto() -> None:
    assert news_veto([FakeNews("Acme beats earnings, raises guidance")]) is None


def test_the_news_gate_stops_the_entry() -> None:
    p = run(news_for=lambda t, d: [FakeNews("Acme enters bankruptcy")])
    assert p.weights == {}
    assert p.vetoes[0].gate == "news"
    assert "bankruptcy" in p.vetoes[0].detail


def test_no_news_service_skips_the_gate_rather_than_blocking() -> None:
    """An unconfigured feed must not silently halt all buying."""
    p = run(news_for=None)
    assert set(p.weights) == {"AAA"}


def test_an_empty_feed_is_not_a_veto() -> None:
    p = run(news_for=lambda t, d: [])
    assert set(p.weights) == {"AAA"}


# ------------------------------------------------------------ punch card


def test_an_empty_punch_card_ends_new_entries() -> None:
    p = run(entries_remaining=0)
    assert p.weights == {}
    assert p.entries_used == 0


def test_the_punch_card_caps_how_many_open_at_once() -> None:
    many = {f"agent{i}": ["AAA", "BBB", "CCC"] for i in range(MIN_AGREEMENT)}
    p = propose(as_of=AS_OF, books=many, risk_on_dials=4, entries_remaining=2)
    assert p.entries_used == 2
    assert len(p.weights) == 2


def test_holding_something_does_not_spend_a_punch() -> None:
    p = run(held=["AAA"], entries_remaining=0)
    assert set(p.weights) == {"AAA"}
    assert p.entries_used == 0


# --------------------------------------------------------------- sizing


def test_a_single_position_takes_the_entry_cap_not_the_whole_book() -> None:
    p = run()
    assert p.weights["AAA"] == MAX_POSITION_AT_ENTRY


def test_weights_never_breach_the_cash_floor() -> None:
    many = {f"agent{i}": [f"T{j}" for j in range(8)] for i in range(MIN_AGREEMENT)}
    p = propose(as_of=AS_OF, books=many, risk_on_dials=4, entries_remaining=20)
    assert sum(p.weights.values()) <= 1.0 - MIN_CASH + 1e-9


def test_every_position_is_equally_weighted() -> None:
    many = {f"agent{i}": ["AAA", "BBB", "CCC"] for i in range(MIN_AGREEMENT)}
    p = propose(as_of=AS_OF, books=many, risk_on_dials=4)
    assert len(set(p.weights.values())) == 1


def test_more_agreement_is_ranked_first_when_the_card_is_short() -> None:
    b = {
        "a": ["POPULAR", "NICHE"],
        "b": ["POPULAR", "NICHE"],
        "c": ["POPULAR", "NICHE"],
        "d": ["POPULAR"],
    }
    p = propose(as_of=AS_OF, books=b, risk_on_dials=4, entries_remaining=1)
    assert list(p.weights) == ["POPULAR"]


# ---------------------------------------------------------------- shape


def test_it_holds_nothing_when_nobody_agrees() -> None:
    """The ordinary outcome, and it must not be an error."""
    p = propose(as_of=AS_OF, books=books(a=["AAA"], b=["BBB"]), risk_on_dials=4)
    assert p.weights == {}
    assert p.vetoes == []


def test_a_single_run_cannot_spend_the_whole_punch_card() -> None:
    """Legal under the lifetime budget, and out of character.

    The first live proposal opened nine positions at once from a book
    the eleven collectively held. The doctrine expects 0 to 2 a year.
    """
    from agents.council.selection import MAX_ENTRIES_PER_RUN

    many = {f"agent{i}": [f"T{j}" for j in range(9)] for i in range(MIN_AGREEMENT)}
    p = propose(as_of=AS_OF, books=many, risk_on_dials=4, entries_remaining=20)
    assert len(p.candidates) == 9
    assert p.entries_used == MAX_ENTRIES_PER_RUN
    assert len(p.weights) == MAX_ENTRIES_PER_RUN
