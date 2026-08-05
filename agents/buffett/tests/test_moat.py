"""Tests for the deterministic franchise test.

Nothing in production tested for a moat. ``is_simple_business`` checked
a SIC code against an exclusion list and deferred the real judgement to
an LLM analyzer the runner instantiates as ``None``, so the moat test
was a comment.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agents.buffett.moat import (
    DEFAULT_MIN_ROE_PCT,
    assess_franchise,
    roe_history,
)
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact

AS_OF = date(2026, 8, 4)


def _fact(concept: str, value: float, fy: int, ticker: str) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace="us-gaap",
        unit="USD",
        value=value,
        period_start=date(fy, 1, 1),
        period_end=date(fy, 12, 31),
        filed=date(fy + 1, 2, 15),
        form="10-K",
        fiscal_year=fy,
        fiscal_period="FY",
        accession_number=f"{ticker}-{concept}-{fy}",
    )


def _company(
    tmp_path: Path,
    ticker: str,
    roes: list[float],
    *,
    equity: float = 1_000.0,
    first_year: int = 2016,
) -> EdgarCache:
    """A filer whose annual ROE series is exactly ``roes``, oldest first."""
    cache = EdgarCache(cache_dir=tmp_path)
    facts: list[XbrlFact] = []
    for i, roe in enumerate(roes):
        fy = first_year + i
        facts.append(_fact("StockholdersEquity", equity, fy, ticker))
        facts.append(
            _fact("NetIncomeLoss", equity * roe / 100.0, fy, ticker)
        )
    cache.save_facts(ticker, facts)
    return cache


class TestRoeHistory:
    def test_series_is_newest_first(self, tmp_path: Path) -> None:
        cache = _company(tmp_path, "ACME", [10.0, 20.0, 30.0])

        history = roe_history(cache, "ACME", AS_OF)

        assert [fy for fy, _ in history] == [2018, 2017, 2016]
        assert history[0][1] == pytest.approx(30.0)

    def test_negative_equity_years_are_skipped(self, tmp_path: Path) -> None:
        # ROE is meaningless on negative book, and a large negative would
        # distort both the median and the worst-year figure.
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "ACME",
            [
                _fact("StockholdersEquity", 1_000.0, 2023, "ACME"),
                _fact("NetIncomeLoss", 200.0, 2023, "ACME"),
                _fact("StockholdersEquity", -500.0, 2024, "ACME"),
                _fact("NetIncomeLoss", 200.0, 2024, "ACME"),
            ],
        )

        history = roe_history(cache, "ACME", AS_OF)

        assert [fy for fy, _ in history] == [2023]

    def test_an_unknown_ticker_has_no_history(self, tmp_path: Path) -> None:
        assert roe_history(EdgarCache(cache_dir=tmp_path), "NOPE", AS_OF) == []


class TestAssessFranchise:
    def test_a_franchise_qualifies(self, tmp_path: Path) -> None:
        # Ten years, every one above the bar. Expeditors and Murphy USA
        # look exactly like this in the real cache.
        cache = _company(tmp_path, "MOAT", [22.0] * 10)

        a = assess_franchise(cache, "MOAT", AS_OF)

        assert a.qualifies
        assert a.years_above == 10
        assert a.worst_roe_pct == pytest.approx(22.0)

    def test_one_bad_year_in_ten_is_tolerated(self, tmp_path: Path) -> None:
        # A recession or a write-down does not end a franchise.
        cache = _company(tmp_path, "MOAT", [22.0] * 9 + [3.0])

        assert assess_franchise(cache, "MOAT", AS_OF).qualifies

    def test_three_bad_years_in_ten_is_not(self, tmp_path: Path) -> None:
        cache = _company(tmp_path, "CYCLE", [22.0] * 7 + [3.0] * 3)

        a = assess_franchise(cache, "CYCLE", AS_OF)

        assert not a.qualifies
        assert "7/10" in a.reason

    def test_a_clean_but_low_return_record_fails(self, tmp_path: Path) -> None:
        # The audit's case: a commodity distributor with twelve
        # unblemished years and no pricing power. Consistency alone is
        # not a moat — the level is what competition failed to compete
        # away.
        cache = _company(tmp_path, "COMMODITY", [7.0] * 12)

        a = assess_franchise(cache, "COMMODITY", AS_OF)

        assert not a.qualifies
        assert a.years_above == 0
        assert a.median_roe_pct == pytest.approx(7.0)

    def test_a_high_average_from_one_year_fails(self, tmp_path: Path) -> None:
        # Mean ROE here is 20%, comfortably over the bar. An average
        # hides its own distribution; persistence is the point.
        cache = _company(tmp_path, "ONEHIT", [4.0] * 9 + [164.0])

        a = assess_franchise(cache, "ONEHIT", AS_OF)

        assert not a.qualifies
        assert a.years_above == 1

    def test_too_little_history_cannot_qualify(self, tmp_path: Path) -> None:
        # Three good years is a cycle, not a franchise.
        cache = _company(tmp_path, "YOUNG", [30.0, 30.0, 30.0])

        a = assess_franchise(cache, "YOUNG", AS_OF)

        assert not a.qualifies
        assert "3 year(s)" in a.reason

    def test_a_financial_is_refused_outright(self, tmp_path: Path) -> None:
        # A bank's ROE is a leverage choice by construction, so the
        # level means something different there.
        cache = _company(tmp_path, "JPM", [18.0] * 10)

        a = assess_franchise(cache, "JPM", AS_OF)

        assert not a.qualifies
        assert "financial" in a.reason

    def test_the_bar_is_configurable(self, tmp_path: Path) -> None:
        cache = _company(tmp_path, "MID", [12.0] * 10)

        assert not assess_franchise(cache, "MID", AS_OF).qualifies
        assert assess_franchise(cache, "MID", AS_OF, min_roe_pct=10.0).qualifies

    def test_the_default_bar_is_fifteen(self) -> None:
        assert DEFAULT_MIN_ROE_PCT == 15.0

    def test_the_reason_records_the_evidence(self, tmp_path: Path) -> None:
        cache = _company(tmp_path, "MOAT", [22.0] * 10)

        a = assess_franchise(cache, "MOAT", AS_OF)

        assert "10/10" in a.reason
        assert a.fraction_above == pytest.approx(1.0)
