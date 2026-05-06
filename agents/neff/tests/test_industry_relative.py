"""Industry-relative scoring tests.

The key behavior: a "cheap" stock is judged against its OWN industry's
median, not the universe median. This test fixes a small two-industry
universe and asserts the right candidate qualifies in each.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agents.neff.ranking import score_candidates
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact


def _fin(
    ticker: str,
    *,
    eps: float = 1.0,
    equity: float = 800_000_000,
    dividends: float = -10_000_000,
    debt: float = 100_000_000,
    net_income: float = 100_000_000,
    shares: float = 100_000_000,
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


def _fact(concept: str, value: float, year: int, ticker: str) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace="us-gaap",
        unit="USD",
        value=value,
        period_start=date(year, 1, 1),
        period_end=date(year, 12, 31),
        filed=date(year + 1, 2, 15),
        form="10-K",
        fiscal_year=year,
        fiscal_period="FY",
        accession_number=f"{ticker}-{year}",
    )


@pytest.fixture
def sic_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Place a temp company_sic.json in the bundle path the lookup
    helper expects, with two distinct industries: 60 (banks) and 73
    (business services)."""
    # Override the bundle location via monkeypatch on the module's
    # constant — load_sic_map is lru_cached so we also clear the cache.
    from core.data import sic_codes

    sic_codes.load_sic_map.cache_clear()
    bundle = tmp_path / "company_sic.json"
    sics = {
        # Banks (SIC2 = 60). 6 banks → industry has enough peers.
        **{f"BANK{i}": 6020 for i in range(1, 7)},
        # Tech (SIC2 = 73). 6 tech firms.
        **{f"TECH{i}": 7372 for i in range(1, 7)},
    }
    bundle.write_text(json.dumps(sics))
    monkeypatch.setattr(sic_codes, "BUNDLE_PATH", bundle)
    sic_codes.load_sic_map.cache_clear()
    yield bundle
    sic_codes.load_sic_map.cache_clear()


@pytest.fixture
def cache_with_growth(tmp_path: Path) -> EdgarCache:
    """Every fixture ticker has 12% trailing CAGR (2019 → 2023)."""
    cache = EdgarCache(cache_dir=tmp_path / "cache")
    for t in [f"BANK{i}" for i in range(1, 7)] + [f"TECH{i}" for i in range(1, 7)]:
        cache.save_facts(
            t,
            [
                _fact("Revenues", 1_000_000_000, 2019, t),
                _fact("Revenues", 1_575_000_000, 2023, t),
                _fact("EarningsPerShareDiluted", 1.0, 2019, t),
                _fact("EarningsPerShareDiluted", 1.575, 2023, t),
            ],
        )
    return cache


def test_candidate_judged_against_its_own_industry(
    sic_bundle: Path, cache_with_growth: EdgarCache
) -> None:
    """A "cheap" tech stock has PE=15, in tech's [9, 13.5] window when
    tech's median is 22. A "cheap" bank stock has PE=5, in bank's
    [4, 6] window when bank's median is 10. Both should pass.

    With UNIVERSE-WIDE medians (median ≈ 16), neither would.
    """
    candidates = []

    # Tech industry: 5 stocks at PE=22 + 1 cheap candidate at PE=11 (50% of 22).
    # Yields kept at 1% so industry yield median = 1%, floor = 3%.
    # The candidate has 5% yield (passes).
    for i in range(1, 6):
        # PE 22, eps=1, price=22 → mcap = 100M shares × 22 = 2.2B,
        # divs=-22M = 1% yield, ROE = 100M / 800M = 12.5%
        f = _fin(f"TECH{i}", eps=1.0, dividends=-22_000_000)
        candidates.append((f, 2_200_000_000.0, 22.0))
    # The "cheap tech" — PE=11 (= 50% of 22), high yield, high ROE.
    cheap_tech = _fin(
        "TECH6",
        eps=2.0,
        net_income=130_000_000,
        equity=800_000_000,
        dividends=-100_000_000,
    )
    candidates.append((cheap_tech, 2_000_000_000.0, 22.0))  # PE=11, yield=5%

    # Bank industry: 5 stocks at PE=10 + 1 cheap candidate at PE=5.
    for i in range(1, 6):
        # PE=10, eps=1, price=10 → mcap=1B, divs=-10M = 1% yield, ROE 12.5%
        f = _fin(f"BANK{i}", eps=1.0, dividends=-10_000_000)
        candidates.append((f, 1_000_000_000.0, 10.0))
    cheap_bank = _fin(
        "BANK6",
        eps=2.0,
        net_income=130_000_000,
        equity=800_000_000,
        dividends=-50_000_000,
    )
    candidates.append((cheap_bank, 1_000_000_000.0, 10.0))  # PE=5, yield=5%

    scores = score_candidates(
        candidates,
        as_of=date(2024, 6, 30),
        edgar_cache=cache_with_growth,
    )
    tickers = {s.ticker for s in scores}
    assert "TECH6" in tickers, "cheap tech stock should pass tech-industry screen"
    assert "BANK6" in tickers, "cheap bank stock should pass bank-industry screen"
    # Sanity: the "expensive" peers should NOT pass their own industry's
    # PE window (they sit at the median, not below 60% of it).
    assert "TECH1" not in tickers
    assert "BANK1" not in tickers
