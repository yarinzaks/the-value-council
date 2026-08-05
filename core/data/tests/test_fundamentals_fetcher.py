"""Unit tests for FundamentalsFetcher and CachedEdgarAdapter."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact
from core.data.fundamentals_fetcher import (
    CONCEPT_MAP,
    MAX_FACT_AGE_DAYS,
    CachedEdgarAdapter,
    FundamentalsError,
    FundamentalsFetcher,
    expected_units,
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


class TestFlowConceptDuration:
    """Flow fields must come from a full-year period.

    Measured on 300 cached tickers: without the window, roughly a
    quarter of the universe resolved revenue, EBIT, net income and
    operating cash flow to a year-to-date figure, with a median value
    of 0.48x the true annual number.
    """

    @staticmethod
    def _cache_with_annual_and_ytd(tmp_path: Path) -> EdgarCache:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "ACME",
            [
                XbrlFact(
                    concept="Revenues",
                    namespace="us-gaap",
                    unit="USD",
                    value=1_000.0,
                    period_start=date(2025, 1, 1),
                    period_end=date(2025, 12, 31),
                    filed=date(2026, 2, 15),
                    form="10-K",
                    fiscal_year=2025,
                    fiscal_period="FY",
                    accession_number="acc-fy",
                ),
                XbrlFact(
                    concept="Revenues",
                    namespace="us-gaap",
                    unit="USD",
                    value=560.0,
                    period_start=date(2026, 1, 1),
                    period_end=date(2026, 9, 30),
                    filed=date(2026, 10, 30),
                    form="10-Q",
                    fiscal_year=2026,
                    fiscal_period="Q3",
                    accession_number="acc-q3",
                ),
                # A balance-sheet instant, to prove stock fields still work.
                XbrlFact(
                    concept="Assets",
                    namespace="us-gaap",
                    unit="USD",
                    value=8_000.0,
                    period_start=None,
                    period_end=date(2026, 9, 30),
                    filed=date(2026, 10, 30),
                    form="10-Q",
                    fiscal_year=2026,
                    fiscal_period="Q3",
                    accession_number="acc-q3",
                ),
            ],
        )
        return cache

    def test_revenue_resolves_to_the_annual_figure(self, tmp_path: Path) -> None:
        fetcher = FundamentalsFetcher(
            cache=self._cache_with_annual_and_ytd(tmp_path), client=None
        )

        fact = fetcher.get_field("ACME", "revenue", date(2026, 12, 1))

        assert fact is not None
        assert fact.value == 1_000.0

    def test_stock_fields_still_use_the_latest_instant(
        self, tmp_path: Path
    ) -> None:
        # The window must not be applied to balance-sheet concepts, which
        # carry no period_start and would otherwise all resolve to None.
        fetcher = FundamentalsFetcher(
            cache=self._cache_with_annual_and_ytd(tmp_path), client=None
        )

        fact = fetcher.get_field("ACME", "total_assets", date(2026, 12, 1))

        assert fact is not None
        assert fact.value == 8_000.0

    def test_quarter_only_filer_yields_none(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "NEWCO",
            [
                XbrlFact(
                    concept="Revenues",
                    namespace="us-gaap",
                    unit="USD",
                    value=120.0,
                    period_start=date(2026, 1, 1),
                    period_end=date(2026, 3, 31),
                    filed=date(2026, 4, 30),
                    form="10-Q",
                    fiscal_year=2026,
                    fiscal_period="Q1",
                    accession_number="acc-q1",
                )
            ],
        )
        fetcher = FundamentalsFetcher(cache=cache, client=None)

        # A quarter is not a year. Better no number than a wrong one.
        assert fetcher.get_field("NEWCO", "revenue", date(2026, 6, 1)) is None


class TestChainRecency:
    """The concept chain is ordered by preference, not by recency.

    Returning the first hit meant a concept a company stopped tagging
    years ago outranked one it still tags today. Measured on 300 cached
    tickers: the oldest fact still being served was 5,577 days old.
    """

    @staticmethod
    def _annual(
        concept: str,
        *,
        value: float,
        fy: int,
        accession: str,
    ) -> XbrlFact:
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
            accession_number=accession,
        )

    def test_freshest_concept_wins_over_chain_order(
        self, tmp_path: Path
    ) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "ACME",
            [
                # First in the chain, but the company stopped tagging it.
                self._annual(
                    "RevenueFromContractWithCustomerExcludingAssessedTax",
                    value=400.0,
                    fy=2016,
                    accession="acc-old",
                ),
                # Later in the chain, and current.
                self._annual("Revenues", value=1_800.0, fy=2025, accession="acc-new"),
            ],
        )
        fetcher = FundamentalsFetcher(cache=cache, client=None)

        fact = fetcher.get_field("ACME", "revenue", date(2026, 8, 4))

        assert fact is not None
        assert fact.value == 1_800.0
        assert fact.period_end == date(2025, 12, 31)

    def test_chain_order_still_wins_when_both_are_current(
        self, tmp_path: Path
    ) -> None:
        # Same period_end: preference order must decide, unchanged.
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "ACME",
            [
                self._annual(
                    "RevenueFromContractWithCustomerExcludingAssessedTax",
                    value=1_000.0,
                    fy=2025,
                    accession="acc-preferred",
                ),
                self._annual("Revenues", value=1_050.0, fy=2025, accession="acc-alt"),
            ],
        )
        fetcher = FundamentalsFetcher(cache=cache, client=None)

        fact = fetcher.get_field("ACME", "revenue", date(2026, 8, 4))

        assert fact is not None
        assert fact.value == 1_000.0

    def test_every_concept_stale_yields_none(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "DORMANT",
            [
                self._annual("Revenues", value=90.0, fy=2011, accession="acc-2011"),
                self._annual(
                    "SalesRevenueNet", value=95.0, fy=2012, accession="acc-2012"
                ),
            ],
        )
        fetcher = FundamentalsFetcher(cache=cache, client=None)

        assert fetcher.get_field("DORMANT", "revenue", date(2026, 8, 4)) is None

    def test_age_bound_boundary(self, tmp_path: Path) -> None:
        as_of = date(2026, 8, 4)
        cutoff = as_of - timedelta(days=MAX_FACT_AGE_DAYS)

        inside = EdgarCache(cache_dir=tmp_path / "inside")
        inside.save_facts(
            "ACME",
            [
                XbrlFact(
                    concept="Revenues",
                    namespace="us-gaap",
                    unit="USD",
                    value=500.0,
                    period_start=cutoff - timedelta(days=365),
                    period_end=cutoff,
                    filed=cutoff + timedelta(days=45),
                    form="10-K",
                    fiscal_year=cutoff.year,
                    fiscal_period="FY",
                    accession_number="acc-edge",
                )
            ],
        )
        assert (
            FundamentalsFetcher(cache=inside, client=None).get_field(
                "ACME", "revenue", as_of
            )
            is not None
        )

        outside = EdgarCache(cache_dir=tmp_path / "outside")
        outside.save_facts(
            "ACME",
            [
                XbrlFact(
                    concept="Revenues",
                    namespace="us-gaap",
                    unit="USD",
                    value=500.0,
                    period_start=cutoff - timedelta(days=366),
                    period_end=cutoff - timedelta(days=1),
                    filed=cutoff + timedelta(days=44),
                    form="10-K",
                    fiscal_year=cutoff.year,
                    fiscal_period="FY",
                    accession_number="acc-past",
                )
            ],
        )
        assert (
            FundamentalsFetcher(cache=outside, client=None).get_field(
                "ACME", "revenue", as_of
            )
            is None
        )

    def test_late_filer_keeps_its_annual_figure(self, tmp_path: Path) -> None:
        # FY2025 ends 2025-12-31 and stays the newest annual fact until
        # the FY2026 10-K lands. A 15-month-old period_end is normal, not
        # stale, and must survive.
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "SLOWCO",
            [self._annual("Revenues", value=770.0, fy=2025, accession="acc-fy25")],
        )
        fetcher = FundamentalsFetcher(cache=cache, client=None)

        fact = fetcher.get_field("SLOWCO", "revenue", date(2027, 3, 20))

        assert fact is not None
        assert fact.value == 770.0


class TestUnitFilter:
    """A foreign private issuer files in its home currency, and those
    figures are divided straight into a USD share price. Measured on 400
    cached tickers: 5 resolved CAD across every monetary field."""

    @staticmethod
    def _fact_in(unit: str, value: float, concept: str = "Revenues") -> XbrlFact:
        return XbrlFact(
            concept=concept,
            namespace="us-gaap",
            unit=unit,
            value=value,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            filed=date(2026, 2, 15),
            form="10-K",
            fiscal_year=2025,
            fiscal_period="FY",
            accession_number=f"acc-{unit}",
        )

    def test_cad_only_filer_yields_none(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("ENBFF", [self._fact_in("CAD", 55_000.0)])
        fetcher = FundamentalsFetcher(cache=cache, client=None)

        assert fetcher.get_field("ENBFF", "revenue", date(2026, 8, 4)) is None

    def test_usd_wins_when_a_filer_reports_both(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "DUAL",
            [self._fact_in("CAD", 55_000.0), self._fact_in("USD", 40_000.0)],
        )
        fetcher = FundamentalsFetcher(cache=cache, client=None)

        fact = fetcher.get_field("DUAL", "revenue", date(2026, 8, 4))

        assert fact is not None
        assert fact.unit == "USD"
        assert fact.value == 40_000.0

    def test_eps_requires_per_share_units(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "ACME",
            [
                self._fact_in(
                    "CAD/shares", 3.20, concept="EarningsPerShareDiluted"
                ),
                self._fact_in(
                    "USD/shares", 2.35, concept="EarningsPerShareDiluted"
                ),
            ],
        )
        fetcher = FundamentalsFetcher(cache=cache, client=None)

        fact = fetcher.get_field("ACME", "eps_diluted", date(2026, 8, 4))

        assert fact is not None
        assert fact.unit == "USD/shares"
        assert fact.value == 2.35

    def test_share_count_uses_the_shares_unit(self, tmp_path: Path) -> None:
        # shares_outstanding is a count, not money — a USD mask would
        # reject every filer.
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "ACME",
            [
                XbrlFact(
                    concept="CommonStockSharesOutstanding",
                    namespace="us-gaap",
                    unit="shares",
                    value=1_000_000.0,
                    period_start=None,
                    period_end=date(2026, 3, 31),
                    filed=date(2026, 4, 30),
                    form="10-Q",
                    fiscal_year=2026,
                    fiscal_period="Q1",
                    accession_number="acc-shares",
                )
            ],
        )
        fetcher = FundamentalsFetcher(cache=cache, client=None)

        fact = fetcher.get_field("ACME", "shares_outstanding", date(2026, 8, 4))

        assert fact is not None
        assert fact.value == 1_000_000.0

    def test_expected_units_covers_every_mapped_field(self) -> None:
        for field in CONCEPT_MAP:
            assert expected_units(field), field


class TestTotalDebt:
    """total_debt mapped to DebtCurrentAndNoncurrent, which 0 of 400
    sampled tickers tag, so it always fell through to long_term_debt —
    LongTermDebtNoncurrent — dropping current maturities and every
    short-term borrowing. Measured: 42% of companies understated."""

    @staticmethod
    def _instant(concept: str, value: float) -> XbrlFact:
        return XbrlFact(
            concept=concept,
            namespace="us-gaap",
            unit="USD",
            value=value,
            period_start=None,
            period_end=date(2026, 3, 31),
            filed=date(2026, 4, 30),
            form="10-Q",
            fiscal_year=2026,
            fiscal_period="Q1",
            accession_number=f"acc-{concept}",
        )

    def _fetcher(self, tmp_path: Path, *facts: XbrlFact) -> FundamentalsFetcher:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("ACME", list(facts))
        return FundamentalsFetcher(cache=cache, client=None)

    AS_OF = date(2026, 8, 4)

    def test_split_components_are_summed(self, tmp_path: Path) -> None:
        f = self._fetcher(
            tmp_path,
            self._instant("LongTermDebtNoncurrent", 800.0),
            self._instant("LongTermDebtCurrent", 150.0),
        )
        total, source = f._compute_total_debt("ACME", self.AS_OF)
        assert total == 950.0
        assert source == "split"

    def test_rollup_is_not_added_to_its_own_components(
        self, tmp_path: Path
    ) -> None:
        # US GAAP defines LongTermDebt as including current maturities.
        # Adding it to the split would double-count the whole balance.
        f = self._fetcher(
            tmp_path,
            self._instant("LongTermDebtNoncurrent", 800.0),
            self._instant("LongTermDebtCurrent", 150.0),
            self._instant("LongTermDebt", 950.0),
        )
        total, source = f._compute_total_debt("ACME", self.AS_OF)
        assert total == 950.0
        assert source == "split"

    def test_rollup_used_when_the_split_is_unavailable(
        self, tmp_path: Path
    ) -> None:
        f = self._fetcher(tmp_path, self._instant("LongTermDebt", 1_200.0))
        total, source = f._compute_total_debt("ACME", self.AS_OF)
        assert total == 1_200.0
        assert source == "rollup"

    def test_short_term_borrowings_are_added(self, tmp_path: Path) -> None:
        f = self._fetcher(
            tmp_path,
            self._instant("LongTermDebtNoncurrent", 800.0),
            self._instant("LongTermDebtCurrent", 150.0),
            self._instant("ShortTermBorrowings", 300.0),
        )
        total, source = f._compute_total_debt("ACME", self.AS_OF)
        assert total == 1_250.0
        assert source == "split+short_term"

    def test_short_term_only_filer_is_no_longer_debt_free(
        self, tmp_path: Path
    ) -> None:
        # 15 of 300 sampled tickers looked debt-free purely because
        # their only borrowing was short-term.
        f = self._fetcher(tmp_path, self._instant("ShortTermBorrowings", 500.0))
        total, source = f._compute_total_debt("ACME", self.AS_OF)
        assert total == 500.0
        assert source == "absent+short_term"

    def test_no_debt_concept_yields_none_not_zero(self, tmp_path: Path) -> None:
        f = self._fetcher(tmp_path, self._instant("Assets", 5_000.0))
        total, source = f._compute_total_debt("ACME", self.AS_OF)
        assert total is None
        assert source == "absent"

    def test_get_all_fields_uses_the_composed_figure(
        self, tmp_path: Path
    ) -> None:
        f = self._fetcher(
            tmp_path,
            self._instant("LongTermDebtNoncurrent", 800.0),
            self._instant("LongTermDebtCurrent", 150.0),
            self._instant("ShortTermBorrowings", 300.0),
        )
        values, _ = f.get_all_fields("ACME", self.AS_OF)
        assert values["total_debt"] == 1_250.0

    def test_stale_debt_facts_are_ignored(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "DORMANT",
            [
                XbrlFact(
                    concept="LongTermDebt",
                    namespace="us-gaap",
                    unit="USD",
                    value=900.0,
                    period_start=None,
                    period_end=date(2012, 12, 31),
                    filed=date(2013, 2, 15),
                    form="10-K",
                    fiscal_year=2012,
                    fiscal_period="FY",
                    accession_number="acc-old",
                )
            ],
        )
        f = FundamentalsFetcher(cache=cache, client=None)
        total, source = f._compute_total_debt("DORMANT", self.AS_OF)
        assert total is None
        assert source == "absent"
