"""Shared fixtures for Buffett tests.

Builds a small synthetic EDGAR cache + PointInTimeFinancials objects
so unit tests don't have to hit real data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact


# ---- Builders ---------------------------------------------------------------
def make_pit(
    ticker: str,
    *,
    net_income: float = 1_000_000_000.0,
    total_equity: float = 5_000_000_000.0,
    total_debt: float = 1_000_000_000.0,
    long_term_debt: float | None = None,
    shares: float = 1_000_000_000.0,
    sic_code: str | None = "2080",  # beverages — Buffett-friendly
    operating_cash_flow: float = 1_500_000_000.0,
    capex: float = 300_000_000.0,
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
        net_income=net_income,
        total_equity=total_equity,
        total_debt=total_debt,
        long_term_debt=long_term_debt
        if long_term_debt is not None
        else total_debt,
        shares_outstanding=shares,
        operating_cash_flow=operating_cash_flow,
        capex=capex,
        sic_code=sic_code,
    )


def make_fact(
    *,
    concept: str,
    value: float,
    period_end: date,
    filed: date,
    accession: str,
    form: str = "10-K",
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
        accession_number=accession,
    )


# ---- Fixtures --------------------------------------------------------------
@pytest.fixture
def empty_cache(tmp_path: Path) -> EdgarCache:
    return EdgarCache(cache_dir=tmp_path)


@pytest.fixture
def buffett_quality_cache(tmp_path: Path) -> EdgarCache:
    """Cache where ticker WONDERFUL has 12 years of clean history:
    growing OE, positive earnings, ~20% ROE.
    """
    cache = EdgarCache(cache_dir=tmp_path)
    facts: list[XbrlFact] = []
    # FY2012..FY2023 — 12 years of growing earnings + steady balance sheet.
    for i, fy in enumerate(range(2012, 2024)):
        # Net income grows from 800M (FY2012) to ~1.7B (FY2023) — 6.5% CAGR.
        ni = 800_000_000.0 * (1.065 ** i)
        eq = 4_000_000_000.0 + (i * 200_000_000.0)
        ocf = ni * 1.4  # OCF a bit above NI (typical for quality biz)
        capex = ni * 0.25  # capex ~25% of NI; FCF clearly positive
        period_end = date(fy, 12, 31)
        filed = date(fy + 1, 2, 15)
        accession = f"WONDERFUL-{fy}"
        facts.extend([
            make_fact(
                concept="NetIncomeLoss",
                value=ni,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
            make_fact(
                concept="StockholdersEquity",
                value=eq,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
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
    cache.save_facts("WONDERFUL", facts)
    return cache
