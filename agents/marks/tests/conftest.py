"""Shared fixtures for Marks tests."""

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
def fcf_cache(tmp_path: Path) -> EdgarCache:
    """Cache where ticker QUALITY has clean OCF + capex history.
    OCF=$280M, capex=$80M → FCF=$200M."""
    cache = EdgarCache(cache_dir=tmp_path)
    facts = [
        make_fact(
            concept="NetCashProvidedByUsedInOperatingActivities",
            value=280_000_000.0,
            period_end=date(2023, 12, 31),
            filed=date(2024, 2, 15),
            accession="QUALITY-2024",
        ),
        make_fact(
            concept="PaymentsToAcquirePropertyPlantAndEquipment",
            value=80_000_000.0,
            period_end=date(2023, 12, 31),
            filed=date(2024, 2, 15),
            accession="QUALITY-2024",
        ),
    ]
    cache.save_facts("QUALITY", facts)
    return cache
