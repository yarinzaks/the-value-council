"""Shared fixtures for Fisher tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact


def make_pit(
    ticker: str,
    *,
    eps_diluted: float = 4.0,
    revenue: float = 5_000_000_000.0,
    net_income: float = 800_000_000.0,
    operating_income: float = 1_000_000_000.0,  # 20% op margin on $5B rev
    total_equity: float = 3_000_000_000.0,
    total_debt: float = 1_000_000_000.0,
    shares: float = 200_000_000.0,
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
        revenue=revenue,
        net_income=net_income,
        operating_income=operating_income,
        eps_diluted=eps_diluted,
        eps_basic=eps_diluted,
        total_equity=total_equity,
        total_debt=total_debt,
        long_term_debt=total_debt,
        shares_outstanding=shares,
    )


def make_fact(
    *,
    concept: str,
    value: float,
    period_end: date,
    filed: date,
    accession: str,
    namespace: str = "us-gaap",
) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace=namespace,
        unit="USD",
        value=value,
        period_start=date(period_end.year, 1, 1),
        period_end=period_end,
        filed=filed,
        form="10-K",
        fiscal_year=period_end.year,
        fiscal_period="FY",
        accession_number=accession,
    )


@pytest.fixture
def empty_cache(tmp_path: Path) -> EdgarCache:
    return EdgarCache(cache_dir=tmp_path)


@pytest.fixture
def fisher_quality_cache(tmp_path: Path) -> EdgarCache:
    """Ticker QUALITY: 8 years of clean Fisher-grade history.

    * Revenue grows 12% per year (passes Point 1's 8% floor)
    * R&D = 8% of revenue (passes Point 3's 5% floor)
    * Operating margin 20% (passes Point 5's 12% floor)
    * Margin EXPANDS slightly over 5 years (passes Point 6)
    * Share count flat (passes Point 13)
    """
    cache = EdgarCache(cache_dir=tmp_path)
    facts: list[XbrlFact] = []
    for i, fy in enumerate(range(2016, 2024)):
        revenue = 2_000_000_000.0 * (1.12 ** i)
        op_inc = revenue * (0.18 + 0.005 * i)  # 18% → 21% over 8 years
        rd = revenue * 0.08
        period_end = date(fy, 12, 31)
        filed = date(fy + 1, 2, 15)
        accession = f"QUALITY-{fy}"
        facts.extend([
            make_fact(
                concept="Revenues",
                value=revenue,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
            make_fact(
                concept="OperatingIncomeLoss",
                value=op_inc,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
            make_fact(
                concept="ResearchAndDevelopmentExpense",
                value=rd,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
            make_fact(
                concept="CommonStockSharesOutstanding",
                value=200_000_000.0,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
        ])
    cache.save_facts("QUALITY", facts)
    return cache


@pytest.fixture
def asc606_switcher_cache(tmp_path: Path) -> EdgarCache:
    """Ticker SWITCH: tagged ``Revenues`` through FY2018, then moved to
    the ASC 606 concept and never touched the old tag again.

    This is the single most common shape in the real cache — 26.7% of
    resolvable revenue reads were stale on it, median lag 7 fiscal
    years. The stale ``Revenues`` fact is still on file and still the
    first entry in the concept chain, so a first-match resolver returns
    FY2018 forever while the company reports through FY2023.

    Everything else (R&D, operating income, shares) keeps filing
    normally, so the fixture isolates the tagging change.
    """
    cache = EdgarCache(cache_dir=tmp_path)
    facts: list[XbrlFact] = []
    for i, fy in enumerate(range(2016, 2024)):
        revenue = 1_000_000_000.0 * (1.15**i)
        period_end = date(fy, 12, 31)
        filed = date(fy + 1, 2, 15)
        accession = f"SWITCH-{fy}"
        concept = (
            "Revenues"
            if fy <= 2018
            else "RevenueFromContractWithCustomerExcludingAssessedTax"
        )
        facts.extend([
            make_fact(
                concept=concept,
                value=revenue,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
            make_fact(
                concept="OperatingIncomeLoss",
                value=revenue * 0.20,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
            make_fact(
                concept="ResearchAndDevelopmentExpense",
                value=revenue * 0.08,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
            make_fact(
                concept="CommonStockSharesOutstanding",
                value=200_000_000.0,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
        ])
    cache.save_facts("SWITCH", facts)
    return cache


@pytest.fixture
def frozen_shares_cache(tmp_path: Path) -> EdgarCache:
    """Ticker FROZEN: stopped tagging share count after FY2017.

    Both ends of the five-year dilution comparison resolve to the same
    frozen year, so the change computes as exactly 0.0% — which passes
    the dilution point. The company issued shares heavily and nothing
    in the data says so.
    """
    cache = EdgarCache(cache_dir=tmp_path)
    facts: list[XbrlFact] = []
    for fy in range(2016, 2024):
        period_end = date(fy, 12, 31)
        filed = date(fy + 1, 2, 15)
        accession = f"FROZEN-{fy}"
        facts.append(
            make_fact(
                concept="Revenues",
                value=1_000_000_000.0,
                period_end=period_end,
                filed=filed,
                accession=accession,
            )
        )
        if fy <= 2017:
            facts.append(
                make_fact(
                    concept="CommonStockSharesOutstanding",
                    value=100_000_000.0,
                    period_end=period_end,
                    filed=filed,
                    accession=accession,
                )
            )
    cache.save_facts("FROZEN", facts)
    return cache
