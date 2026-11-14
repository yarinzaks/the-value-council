"""Unit tests for FundamentalsFetcher and CachedEdgarAdapter."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from core.backtest.point_in_time import FilingMetadata
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
from core.data.sic_codes import sic_for


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

    def test_commercial_paper_counts_when_the_rollup_is_absent(
        self, tmp_path: Path
    ) -> None:
        """Apple's case, and it was being dropped.

        AAPL has never tagged ShortTermBorrowings. On 2026-08-01 it
        reported $7.979bn of CommercialPaper against $90.678bn of
        long-term debt, and total_debt came back as 90.678 — 8.1% light,
        in the field that feeds the leverage gate. Across 300 sampled
        issuers, 1.7% have short-term debt reachable only through this
        tag or OtherShortTermBorrowings.
        """
        f = self._fetcher(
            tmp_path,
            self._instant("LongTermDebtNoncurrent", 800.0),
            self._instant("LongTermDebtCurrent", 150.0),
            self._instant("CommercialPaper", 75.0),
        )
        total, source = f._compute_total_debt("ACME", self.AS_OF)
        assert total == 1_025.0
        assert source == "split+short_term_components"

    def test_other_short_term_borrowings_count_too(self, tmp_path: Path) -> None:
        f = self._fetcher(
            tmp_path,
            self._instant("LongTermDebt", 500.0),
            self._instant("OtherShortTermBorrowings", 40.0),
        )
        total, source = f._compute_total_debt("ACME", self.AS_OF)
        assert total == 540.0
        assert source == "rollup+short_term_components"

    def test_components_are_summed_with_each_other(self, tmp_path: Path) -> None:
        f = self._fetcher(
            tmp_path,
            self._instant("LongTermDebt", 500.0),
            self._instant("CommercialPaper", 75.0),
            self._instant("OtherShortTermBorrowings", 40.0),
        )
        total, _ = f._compute_total_debt("ACME", self.AS_OF)
        assert total == 615.0

    def test_rollup_wins_over_its_own_components(self, tmp_path: Path) -> None:
        """ShortTermBorrowings is defined to include commercial paper.

        A filer that tags both must not have it counted twice — the same
        guard the long-term side already has.
        """
        f = self._fetcher(
            tmp_path,
            self._instant("LongTermDebt", 500.0),
            self._instant("ShortTermBorrowings", 100.0),
            self._instant("CommercialPaper", 75.0),
        )
        total, source = f._compute_total_debt("ACME", self.AS_OF)
        assert total == 600.0
        assert source == "rollup+short_term"

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


class TestDebtRollupsAndInstruments:
    """Audit FUND-4: ten holdings carried debt in concepts the composer
    never read, so total_debt came back ``None`` and the leverage rule
    scored them D/E 0.00. Values below are the ones the real cache holds
    for those filers at their FY2025 balance sheet (USD millions)."""

    AS_OF = date(2026, 11, 13)

    @staticmethod
    def _at(
        concept: str,
        value: float,
        period_end: date = date(2025, 12, 31),
        filed: date = date(2026, 2, 20),
    ) -> XbrlFact:
        return XbrlFact(
            concept=concept,
            namespace="us-gaap",
            unit="USD",
            value=value,
            period_start=None,
            period_end=period_end,
            filed=filed,
            form="10-K",
            fiscal_year=period_end.year,
            fiscal_period="FY",
            accession_number=f"acc-{concept}-{period_end.isoformat()}",
        )

    def _total(self, tmp_path: Path, *facts: XbrlFact) -> tuple[float | None, str]:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("ACME", list(facts))
        fetcher = FundamentalsFetcher(cache=cache, client=None)
        return fetcher._compute_total_debt("ACME", self.AS_OF)

    def test_fisv_debt_is_read_from_rollup(self, tmp_path: Path) -> None:
        # FISV tags its whole debt only as DebtAndCapitalLeaseObligations
        # (plus the noncurrent/current split of the same figure).
        total, source = self._total(
            tmp_path,
            self._at("DebtAndCapitalLeaseObligations", 28_997.0),
            self._at("LongTermDebtAndCapitalLeaseObligations", 27_758.0),
            self._at("LongTermDebtAndCapitalLeaseObligationsCurrent", 1_239.0),
        )
        assert total == 28_997.0
        assert source == "debt_and_lease_total"

    def test_debt_and_lease_total_is_not_topped_up_with_short_term(
        self, tmp_path: Path
    ) -> None:
        # DebtAndCapitalLeaseObligations is defined as short- AND
        # long-term debt; adding ShortTermBorrowings would count it twice.
        total, source = self._total(
            tmp_path,
            self._at("DebtAndCapitalLeaseObligations", 1_000.0),
            self._at("ShortTermBorrowings", 200.0),
        )
        assert total == 1_000.0
        assert source == "debt_and_lease_total"

    def test_lease_inclusive_split_is_summed(self, tmp_path: Path) -> None:
        # APH / HRI / WH: noncurrent plus current maturities.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtAndCapitalLeaseObligations", 14_564.8),
            self._at("LongTermDebtAndCapitalLeaseObligationsCurrent", 937.2),
        )
        assert total == pytest.approx(15_502.0)
        assert source == "lease_split"

    def test_lease_inclusive_rollup_is_not_added_to_its_split(
        self, tmp_path: Path
    ) -> None:
        total, source = self._total(
            tmp_path,
            self._at(
                "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                9_717.0,
            ),
            self._at("LongTermDebtAndCapitalLeaseObligations", 8_648.0),
            self._at("LongTermDebtAndCapitalLeaseObligationsCurrent", 1_058.0),
        )
        assert total == 9_717.0
        assert source == "lease_rollup"

    def test_lease_inclusive_long_term_takes_short_term_borrowings(
        self, tmp_path: Path
    ) -> None:
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtAndCapitalLeaseObligations", 800.0),
            self._at("LongTermDebtAndCapitalLeaseObligationsCurrent", 100.0),
            self._at("CommercialPaper", 50.0),
        )
        assert total == 950.0
        assert source == "lease_split+short_term_components"

    def test_fresher_lease_inclusive_total_beats_a_stale_long_term_debt(
        self, tmp_path: Path
    ) -> None:
        # A filer that moved from LongTermDebt to the lease-inclusive
        # element: the abandoned tag is still inside the age bound, but
        # it describes a balance sheet a year older.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebt", 3_000.0, period_end=date(2024, 12, 31),
                     filed=date(2025, 2, 20)),
            self._at("LongTermDebtAndCapitalLeaseObligations", 7_958.0),
            self._at("LongTermDebtAndCapitalLeaseObligationsCurrent", 27.0),
        )
        assert total == 7_985.0
        assert source == "lease_split"

    def test_same_period_long_term_debt_keeps_precedence(
        self, tmp_path: Path
    ) -> None:
        # Existing behaviour: a filer that tags LongTermDebt at the same
        # balance-sheet date is still read from LongTermDebt.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebt", 900.0),
            self._at("LongTermDebtAndCapitalLeaseObligations", 950.0),
        )
        assert total == 900.0
        assert source == "rollup"

    def test_convertible_notes_are_debt(self, tmp_path: Path) -> None:
        # RPD: its only borrowing is a convertible.
        total, source = self._total(
            tmp_path,
            self._at("ConvertibleDebtNoncurrent", 892.3),
            self._at("ConvertibleDebtCurrent", 0.0),
        )
        assert total == pytest.approx(892.3)
        assert source == "instruments"

    def test_senior_notes_split_is_summed(self, tmp_path: Path) -> None:
        # ALRM: notes due within a year sit in SeniorNotesCurrent.
        total, source = self._total(
            tmp_path,
            self._at("SeniorLongTermNotes", 489.6),
            self._at("SeniorNotesCurrent", 499.9),
        )
        assert total == pytest.approx(989.5)
        assert source == "instruments"

    def test_instrument_families_are_summed(self, tmp_path: Path) -> None:
        # COLL: a term loan and a convertible, tagged separately.
        total, source = self._total(
            tmp_path,
            self._at("LongTermLoansPayable", 542.1),
            self._at("LoansPayableCurrent", 29.0),
            self._at("ConvertibleLongTermNotesPayable", 238.2),
        )
        assert total == pytest.approx(809.3)
        assert source == "instruments"

    def test_line_of_credit_and_notes_count(self, tmp_path: Path) -> None:
        # GOLD: a drawn revolver plus small notes; CRTO: notes only.
        total, _ = self._total(
            tmp_path,
            self._at("LongTermLineOfCredit", 300.0),
            self._at("LongTermNotesPayable", 3.3),
            self._at("NotesPayableCurrent", 4.0),
        )
        assert total == pytest.approx(307.3)

    def test_instrument_rollup_is_not_added_to_its_split(
        self, tmp_path: Path
    ) -> None:
        total, _ = self._total(
            tmp_path,
            self._at("ConvertibleNotesPayable", 500.0),
            self._at("ConvertibleLongTermNotesPayable", 450.0),
            self._at("ConvertibleNotesPayableCurrent", 50.0),
        )
        assert total == 500.0

    def test_instruments_are_not_added_to_a_long_term_debt_total(
        self, tmp_path: Path
    ) -> None:
        # LongTermDebt already contains the notes; the instrument tier is
        # a fallback only, or a filer that tags both counts them twice.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebt", 1_000.0),
            self._at("ConvertibleDebtNoncurrent", 400.0),
        )
        assert total == 1_000.0
        assert source == "rollup"

    def test_instruments_take_short_term_borrowings(self, tmp_path: Path) -> None:
        total, source = self._total(
            tmp_path,
            self._at("ConvertibleDebtNoncurrent", 400.0),
            self._at("ShortTermBorrowings", 60.0),
        )
        assert total == 460.0
        assert source == "instruments+short_term"

    def test_complete_lease_shape_beats_a_newer_one_sided_shape(
        self, tmp_path: Path
    ) -> None:
        # A 10-Q that re-tags only the noncurrent half: taking it because
        # it is newer drops the current maturities the 10-K roll-up holds.
        total, source = self._total(
            tmp_path,
            self._at(
                "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                9_717.0,
            ),
            self._at("LongTermDebtAndCapitalLeaseObligations", 8_648.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
        )
        assert total == 9_717.0
        assert source == "lease_rollup"

    def test_complete_instrument_shape_beats_a_newer_one_sided_shape(
        self, tmp_path: Path
    ) -> None:
        total, source = self._total(
            tmp_path,
            self._at("SeniorNotes", 989.5),
            self._at("SeniorLongTermNotes", 489.6,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
        )
        assert total == pytest.approx(989.5)
        assert source == "instruments"

    def test_newer_complete_shape_still_beats_an_older_complete_one(
        self, tmp_path: Path
    ) -> None:
        total, _ = self._total(
            tmp_path,
            self._at("SeniorNotes", 989.5),
            self._at("SeniorLongTermNotes", 450.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
            self._at("SeniorNotesCurrent", 500.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
        )
        assert total == 950.0

    def test_instruments_are_read_when_long_term_debt_is_zero(
        self, tmp_path: Path
    ) -> None:
        # A LongTermDebt of zero beside $990M of senior notes is not a
        # debt-free balance sheet; the larger figure is the debt.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebt", 0.0),
            self._at("SeniorLongTermNotes", 489.6),
            self._at("SeniorNotesCurrent", 499.9),
        )
        assert total == pytest.approx(989.5)
        assert source == "instruments"

    def test_a_reported_zero_with_no_instruments_stays_zero(
        self, tmp_path: Path
    ) -> None:
        total, source = self._total(tmp_path, self._at("LongTermDebt", 0.0))
        assert total == 0.0
        assert source == "rollup"

    def test_overlapping_note_families_are_not_double_counted(
        self, tmp_path: Path
    ) -> None:
        # NotesPayable is defined as all notes payable, senior notes and
        # convertible notes included: one figure per overlap group.
        total, source = self._total(
            tmp_path,
            self._at("SeniorNotes", 600.0),
            self._at("NotesPayable", 1_000.0),
            self._at("ConvertibleNotesPayable", 400.0),
        )
        assert total == 1_000.0
        assert source == "instruments"

    def test_distinct_senior_and_convertible_notes_are_summed(
        self, tmp_path: Path
    ) -> None:
        # Senior and convertible notes are separate borrowings; only a
        # NotesPayable total can contain both.
        total, source = self._total(
            tmp_path,
            self._at("SeniorNotes", 990.0),
            self._at("ConvertibleDebt", 400.0),
        )
        assert total == 1_390.0
        assert source == "instruments"

    def test_notes_total_smaller_than_its_parts_does_not_cap_them(
        self, tmp_path: Path
    ) -> None:
        # A NotesPayable below senior + convertible cannot contain both;
        # the larger reading is kept so debt is never understated.
        total, _ = self._total(
            tmp_path,
            self._at("SeniorNotes", 990.0),
            self._at("NotesPayable", 1_000.0),
            self._at("ConvertibleNotesPayable", 400.0),
        )
        assert total == 1_390.0

    def test_zero_debt_and_lease_total_falls_back_to_instruments(
        self, tmp_path: Path
    ) -> None:
        total, source = self._total(
            tmp_path,
            self._at("DebtAndCapitalLeaseObligations", 0.0),
            self._at("SeniorNotes", 990.0),
        )
        assert total == 990.0
        assert source == "instruments"

    def test_zero_debt_and_lease_total_with_no_instruments_stays_zero(
        self, tmp_path: Path
    ) -> None:
        total, source = self._total(
            tmp_path,
            self._at("DebtAndCapitalLeaseObligations", 0.0),
            self._at("ShortTermBorrowings", 50.0),
        )
        assert total == 0.0
        assert source == "debt_and_lease_total"

    def test_newer_larger_one_sided_shape_beats_an_older_complete_one(
        self, tmp_path: Path
    ) -> None:
        # Completeness guards against a newer one-sided reading dropping
        # current maturities; it must not let a stale roll-up hide debt
        # that has since grown past it.
        total, _ = self._total(
            tmp_path,
            self._at("SeniorNotes", 500.0),
            self._at("SeniorLongTermNotes", 2_000.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
        )
        assert total == 2_000.0

    def test_complete_lease_rollup_beats_a_newer_one_sided_long_term_debt(
        self, tmp_path: Path
    ) -> None:
        # Completeness before recency holds across the LongTermDebt and
        # lease-inclusive families too: a 10-Q that re-tags only a sliver
        # of noncurrent debt must not replace the complete 10-K total.
        total, source = self._total(
            tmp_path,
            self._at(
                "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                9_717.0,
            ),
            self._at("LongTermDebtNoncurrent", 100.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
        )
        assert total == 9_717.0
        assert source == "lease_rollup"

    def test_complete_long_term_debt_beats_a_newer_one_sided_lease_shape(
        self, tmp_path: Path
    ) -> None:
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebt", 9_717.0),
            self._at("LongTermDebtAndCapitalLeaseObligations", 8_648.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
        )
        assert total == 9_717.0
        assert source == "rollup"

    def test_newer_larger_one_sided_lease_shape_beats_older_long_term_debt(
        self, tmp_path: Path
    ) -> None:
        # The same exception as inside a family: debt that grew past the
        # stale complete total is not hidden by it.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebt", 500.0),
            self._at("LongTermDebtAndCapitalLeaseObligations", 2_000.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
        )
        assert total == 2_000.0
        assert source == "lease_noncurrent_only"

    def test_small_one_sided_long_term_debt_does_not_block_instruments(
        self, tmp_path: Path
    ) -> None:
        # CRTO-shaped: only $37M of current maturities is tagged as
        # LongTermDebt, the senior notes sit in their own elements.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtCurrent", 37.0),
            self._at("SeniorNotes", 990.0),
        )
        assert total == 990.0
        assert source == "instruments"

    def test_small_one_sided_lease_shape_does_not_block_instruments(
        self, tmp_path: Path
    ) -> None:
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtAndCapitalLeaseObligationsCurrent", 37.0),
            self._at("SeniorNotes", 990.0),
        )
        assert total == 990.0
        assert source == "instruments"

    def test_larger_one_sided_long_term_debt_beats_smaller_instruments(
        self, tmp_path: Path
    ) -> None:
        # Same half: the larger reading is kept, never the sum, because
        # the one-sided total may already contain the instrument lines.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtNoncurrent", 1_000.0),
            self._at("SeniorLongTermNotes", 400.0),
        )
        assert total == 1_000.0
        assert source == "noncurrent_only"

    def test_noncurrent_long_term_debt_and_current_instruments_are_summed(
        self, tmp_path: Path
    ) -> None:
        # Opposite halves are disjoint: LongTermDebtNoncurrent excludes
        # the current maturities SeniorNotesCurrent reports, so keeping
        # the larger dropped the 37.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtNoncurrent", 1_000.0),
            self._at("SeniorNotesCurrent", 37.0),
        )
        assert total == 1_037.0
        assert source == "noncurrent_only+instruments"

    def test_current_long_term_debt_and_noncurrent_instruments_are_summed(
        self, tmp_path: Path
    ) -> None:
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtCurrent", 37.0),
            self._at("SeniorLongTermNotes", 990.0),
        )
        assert total == 1_027.0
        assert source == "current_only+instruments"

    def test_mixed_half_instruments_are_not_summed_with_a_one_sided_total(
        self, tmp_path: Path
    ) -> None:
        # The loans roll-up covers both halves, so it may hold the 37 of
        # current maturities: only wholly disjoint halves are summed.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtCurrent", 37.0),
            self._at("SeniorLongTermNotes", 500.0),
            self._at("LoansPayable", 200.0),
        )
        assert total == 700.0
        assert source == "instruments"

    def test_ltd_split_is_dated_by_its_oldest_component(
        self, tmp_path: Path
    ) -> None:
        # A split is only as current as its oldest half, the same rule
        # the lease shapes use. Dating it by its newest half made a split
        # half from 2025-09-30 outrank a 2025-12-31 lease roll-up.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtNoncurrent", 100.0,
                     period_end=date(2025, 9, 30), filed=date(2025, 11, 5)),
            self._at("LongTermDebtCurrent", 37.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
            self._at(
                "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                9_717.0,
            ),
        )
        assert total == 9_717.0
        assert source == "lease_rollup"

    def test_ltd_split_with_a_newer_current_half_does_not_beat_lease_rollup(
        self, tmp_path: Path
    ) -> None:
        # Reviewer probe: the split's oldest half is the FY balance
        # sheet, a tie with the roll-up, and the larger complete wins.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtNoncurrent", 100.0),
            self._at("LongTermDebtCurrent", 37.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
            self._at(
                "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                9_717.0,
            ),
        )
        assert total == 9_717.0
        assert source == "lease_rollup"

    def test_complete_readings_tied_on_date_keep_the_larger(
        self, tmp_path: Path
    ) -> None:
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebt", 0.0),
            self._at(
                "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                9_717.0,
            ),
        )
        assert total == 9_717.0
        assert source == "lease_rollup"

    def test_one_sided_halves_from_both_families_are_summed(
        self, tmp_path: Path
    ) -> None:
        # A newer LongTermDebtCurrent sliver must not replace the 9,000 of
        # noncurrent lease-inclusive debt: the halves are disjoint.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtAndCapitalLeaseObligations", 9_000.0),
            self._at("LongTermDebtCurrent", 37.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
        )
        assert total == 9_037.0
        assert source == "current_only+lease_noncurrent_only"

    def test_same_half_one_sided_readings_keep_the_newer(
        self, tmp_path: Path
    ) -> None:
        # The same half from two families describes the same balance-sheet
        # line; the newer balance sheet is kept, as within a family.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtAndCapitalLeaseObligations", 9_000.0),
            self._at("LongTermDebtNoncurrent", 100.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
        )
        assert total == 100.0
        assert source == "noncurrent_only"

    def test_lease_win_does_not_mask_a_one_sided_long_term_debt(
        self, tmp_path: Path
    ) -> None:
        # The complete lease roll-up wins over the newer one-sided
        # LongTermDebtNoncurrent, but the LongTermDebt family was
        # one-sided, so the instrument lines are still read and the
        # larger figure kept.
        total, source = self._total(
            tmp_path,
            self._at(
                "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                500.0,
            ),
            self._at("LongTermDebtNoncurrent", 100.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
            self._at("SeniorNotes", 990.0),
        )
        assert total == 990.0
        assert source == "instruments"

    def test_completing_an_instrument_split_does_not_lower_the_total(
        self, tmp_path: Path
    ) -> None:
        # Reviewer probe x1: SeniorNotesCurrent is a current line, never
        # inside LongTermDebtNoncurrent. Adding SeniorLongTermNotes made
        # the senior split complete and the 37 disappeared into a max.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtNoncurrent", 1_000.0),
            self._at("SeniorNotesCurrent", 37.0),
            self._at("SeniorLongTermNotes", 400.0),
        )
        assert total == 1_037.0
        assert source == "noncurrent_only+instruments"

    def test_a_noncurrent_instrument_in_another_group_keeps_current_lines(
        self, tmp_path: Path
    ) -> None:
        # Reviewer probe x2: the noncurrent senior notes may sit inside
        # the noncurrent total; the current loans cannot.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtNoncurrent", 1_000.0),
            self._at("LoansPayableCurrent", 50.0),
            self._at("SeniorLongTermNotes", 400.0),
        )
        assert total == 1_050.0
        assert source == "noncurrent_only+instruments"

    def test_a_current_only_total_keeps_noncurrent_instrument_lines(
        self, tmp_path: Path
    ) -> None:
        # The mirror: a current-only total may contain the current loans,
        # never the noncurrent senior notes.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtCurrent", 37.0),
            self._at("SeniorLongTermNotes", 990.0),
            self._at("LoansPayableCurrent", 20.0),
        )
        assert total == 1_027.0
        assert source == "current_only+instruments"

    def test_overlap_alternative_is_chosen_by_the_combined_total(
        self, tmp_path: Path
    ) -> None:
        # Within an overlap group the larger alternative alone (40 of
        # noncurrent senior notes) may vanish into the noncurrent total,
        # while the smaller one (37 of current notes) is disjoint from it.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtNoncurrent", 1_000.0),
            self._at("NotesPayableCurrent", 37.0),
            self._at("SeniorLongTermNotes", 40.0),
        )
        assert total == 1_037.0
        assert source == "noncurrent_only+instruments"

    def test_instrument_rollup_beside_a_noncurrent_total_keeps_the_larger(
        self, tmp_path: Path
    ) -> None:
        # A roll-up covers both halves in unknown shares, so it may sit
        # inside the noncurrent total: kept as the larger, not summed.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtNoncurrent", 1_000.0),
            self._at("LoansPayable", 200.0),
        )
        assert total == 1_000.0
        assert source == "noncurrent_only"

    def test_larger_one_sided_debt_beside_a_lease_split_keeps_both_halves(
        self, tmp_path: Path
    ) -> None:
        # Reviewer probe x8: on the same balance sheet the complete lease
        # split replaced 1,000 of noncurrent debt with 437. The noncurrent
        # reading is kept and the split's current half, disjoint from it,
        # is added.
        total, source = self._total(
            tmp_path,
            self._at("LongTermDebtNoncurrent", 1_000.0),
            self._at("LongTermDebtAndCapitalLeaseObligations", 400.0),
            self._at("LongTermDebtAndCapitalLeaseObligationsCurrent", 37.0),
        )
        assert total == 1_037.0
        assert source == "noncurrent_only+lease_current_only"

    def test_smaller_or_stale_one_sided_debt_does_not_split_a_lease_split(
        self, tmp_path: Path
    ) -> None:
        # A newer sliver smaller than the split's own noncurrent half, or
        # a larger reading from an older balance sheet, leaves the
        # complete split in charge, as before.
        newer_sliver = self._total(
            tmp_path / "sliver",
            self._at("LongTermDebtNoncurrent", 300.0,
                     period_end=date(2026, 3, 31), filed=date(2026, 5, 5)),
            self._at("LongTermDebtAndCapitalLeaseObligations", 400.0),
            self._at("LongTermDebtAndCapitalLeaseObligationsCurrent", 37.0),
        )
        stale_larger = self._total(
            tmp_path / "stale",
            self._at("LongTermDebtNoncurrent", 1_000.0,
                     period_end=date(2025, 9, 30), filed=date(2025, 11, 5)),
            self._at("LongTermDebtAndCapitalLeaseObligations", 400.0),
            self._at("LongTermDebtAndCapitalLeaseObligationsCurrent", 37.0),
        )
        assert newer_sliver == (437.0, "lease_split")
        assert stale_larger == (437.0, "lease_split")

    def test_overlap_group_is_still_summed_with_loans_and_credit_lines(
        self, tmp_path: Path
    ) -> None:
        total, _ = self._total(
            tmp_path,
            self._at("SeniorNotes", 990.0),
            self._at("NotesPayable", 1_000.0),
            self._at("LoansPayable", 200.0),
            self._at("LineOfCredit", 50.0),
        )
        assert total == 1_250.0

    def test_stale_rollups_and_instruments_are_ignored(
        self, tmp_path: Path
    ) -> None:
        old = date(2018, 12, 31)
        total, source = self._total(
            tmp_path,
            self._at("DebtAndCapitalLeaseObligations", 5_000.0, period_end=old,
                     filed=date(2019, 2, 20)),
            self._at("SeniorNotes", 700.0, period_end=old, filed=date(2019, 2, 20)),
        )
        assert total is None
        assert source == "absent"


class TestSicCodePopulation:
    """sic_code was hard-coded None, so Greenblatt's mandatory
    financials-and-utilities exclusion never fired once. Measured across
    the 8,290 cached tickers: 8,170 have a bundled SIC code and 2,623 of
    those (32%) fall in the excluded ranges."""

    def test_parse_financials_populates_sic(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "AAPL",
            [
                XbrlFact(
                    concept="Assets",
                    namespace="us-gaap",
                    unit="USD",
                    value=1_000.0,
                    period_start=None,
                    period_end=date(2026, 3, 31),
                    filed=date(2026, 4, 30),
                    form="10-Q",
                    fiscal_year=2026,
                    fiscal_period="Q1",
                    accession_number="acc-1",
                )
            ],
        )
        adapter = CachedEdgarAdapter(
            fetcher=FundamentalsFetcher(cache=cache, client=None)
        )
        filing = FilingMetadata(
            ticker="AAPL",
            cik="320193",
            form_type="10-Q",
            filing_date=date(2026, 4, 30),
            period_of_report=date(2026, 3, 31),
            accession_number="acc-1",
        )

        payload = adapter.parse_financials(filing)

        assert payload["sic_code"] == sic_for("AAPL")
        assert payload["sic_code"] is not None

    def test_unknown_ticker_still_yields_none(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "ZZZQQ",
            [
                XbrlFact(
                    concept="Assets",
                    namespace="us-gaap",
                    unit="USD",
                    value=1.0,
                    period_start=None,
                    period_end=date(2026, 3, 31),
                    filed=date(2026, 4, 30),
                    form="10-Q",
                    fiscal_year=2026,
                    fiscal_period="Q1",
                    accession_number="acc-z",
                )
            ],
        )
        adapter = CachedEdgarAdapter(
            fetcher=FundamentalsFetcher(cache=cache, client=None)
        )
        filing = FilingMetadata(
            ticker="ZZZQQ",
            cik="0",
            form_type="10-Q",
            filing_date=date(2026, 4, 30),
            period_of_report=date(2026, 3, 31),
            accession_number="acc-z",
        )

        assert adapter.parse_financials(filing)["sic_code"] is None

    def test_a_bank_is_now_excluded_by_greenblatt(self) -> None:
        # The rule existed and was correct; it was simply never fed.
        from agents.greenblatt.filters import is_excluded_sector

        bank_sic = sic_for("JPM")
        assert bank_sic is not None
        assert is_excluded_sector(str(bank_sic))

    def test_an_operating_company_is_not_excluded(self) -> None:
        from agents.greenblatt.filters import is_excluded_sector

        assert not is_excluded_sector(str(sic_for("AAPL")))


class TestNetCash:
    """Gate A's second path: a floor you read off the balance sheet.

    The three inputs are treated differently on purpose, and the reason
    is Cal-Maine: a company that repays its debt stops tagging the
    concept, so the debt-free balance sheets this screen exists to find
    are exactly the ones a strict reading calls unreadable.
    """

    @staticmethod
    def _cache(tmp_path: Path, facts: list[XbrlFact]) -> EdgarCache:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("NC", facts)
        return cache

    @staticmethod
    def _fetcher(cache: EdgarCache) -> FundamentalsFetcher:
        from core.data.fundamentals_fetcher import FundamentalsFetcherConfig

        return FundamentalsFetcher(
            cache=cache,
            config=FundamentalsFetcherConfig(populate_cache_on_miss=False),
        )

    AS_OF = date(2021, 3, 1)

    def test_short_term_investments_are_counted(self, tmp_path: Path) -> None:
        # Without the concept chain this reads 300m instead of 800m, and
        # a net-cash-rich small cap fails the gate it was built for.
        cache = self._cache(
            tmp_path,
            [
                _fact(concept="CashAndCashEquivalentsAtCarryingValue", value=300.0),
                _fact(concept="ShortTermInvestments", value=500.0),
                _fact(concept="Assets", value=5_000.0),
            ],
        )
        assert self._fetcher(cache).net_cash("NC", self.AS_OF) == 800.0

    def test_debt_is_subtracted(self, tmp_path: Path) -> None:
        cache = self._cache(
            tmp_path,
            [
                _fact(concept="CashAndCashEquivalentsAtCarryingValue", value=300.0),
                _fact(concept="ShortTermInvestments", value=500.0),
                _fact(concept="LongTermDebtNoncurrent", value=200.0),
                _fact(concept="Assets", value=5_000.0),
            ],
        )
        assert self._fetcher(cache).net_cash("NC", self.AS_OF) == 600.0

    def test_absent_investments_default_to_zero(self, tmp_path: Path) -> None:
        """Understating net cash fails a name; overstating buys one."""
        cache = self._cache(
            tmp_path,
            [
                _fact(concept="CashAndCashEquivalentsAtCarryingValue", value=300.0),
                _fact(concept="Assets", value=5_000.0),
            ],
        )
        assert self._fetcher(cache).net_cash("NC", self.AS_OF) == 300.0

    def test_absent_cash_is_unreadable_not_zero(self, tmp_path: Path) -> None:
        cache = self._cache(tmp_path, [_fact(concept="Assets", value=5_000.0)])
        assert self._fetcher(cache).net_cash("NC", self.AS_OF) is None


class TestDebtWithZeroEvidence:
    _cache = staticmethod(TestNetCash._cache)
    _fetcher = staticmethod(TestNetCash._fetcher)
    AS_OF = date(2021, 3, 1)

    def test_a_reported_figure_is_returned_unchanged(self, tmp_path: Path) -> None:
        cache = self._cache(
            tmp_path,
            [
                _fact(concept="LongTermDebtNoncurrent", value=200.0),
                _fact(concept="Assets", value=5_000.0),
            ],
        )
        debt, source = self._fetcher(cache).debt_with_zero_evidence("NC", self.AS_OF)
        assert debt == 200.0
        assert source != "assumed_zero_balance_sheet_current"

    def test_a_current_balance_sheet_with_no_debt_tag_means_zero(
        self, tmp_path: Path
    ) -> None:
        """Cal-Maine: last debt tag 2019, balance sheet current to 2026."""
        stale = self.AS_OF - timedelta(days=MAX_FACT_AGE_DAYS + 400)
        cache = self._cache(
            tmp_path,
            [
                _fact(
                    concept="LongTermDebtNoncurrent",
                    value=999.0,
                    period_end=stale,
                    filed=stale + timedelta(days=45),
                ),
                _fact(concept="Assets", value=5_000.0),
            ],
        )
        debt, source = self._fetcher(cache).debt_with_zero_evidence("NC", self.AS_OF)
        assert debt == 0.0
        assert source == "assumed_zero_balance_sheet_current"

    def test_a_stale_balance_sheet_stays_unknown(self, tmp_path: Path) -> None:
        """No evidence either way is not evidence of no debt."""
        stale = self.AS_OF - timedelta(days=MAX_FACT_AGE_DAYS + 400)
        cache = self._cache(
            tmp_path,
            [
                _fact(
                    concept="Assets",
                    value=5_000.0,
                    period_end=stale,
                    filed=stale + timedelta(days=45),
                )
            ],
        )
        debt, source = self._fetcher(cache).debt_with_zero_evidence("NC", self.AS_OF)
        assert debt is None
        assert source == "absent"

    def test_an_assumed_zero_is_named_so_it_is_never_mistaken(
        self, tmp_path: Path
    ) -> None:
        cache = self._cache(tmp_path, [_fact(concept="Assets", value=5_000.0)])
        _debt, source = self._fetcher(cache).debt_with_zero_evidence("NC", self.AS_OF)
        assert "assumed" in source

    def test_the_leverage_path_is_left_alone(self, tmp_path: Path) -> None:
        """_compute_total_debt must keep answering None for the eleven.

        Unknown leverage is not safe leverage, and the other agents rely
        on that reading. Only the net-cash path resolves the ambiguity.
        """
        cache = self._cache(tmp_path, [_fact(concept="Assets", value=5_000.0)])
        fetcher = self._fetcher(cache)
        assert fetcher._compute_total_debt("NC", self.AS_OF)[0] is None


class TestInstantConceptsFollowTheNewestBalanceSheet:
    """Audit FUND-6: a newer 10-Q balance sheet beats an older 10-K one.

    Instant concepts were looked up with ``prefer_annual=True``, which
    keeps 10-K facts whenever any exist. 50 of 182 holdings were read
    from a balance sheet older than one already in the cache: AAPL's
    2025-09-27 10-K over its 2025-12-27 10-Q, CALM's 2025-05-31 10-K
    over its 2026-02-28 10-Q.
    """

    AS_OF = date(2026, 4, 15)

    @staticmethod
    def _instant(
        concept: str,
        value: float,
        *,
        form: str,
        namespace: str = "us-gaap",
        unit: str = "USD",
    ) -> XbrlFact:
        annual = form == "10-K"
        return XbrlFact(
            concept=concept,
            namespace=namespace,
            unit=unit,
            value=value,
            period_start=None,
            period_end=date(2025, 5, 31) if annual else date(2026, 2, 28),
            filed=date(2025, 7, 22) if annual else date(2026, 4, 1),
            form=form,
            fiscal_year=2025 if annual else 2026,
            fiscal_period="FY" if annual else "Q3",
            accession_number="acc-10k" if annual else "acc-10q",
        )

    def _fetcher(self, tmp_path: Path, *facts: XbrlFact) -> FundamentalsFetcher:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("CALM", list(facts))
        return FundamentalsFetcher(cache=cache, client=None)

    def test_newer_10q_balance_sheet_beats_older_10k(self, tmp_path: Path) -> None:
        f = self._fetcher(
            tmp_path,
            self._instant("Assets", 3_084_619_000.0, form="10-K"),
            self._instant("Assets", 3_139_261_000.0, form="10-Q"),
        )

        fact = f.get_field("CALM", "total_assets", self.AS_OF)

        assert fact is not None
        assert fact.value == 3_139_261_000.0
        assert fact.accession_number == "acc-10q"

    def test_concept_tagged_only_in_the_10k_still_resolves(
        self, tmp_path: Path
    ) -> None:
        """Failure mode: a 10-Q that omits a line must not blank it."""
        f = self._fetcher(
            tmp_path,
            self._instant("Goodwill", 44_000_000.0, form="10-K"),
            self._instant("Assets", 3_139_261_000.0, form="10-Q"),
        )

        fact = f.get_field("CALM", "goodwill", self.AS_OF)

        assert fact is not None
        assert fact.value == 44_000_000.0

    def test_10q_not_yet_filed_is_not_seen(self, tmp_path: Path) -> None:
        """Failure mode: preferring the newer filing must stay point-in-time."""
        f = self._fetcher(
            tmp_path,
            self._instant("Assets", 3_084_619_000.0, form="10-K"),
            self._instant("Assets", 3_139_261_000.0, form="10-Q"),
        )

        fact = f.get_field("CALM", "total_assets", date(2026, 3, 31))

        assert fact is not None
        assert fact.value == 3_084_619_000.0

    def test_debt_follows_the_same_balance_sheet(self, tmp_path: Path) -> None:
        """total_debt must not come from an older balance sheet than equity."""
        f = self._fetcher(
            tmp_path,
            self._instant("LongTermDebt", 900.0, form="10-K"),
            self._instant("LongTermDebt", 400.0, form="10-Q"),
            self._instant("Assets", 3_139_261_000.0, form="10-Q"),
        )

        total, source = f._compute_total_debt("CALM", self.AS_OF)

        assert total == 400.0
        assert source == "rollup"

    def test_cover_count_follows_the_newest_filing(self, tmp_path: Path) -> None:
        f = self._fetcher(
            tmp_path,
            self._instant(
                "EntityCommonStockSharesOutstanding", 48_497_480.0,
                form="10-K", namespace="dei", unit="shares",
            ),
            self._instant(
                "EntityCommonStockSharesOutstanding", 47_376_588.0,
                form="10-Q", namespace="dei", unit="shares",
            ),
        )

        fact = f.get_field("CALM", "shares_outstanding", self.AS_OF)

        assert fact is not None
        assert fact.value == 47_376_588.0


class TestPeriodOfReport:
    """Audit FUND-6: ``period_of_report`` was whichever row came first.

    ``list_filings`` took the first row of each accession, which for CALM
    was a cover-page fact: its 10-Q reported a period of 2026-04-01 (the
    share-count date) and its 10-K 2024-11-29 (the public-float date).
    """

    @staticmethod
    def _f(
        concept: str,
        period_end: date,
        *,
        namespace: str = "us-gaap",
        accession: str = "acc-10q",
        filed: date = date(2026, 4, 1),
        form: str = "10-Q",
    ) -> XbrlFact:
        return XbrlFact(
            concept=concept,
            namespace=namespace,
            unit="shares" if namespace == "dei" else "USD",
            value=1.0,
            period_start=None,
            period_end=period_end,
            filed=filed,
            form=form,
            fiscal_year=period_end.year,
            fiscal_period="Q3",
            accession_number=accession,
        )

    def _listed(self, tmp_path: Path, facts: list[XbrlFact]) -> dict[str, date | None]:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("CALM", facts)
        adapter = CachedEdgarAdapter(
            fetcher=FundamentalsFetcher(cache=cache, client=None)
        )
        return {
            f.accession_number: f.period_of_report
            for f in adapter.list_filings("CALM", form_types=("10-K", "10-Q"))
        }

    def test_cover_date_is_not_the_period(self, tmp_path: Path) -> None:
        q3, ye = date(2026, 2, 28), date(2025, 5, 31)
        listed = self._listed(
            tmp_path,
            [
                self._f("EntityCommonStockSharesOutstanding", date(2026, 4, 1), namespace="dei"),
                self._f("Assets", ye),
                self._f("Liabilities", ye),
                self._f("Assets", q3),
                self._f("Liabilities", q3),
                self._f("StockholdersEquity", q3),
            ],
        )

        assert listed == {"acc-10q": q3}

    def test_a_subsequent_event_fact_does_not_move_the_period(
        self, tmp_path: Path
    ) -> None:
        """Edge: one fact dated after quarter-end, before filing.

        Measured over 12,067 cached 10-K/10-Q accessions, the latest
        us-gaap period end was a subsequent-event date in some filings
        (XELB, NMHI, CNTM); a lone fact cannot outvote the statements.
        """
        q3 = date(2026, 2, 28)
        listed = self._listed(
            tmp_path,
            [
                self._f("Assets", q3),
                self._f("Liabilities", q3),
                self._f("StockholdersEquity", q3),
                self._f("DebtInstrumentFaceAmount", date(2026, 3, 15)),
            ],
        )

        assert listed == {"acc-10q": q3}

    def test_10k_period_is_the_current_year_not_the_larger_comparative(
        self, tmp_path: Path
    ) -> None:
        """Edge: a 10-K can tag a prior year slightly more often (VERI,
        SEGG, KANP); the most common period alone picks the comparative."""
        fy25, fy24 = date(2025, 5, 31), date(2024, 6, 1)

        def k(concept: str, period_end: date) -> XbrlFact:
            return self._f(
                concept, period_end, accession="acc-10k", filed=date(2025, 7, 22), form="10-K"
            )

        listed = self._listed(
            tmp_path,
            [
                k("Assets", fy24),
                k("Liabilities", fy24),
                k("StockholdersEquity", fy24),
                k("Assets", fy25),
                k("Liabilities", fy25),
            ],
        )

        assert listed == {"acc-10k": fy25}

    def test_filing_with_only_cover_facts_keeps_its_latest_date(
        self, tmp_path: Path
    ) -> None:
        listed = self._listed(
            tmp_path,
            [self._f("EntityCommonStockSharesOutstanding", date(2026, 4, 1), namespace="dei")],
        )

        assert listed == {"acc-10q": date(2026, 4, 1)}
