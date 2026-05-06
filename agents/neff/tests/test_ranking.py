"""Unit tests for Neff ranking — uses an in-memory EdgarCache fixture
so we can drive growth lookups without network."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agents.neff.ranking import NeffScore, score_candidates, select_top_n
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact


def _fin(
    *,
    ticker: str,
    eps: float = 2.0,
    equity: float = 800_000_000.0,
    dividends: float = -40_000_000.0,
    debt: float = 100_000_000.0,
    net_income: float = 100_000_000.0,
    shares: float = 100_000_000.0,
) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=date(2024, 6, 30),
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=date(2024, 2, 15),
            period_of_report=date(2023, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        eps_diluted=eps,
        eps_basic=eps,
        total_equity=equity,
        dividends_paid=dividends,
        total_debt=debt,
        long_term_debt=debt,
        net_income=net_income,
        shares_outstanding=shares,
    )


def _fact(
    *,
    concept: str,
    value: float,
    period_end: date,
    filed: date,
    form: str = "10-K",
    accession_number: str = "a",
) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace="us-gaap",
        unit="USD",
        value=value,
        period_start=date(period_end.year, 1, 1),
        period_end=period_end,
        filed=filed,
        form=form,
        fiscal_year=period_end.year,
        fiscal_period="FY",
        accession_number=accession_number,
    )


@pytest.fixture
def cache_with_growth(tmp_path: Path) -> EdgarCache:
    """A cache where every ticker has FY2019 + FY2023 history that
    yields 12% CAGR over 4 fiscal years (Neff sweet spot).

    Why FY2019 not FY2020: the "4 years prior" anchor uses
    ``as_of - 4 years``. With ``as_of=2024-06-30`` that's
    2020-06-30 — at which point the FY2020 10-K (filed Feb 2021)
    isn't visible yet. The FY2019 10-K (filed Feb 2020) IS visible.
    """
    cache = EdgarCache(cache_dir=tmp_path)
    for t in ("OK1", "OK2", "OK3", "EXP", "TINY"):
        # FY2019 base = 1.0; FY2023 = 1.0 × (1.12)^4 ≈ 1.575 → 12% CAGR
        facts = [
            _fact(
                concept="Revenues",
                value=1_000_000_000.0,
                period_end=date(2019, 12, 31),
                filed=date(2020, 2, 15),
                accession_number=f"{t}-2020",
            ),
            _fact(
                concept="Revenues",
                value=1_575_000_000.0,
                period_end=date(2023, 12, 31),
                filed=date(2024, 2, 15),
                accession_number=f"{t}-2024",
            ),
            _fact(
                concept="EarningsPerShareDiluted",
                value=1.0,
                period_end=date(2019, 12, 31),
                filed=date(2020, 2, 15),
                accession_number=f"{t}-2020",
            ),
            _fact(
                concept="EarningsPerShareDiluted",
                value=1.575,
                period_end=date(2023, 12, 31),
                filed=date(2024, 2, 15),
                accession_number=f"{t}-2024",
            ),
        ]
        cache.save_facts(t, facts)
    return cache


class TestScoreCandidatesEmpty:
    def test_empty_returns_empty(self, cache_with_growth: EdgarCache) -> None:
        assert score_candidates(
            [], as_of=date(2024, 6, 30), edgar_cache=cache_with_growth
        ) == []


class TestScoreCandidatesScreen:
    def test_passes_full_screen(self, cache_with_growth: EdgarCache) -> None:
        # Build a universe where:
        #   - 4 "HIGH" stocks at PE=20 with TINY 1% yield → set the
        #     market median PE high and yield median low
        #   - 1 candidate "OK1" at PE=10 (= 50% of median, in
        #     window), yield = 5% (above 1+2=3% floor), 12% growth,
        #     16.25% ROE
        candidates: list = []
        for i, t in enumerate(("HIGH1", "HIGH2", "HIGH3", "HIGH4")):
            # PE=20 stock with 1% yield (divs=-10M, mcap=1B).
            f = _fin(ticker=t, eps=1.0, dividends=-10_000_000.0)
            candidates.append((f, 1_000_000_000.0, 20.0))
        # Mark these high-PE filers in cache too so growth lookups
        # work — re-use the same fixture-style fact set.
        for t in ("HIGH1", "HIGH2", "HIGH3", "HIGH4"):
            facts = [
                _fact(
                    concept="EarningsPerShareDiluted",
                    value=1.0,
                    period_end=date(2019, 12, 31),
                    filed=date(2020, 2, 15),
                    accession_number=f"{t}-20",
                ),
                _fact(
                    concept="EarningsPerShareDiluted",
                    value=1.575,
                    period_end=date(2023, 12, 31),
                    filed=date(2024, 2, 15),
                    accession_number=f"{t}-24",
                ),
                _fact(
                    concept="Revenues",
                    value=1_000_000_000.0,
                    period_end=date(2019, 12, 31),
                    filed=date(2020, 2, 15),
                    accession_number=f"{t}-20",
                ),
                _fact(
                    concept="Revenues",
                    value=1_575_000_000.0,
                    period_end=date(2023, 12, 31),
                    filed=date(2024, 2, 15),
                    accession_number=f"{t}-24",
                ),
            ]
            cache_with_growth.save_facts(t, facts)
        # Candidate: 100M shares × $20 = $2B mcap. EPS=2 → PE=10.
        # Dividends=-100M / mcap=2B → 5% yield (≥ 1+2 = 3% floor).
        # ROE: 130M / 800M = 16.25% (≥ 15% floor).
        cand = _fin(
            ticker="OK1",
            eps=2.0,
            net_income=130_000_000,
            equity=800_000_000,
            dividends=-100_000_000,
        )
        candidates.append((cand, 2_000_000_000.0, 20.0))  # PE = 10, yield 5%

        scores = score_candidates(
            candidates, as_of=date(2024, 6, 30), edgar_cache=cache_with_growth
        )
        # Candidate must be among survivors. The HIGH stocks have
        # PE=20 = median, PE-window upper bound = 60% of median = 12,
        # so they're all rejected on the PE window.
        tickers = [s.ticker for s in scores]
        assert "OK1" in tickers


class TestSelectTopN:
    def test_returns_top_n(self) -> None:
        scores = [
            NeffScore(
                ticker=f"T{i}",
                price=10,
                market_cap=1e9,
                pe=10,
                eps_growth_pct=10.0 + i,
                sales_growth_pct=8.0,
                dividend_yield_pct=4.0,
                roe_pct=18.0,
                total_return_pe=1.0 + i / 10,
                debt_to_equity=0.5,
                net_income=1e8,
                industry_sic2=73,
                industry_peer_count=10,
                pass_pe_window=True,
                pass_growth_window=True,
                pass_yield_premium=True,
                pass_tr_pe_multiple=True,
                pass_sales_drives_eps=True,
                pass_roe=True,
            )
            for i in range(5)
        ]
        # Sorted by total_return_pe desc happens elsewhere — test
        # select_top_n preserves input order.
        out = select_top_n(scores, n=3)
        assert [s.ticker for s in out] == ["T0", "T1", "T2"]

    def test_zero_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], n=0)

    def test_negative_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], n=-5)
