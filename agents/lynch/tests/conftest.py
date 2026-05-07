"""Shared fixtures for Lynch tests."""

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
    long_term_debt: float | None = None,
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
        long_term_debt=long_term_debt
        if long_term_debt is not None
        else total_debt,
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


@pytest.fixture
def empty_cache(tmp_path: Path) -> EdgarCache:
    return EdgarCache(cache_dir=tmp_path)


@pytest.fixture
def fast_grower_cache(tmp_path: Path) -> EdgarCache:
    """Ticker FASTY: 12 years of clean history, 25% EPS CAGR — Lynch
    Fast Grower archetype.
    """
    cache = EdgarCache(cache_dir=tmp_path)
    facts: list[XbrlFact] = []
    # FY2012..FY2023 — 12 years; EPS grows from 0.5 to ~7.45 = ~25% CAGR.
    base_eps = 0.5
    for i, fy in enumerate(range(2012, 2024)):
        eps = base_eps * (1.25 ** i)
        ni = eps * 100_000_000.0  # 100M shares assumed
        ocf = ni * 1.4
        capex = ni * 0.3
        period_end = date(fy, 12, 31)
        filed = date(fy + 1, 2, 15)
        accession = f"FASTY-{fy}"
        facts.extend([
            make_fact(
                concept="EarningsPerShareDiluted",
                value=eps,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
            make_fact(
                concept="NetIncomeLoss",
                value=ni,
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
    cache.save_facts("FASTY", facts)
    return cache


@pytest.fixture
def stalwart_cache(tmp_path: Path) -> EdgarCache:
    """Ticker STEADY: 12 years, 12% EPS CAGR — Stalwart archetype."""
    cache = EdgarCache(cache_dir=tmp_path)
    facts: list[XbrlFact] = []
    base_eps = 2.0
    for i, fy in enumerate(range(2012, 2024)):
        eps = base_eps * (1.12 ** i)
        ni = eps * 100_000_000.0
        ocf = ni * 1.3
        capex = ni * 0.25
        period_end = date(fy, 12, 31)
        filed = date(fy + 1, 2, 15)
        accession = f"STEADY-{fy}"
        facts.extend([
            make_fact(
                concept="EarningsPerShareDiluted",
                value=eps,
                period_end=period_end,
                filed=filed,
                accession=accession,
            ),
            make_fact(
                concept="NetIncomeLoss",
                value=ni,
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
    cache.save_facts("STEADY", facts)
    return cache
