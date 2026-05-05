"""Unit tests for FundamentalsFetcher and CachedEdgarAdapter."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact
from core.data.fundamentals_fetcher import (
    CONCEPT_MAP,
    CachedEdgarAdapter,
    FundamentalsError,
    FundamentalsFetcher,
)


def _fact(
    *,
    concept: str,
    namespace: str = "us-gaap",
    unit: str = "USD",
    value: float = 100.0,
    period_end: date = date(2020, 12, 31),
    filed: date = date(2021, 2, 15),
    form: str = "10-K",
    accession_number: str = "acc-1",
) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace=namespace,
        unit=unit,
        value=value,
        period_start=date(period_end.year, 1, 1) if form == "10-K" else None,
        period_end=period_end,
        filed=filed,
        form=form,
        fiscal_year=period_end.year,
        fiscal_period="FY" if form == "10-K" else "Q4",
        accession_number=accession_number,
    )


@pytest.fixture
def populated_cache(tmp_path: Path) -> EdgarCache:
    cache = EdgarCache(cache_dir=tmp_path)
    facts = [
        _fact(concept="Revenues", value=1_000_000_000.0),
        _fact(concept="OperatingIncomeLoss", value=200_000_000.0),
        _fact(concept="NetIncomeLoss", value=150_000_000.0),
        _fact(concept="Assets", value=5_000_000_000.0),
        _fact(concept="AssetsCurrent", value=2_000_000_000.0),
        _fact(concept="LiabilitiesCurrent", value=1_500_000_000.0),
        _fact(concept="PropertyPlantAndEquipmentNet", value=1_000_000_000.0),
        _fact(concept="LongTermDebtNoncurrent", value=500_000_000.0),
        _fact(concept="CashAndCashEquivalentsAtCarryingValue", value=300_000_000.0),
        _fact(
            concept="EntityCommonStockSharesOutstanding",
            namespace="dei",
            unit="shares",
            value=100_000_000.0,
            accession_number="acc-shares",
        ),
    ]
    cache.save_facts("TEST", facts)
    return cache


class TestConceptMap:
    def test_required_fields_present(self) -> None:
        # The Greenblatt strategy needs these fields — ensure mapping
        # exists for all of them.
        for f in (
            "operating_income",
            "current_assets",
            "current_liabilities",
            "ppe_net",
            "shares_outstanding",
            "cash_and_equivalents",
            "long_term_debt",
        ):
            assert f in CONCEPT_MAP
            assert len(CONCEPT_MAP[f]) >= 1

    def test_unknown_field_raises(self, populated_cache: EdgarCache) -> None:
        fetcher = FundamentalsFetcher(cache=populated_cache, client=None)
        with pytest.raises(FundamentalsError):
            fetcher.get_field("TEST", "nonexistent_field", date(2021, 6, 1))


class TestGetField:
    def test_returns_none_for_missing_ticker(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        # Disable populate-on-miss so the test stays offline
        from core.data.fundamentals_fetcher import FundamentalsFetcherConfig

        fetcher = FundamentalsFetcher(
            cache=cache, client=None, config=FundamentalsFetcherConfig(populate_cache_on_miss=False)
        )
        assert fetcher.get_field("UNKNOWN", "revenue", date(2021, 6, 1)) is None

    def test_returns_value_for_cached_ticker(self, populated_cache: EdgarCache) -> None:
        fetcher = FundamentalsFetcher(cache=populated_cache, client=None)
        fact = fetcher.get_field("TEST", "operating_income", date(2021, 6, 1))
        assert fact is not None
        assert fact.value == 200_000_000.0

    def test_pit_correctness(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        facts = [
            _fact(  # FY 2019, filed Feb 2020
                concept="Revenues",
                value=1.0,
                period_end=date(2019, 12, 31),
                filed=date(2020, 2, 15),
                accession_number="acc-2019",
            ),
            _fact(  # FY 2020, filed Feb 2021
                concept="Revenues",
                value=2.0,
                period_end=date(2020, 12, 31),
                filed=date(2021, 2, 15),
                accession_number="acc-2020",
            ),
        ]
        cache.save_facts("TEST", facts)
        fetcher = FundamentalsFetcher(cache=cache, client=None)
        # Just before the 2020 10-K was filed
        f = fetcher.get_field("TEST", "revenue", date(2021, 1, 15))
        assert f is not None
        assert f.value == 1.0
        # Just after
        f = fetcher.get_field("TEST", "revenue", date(2021, 3, 1))
        assert f is not None
        assert f.value == 2.0


class TestGetAllFields:
    def test_returns_all_present_fields_and_filing_metadata(
        self, populated_cache: EdgarCache
    ) -> None:
        fetcher = FundamentalsFetcher(cache=populated_cache, client=None)
        values, meta = fetcher.get_all_fields("TEST", date(2021, 6, 1))
        assert values["operating_income"] == 200_000_000.0
        assert values["current_assets"] == 2_000_000_000.0
        assert values["current_liabilities"] == 1_500_000_000.0
        assert values["ppe_net"] == 1_000_000_000.0
        assert values["shares_outstanding"] == 100_000_000.0
        # total_debt is synthesized from long_term_debt when missing
        assert values["total_debt"] == 500_000_000.0
        assert meta is not None
        assert meta.ticker == "TEST"
        assert meta.form_type == "10-K"

    def test_no_data_returns_none_metadata(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        fetcher = FundamentalsFetcher(cache=cache, client=None)
        values, meta = fetcher.get_all_fields("UNKNOWN", date(2021, 6, 1))
        assert meta is None
        assert all(v is None for v in values.values())


class TestEnsureCached:
    def test_already_cached_returns_true(self, populated_cache: EdgarCache) -> None:
        fetcher = FundamentalsFetcher(cache=populated_cache, client=None)
        assert fetcher.ensure_cached("TEST") is True

    def test_no_client_no_cache_returns_false(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        fetcher = FundamentalsFetcher(cache=cache, client=None)
        assert fetcher.ensure_cached("UNKNOWN") is False


class TestCachedEdgarAdapter:
    def test_list_filings_from_cache(self, populated_cache: EdgarCache) -> None:
        adapter = CachedEdgarAdapter(
            fetcher=FundamentalsFetcher(cache=populated_cache, client=None)
        )
        filings = adapter.list_filings("TEST", form_types=("10-K", "10-Q"))
        # Two distinct accession numbers in the fixture (acc-1, acc-shares)
        assn = sorted(f.accession_number for f in filings)
        assert "acc-1" in assn
        assert "acc-shares" in assn

    def test_parse_financials_returns_field_dict(
        self, populated_cache: EdgarCache
    ) -> None:
        adapter = CachedEdgarAdapter(
            fetcher=FundamentalsFetcher(cache=populated_cache, client=None)
        )
        filings = adapter.list_filings("TEST", form_types=("10-K", "10-Q"))
        # Pick the most recent
        latest = max(filings, key=lambda f: f.filing_date)
        fields = adapter.parse_financials(latest)
        assert fields["operating_income"] == 200_000_000.0
        assert fields["current_assets"] == 2_000_000_000.0
        assert "sic_code" in fields  # always present, even if None

    def test_unknown_ticker_returns_empty(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        from core.data.fundamentals_fetcher import FundamentalsFetcherConfig

        adapter = CachedEdgarAdapter(
            fetcher=FundamentalsFetcher(
                cache=cache, client=None, config=FundamentalsFetcherConfig(populate_cache_on_miss=False)
            )
        )
        assert adapter.list_filings("UNKNOWN", form_types=("10-K",)) == []
