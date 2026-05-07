"""Shared fixtures for Klarman tests."""

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
    eps_diluted: float = 2.0,
    net_income: float = 200_000_000.0,
    total_equity: float = 1_500_000_000.0,
    total_debt: float = 400_000_000.0,
    shares: float = 100_000_000.0,
    dividends_paid: float = -10_000_000.0,
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
        eps_diluted=eps_diluted,
        eps_basic=eps_diluted,
        net_income=net_income,
        total_equity=total_equity,
        total_debt=total_debt,
        long_term_debt=total_debt,
        shares_outstanding=shares,
        dividends_paid=dividends_paid,
    )


def make_fact(
    *,
    concept: str,
    value: float,
    period_end: date,
    filed: date,
    accession: str,
) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace="us-gaap",
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
def steady_fcf_cache(tmp_path: Path) -> EdgarCache:
    """Ticker STEADY: 8 years of stable FCF (~$300M annually).
    OCF=$400M, capex=$100M → FCF=$300M, very modest growth.
    """
    cache = EdgarCache(cache_dir=tmp_path)
    facts: list[XbrlFact] = []
    for i, fy in enumerate(range(2016, 2024)):
        # FCF grows 3% per year — well within Klarman's 5% cap.
        ocf = 400_000_000.0 * (1.03 ** i)
        capex = 100_000_000.0
        period_end = date(fy, 12, 31)
        filed = date(fy + 1, 2, 15)
        accession = f"STEADY-{fy}"
        facts.extend([
            make_fact(
                concept="NetCashProvidedByUsedInOperatingActivities",
                value=ocf,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
            make_fact(
                concept="PaymentsToAcquirePropertyPlantAndEquipment",
                value=capex,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
        ])
    cache.save_facts("STEADY", facts)
    return cache
