"""Tests for Rule 18 — 20-30 names across 15+ industries.

Nothing in the strategy had a concept of industry. ``select`` took the
top N by composite rank and equal-weighted them, so the book was
whatever the screen returned. The live book at the time of writing was
25 holdings across 11 industries, insurance carriers at 28.5% against a
15% cap, and financials at 49.9% — half the portfolio in the one sector
the playbook singles out ahead of its own 2008 case study.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agents.dreman.diversification import (
    DEFAULT_MAX_INDUSTRY_WEIGHT_PCT,
    DEFAULT_MIN_INDUSTRIES,
    diversify,
    industry_of,
    max_per_industry,
)


@dataclass(frozen=True)
class _Row:
    """Minimal stand-in for a DremanScore — diversify only reads ticker."""

    ticker: str


def _rows(*tickers: str) -> list[_Row]:
    return [_Row(t) for t in tickers]


class TestIndustryOf:
    def test_a_bank_and_an_insurer_are_different_industries(self) -> None:
        # JPM is SIC 6022, ALL is 6331 — both financial, and Rule 18 is
        # about not owning eight of either.
        assert industry_of("JPM") != industry_of("ALL")

    def test_two_insurers_share_an_industry(self) -> None:
        assert industry_of("ALL") == industry_of("CNA")

    def test_an_unknown_ticker_is_its_own_group(self) -> None:
        # Pooling unknowns would invent an industry and then cap it as
        # though it were real.
        a = industry_of("NOTATICKER1")
        b = industry_of("NOTATICKER2")

        assert a != b
        assert a.startswith("?")


class TestMaxPerIndustry:
    def test_dremans_own_numbers_give_two(self) -> None:
        # 30 names across 15 industries. Two per group is the largest
        # count that still admits the floor.
        assert max_per_industry(30, 15) == 2

    def test_it_never_returns_zero(self) -> None:
        # A book smaller than the industry floor would otherwise be
        # unable to select anything at all.
        assert max_per_industry(5, 15) == 1


class TestConcentrationIsBroken:
    def test_eight_insurers_become_two(self) -> None:
        # The live book's actual shape: insurance sweeps the cheap
        # quintile because insurers reliably screen cheap on P/E, P/B
        # and yield at once.
        insurers = ["ALL", "CNA", "UVE", "HCI", "SPNT", "ACGL", "AIZ", "CINF"]
        others = ["JPM", "DUK", "XOM", "MMM", "KO", "T", "CAT", "PFE"]
        rows = _rows(*(insurers + others))

        chosen, report = diversify(rows, portfolio_size=10, min_industries=5)

        picked = {r.ticker for r in chosen}
        assert len([t for t in insurers if t in picked]) <= 2
        assert report.dropped_for_concentration

    def test_the_cap_holds_on_a_properly_sized_book(self) -> None:
        # 30 names, 15 industries, 2 per industry: 6.7% each, well
        # inside the 15% ceiling.
        rows = _rows(*[f"T{i}" for i in range(60)])

        _, report = diversify(rows, portfolio_size=30, min_industries=15)

        assert report.max_industry_weight_pct <= DEFAULT_MAX_INDUSTRY_WEIGHT_PCT

    def test_rank_order_is_preserved_among_survivors(self) -> None:
        # Diversification decides *which* names, not their order. A
        # cheaper name never ends up behind a dearer one from the same
        # industry.
        rows = _rows("ALL", "CNA", "UVE", "JPM", "DUK")

        chosen, _ = diversify(rows, portfolio_size=5, min_industries=3)

        order = [r.ticker for r in chosen]
        assert order.index("ALL") < order.index("CNA")


class TestBreadthIsBought:
    def test_a_crowded_book_gives_up_its_weakest_name_for_a_new_industry(
        self,
    ) -> None:
        # Pass one admits two per industry and can still land short of
        # the floor. Pass two swaps the lowest-ranked name from the most
        # crowded group for the best candidate from a missing one.
        rows = _rows("ALL", "CNA", "JPM", "BAC", "DUK", "SO", "XOM", "CVX")

        chosen, report = diversify(rows, portfolio_size=4, min_industries=4)

        assert report.industries == 4
        assert report.met_industry_floor
        # CNA is the second insurer and the weakest name in the most
        # crowded group — it is the one that pays for the breadth.
        assert "CNA" not in {r.ticker for r in chosen}

    def test_a_thin_universe_under_deploys_rather_than_concentrate(
        self,
    ) -> None:
        # Three insurers cannot be spread across fifteen industries, and
        # the cap cannot manufacture breadth that the screen does not
        # contain. A 25-name book aiming for 15 industries gives any one
        # industry two slots, so two is what insurance gets and the
        # other 23 slots stay empty.
        #
        # That is the intended trade. Rule 18's protection is structural
        # — "wide diversification protects against the inevitable 15-20%
        # of holdings that turn out to be value traps" — so a screen
        # that cannot supply the structure means the protection is
        # absent, and the response is less risk rather than more. The
        # report says exactly what happened.
        rows = _rows("ALL", "CNA", "UVE")

        chosen, report = diversify(rows, portfolio_size=25, min_industries=15)

        assert len(chosen) == 2
        assert not report.met_industry_floor
        assert report.industries == 1
        assert "UVE" in report.dropped_for_concentration

    def test_it_does_not_strand_a_singleton_book(self) -> None:
        # Every industry already a singleton means there is nothing to
        # trade away; pass two must stop rather than loop.
        rows = _rows("JPM", "DUK", "XOM")

        chosen, report = diversify(rows, portfolio_size=3, min_industries=15)

        assert len(chosen) == 3
        assert report.industries == 3


class TestEdges:
    def test_an_empty_ranking_selects_nothing(self) -> None:
        chosen, report = diversify([], portfolio_size=25)

        assert chosen == []
        assert report.industries == 0

    def test_a_non_positive_book_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            diversify(_rows("JPM"), portfolio_size=0)

    def test_it_never_exceeds_the_requested_size(self) -> None:
        rows = _rows(*[f"T{i}" for i in range(100)])

        chosen, _ = diversify(rows, portfolio_size=25, min_industries=15)

        assert len(chosen) == 25

    def test_the_defaults_are_dremans(self) -> None:
        assert DEFAULT_MIN_INDUSTRIES == 15
        assert DEFAULT_MAX_INDUSTRY_WEIGHT_PCT == 15.0
