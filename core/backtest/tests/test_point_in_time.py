"""Point-in-time correctness tests.

The cornerstone test of the backtest engine: when querying for
financials as of date X, we must receive the data from the latest
filing whose ``filing_date <= X``, NOT data from a later filing.

We use a fake :class:`EdgarAdapter` so the test is fast, offline, and
deterministic.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.backtest.point_in_time import (
    _PAYLOAD_VERSION,
    _VERSION_KEY,
    EdgarAdapter,
    EdgartoolsAdapter,
    FilingMetadata,
    PointInTimeError,
    PointInTimeLoader,
)
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact
from core.data.fundamentals_fetcher import (
    CachedEdgarAdapter,
    FundamentalsFetcher,
    FundamentalsFetcherConfig,
)


class FakeAdapter(EdgarAdapter):
    """Test adapter returning hand-built filings."""

    def __init__(
        self,
        filings: dict[str, list[FilingMetadata]],
        financials: dict[str, dict[str, float | None]],
    ) -> None:
        self._filings = filings
        self._financials = financials
        self.parse_calls: list[str] = []

    def list_filings(
        self, ticker: str, *, form_types: tuple[str, ...]
    ) -> list[FilingMetadata]:
        return [
            f for f in self._filings.get(ticker.upper(), []) if f.form_type in form_types
        ]

    def parse_financials(
        self, filing: FilingMetadata
    ) -> dict[str, float | None]:
        self.parse_calls.append(filing.accession_number)
        return self._financials.get(filing.accession_number, {})


@pytest.fixture
def adapter() -> FakeAdapter:
    """AAPL fixture with two filings:
    - Q4 2014 10-K: filed 2015-01-30, period 2014-12-27. EPS_basic = 6.49.
    - Q1 2015 10-Q: filed 2015-04-25, period 2015-03-28. EPS_basic = 2.34.
    """
    filings = {
        "AAPL": [
            FilingMetadata(
                ticker="AAPL",
                cik="320193",
                form_type="10-K",
                filing_date=date(2015, 1, 30),
                period_of_report=date(2014, 12, 27),
                accession_number="ACC-10K-2014",
            ),
            FilingMetadata(
                ticker="AAPL",
                cik="320193",
                form_type="10-Q",
                filing_date=date(2015, 4, 25),
                period_of_report=date(2015, 3, 28),
                accession_number="ACC-10Q-2015Q1",
            ),
        ]
    }
    financials = {
        "ACC-10K-2014": {
            "revenue": 182_795_000_000.0,
            "net_income": 39_510_000_000.0,
            "eps_basic": 6.49,
            "eps_diluted": 6.45,
            "total_assets": 231_839_000_000.0,
            "total_equity": 111_547_000_000.0,
        },
        "ACC-10Q-2015Q1": {
            "revenue": 58_010_000_000.0,
            "net_income": 13_569_000_000.0,
            "eps_basic": 2.34,
            "eps_diluted": 2.33,
            "total_assets": 261_847_000_000.0,
            "total_equity": 113_640_000_000.0,
        },
    }
    return FakeAdapter(filings, financials)


@pytest.fixture
def loader(adapter: FakeAdapter, tmp_path: Path) -> PointInTimeLoader:
    return PointInTimeLoader(adapter=adapter, cache_path=tmp_path / "edgar.sqlite")


class TestPointInTimeCorrectness:
    """The critical correctness tests."""

    def test_query_before_10k_filed_returns_none(
        self, loader: PointInTimeLoader
    ) -> None:
        # Before 2015-01-30, no filings exist for AAPL in the fixture
        result = loader.get_financials("AAPL", date(2015, 1, 1))
        assert result is None

    def test_query_just_after_10k_returns_10k_data(
        self, loader: PointInTimeLoader
    ) -> None:
        # Day after 10-K was filed, before Q1 10-Q exists
        result = loader.get_financials("AAPL", date(2015, 2, 1))
        assert result is not None
        assert result.eps_basic == pytest.approx(6.49)
        assert result.source_filing.form_type == "10-K"
        assert result.source_filing.accession_number == "ACC-10K-2014"

    def test_query_on_2015_03_15_returns_10k_not_10q(
        self, loader: PointInTimeLoader
    ) -> None:
        """The exact scenario the user requirements call out:
        On 2015-03-15, the Q1 2015 10-Q (filed 2015-04-25) has NOT
        been published. The most recent available filing is the
        Q4 2014 10-K (filed 2015-01-30). We must return the 10-K data,
        NOT the (later-filed) 10-Q data.
        """
        result = loader.get_financials("AAPL", date(2015, 3, 15))
        assert result is not None, "Expected a filing to be available on 2015-03-15"
        assert result.source_filing.form_type == "10-K"
        assert result.source_filing.filing_date == date(2015, 1, 30)
        assert result.eps_basic == pytest.approx(6.49)
        # Specifically NOT the Q1 number
        assert result.eps_basic != pytest.approx(2.34)

    def test_query_after_10q_returns_10q_data(
        self, loader: PointInTimeLoader
    ) -> None:
        result = loader.get_financials("AAPL", date(2015, 5, 1))
        assert result is not None
        assert result.source_filing.form_type == "10-Q"
        assert result.eps_basic == pytest.approx(2.34)


class TestCaching:
    def test_filings_are_cached(
        self,
        adapter: FakeAdapter,
        loader: PointInTimeLoader,
    ) -> None:
        # First call hits the adapter
        loader.list_filings("AAPL")
        # Reset call tracking
        adapter.parse_calls.clear()
        # Second call should NOT trigger a new list_filings call to the adapter
        # because the filings were cached. We can confirm by checking the
        # cached path is non-empty.
        cached = loader._cached_filings("AAPL")
        assert len(cached) == 2

    def test_financials_are_cached(
        self,
        adapter: FakeAdapter,
        loader: PointInTimeLoader,
    ) -> None:
        # First call: adapter is invoked
        loader.get_financials("AAPL", date(2015, 5, 1))
        assert "ACC-10Q-2015Q1" in adapter.parse_calls
        adapter.parse_calls.clear()
        # Second call: should hit the cache, no new parse
        loader.get_financials("AAPL", date(2015, 5, 1))
        assert adapter.parse_calls == []


def _net_income_fact(
    *, value: float, period_end: date, filed: date, accession: str
) -> XbrlFact:
    return XbrlFact(
        concept="NetIncomeLoss",
        namespace="us-gaap",
        unit="USD",
        value=value,
        period_start=date(period_end.year - 1, period_end.month, 1),
        period_end=period_end,
        filed=filed,
        form="10-K",
        fiscal_year=period_end.year,
        fiscal_period="FY",
        accession_number=accession,
    )


def _parquet_loader(fc_dir: Path, sqlite: Path) -> PointInTimeLoader:
    """The live run's loader: parquet-backed, never touching the network.

    A fresh EdgarCache per call stands for a fresh process, so nothing
    is shared between two of these but what is on disk.
    """
    fetcher = FundamentalsFetcher(
        cache=EdgarCache(cache_dir=fc_dir),
        client=None,
        config=FundamentalsFetcherConfig(populate_cache_on_miss=False),
    )
    return PointInTimeLoader(
        adapter=CachedEdgarAdapter(fetcher=fetcher), cache_path=sqlite
    )


#: UVV's two 10-Ks as the audit found them (FUND-2).
_UVV_FY2025 = _net_income_fact(
    value=95_047_000.0,
    period_end=date(2025, 3, 31),
    filed=date(2025, 6, 2),
    accession="ACC-10K-2025",
)
_UVV_FY2026 = _net_income_fact(
    value=32_637_000.0,
    period_end=date(2026, 3, 31),
    filed=date(2026, 6, 1),
    accession="ACC-10K-2026",
)


class TestFilingListFollowsTheParquet:
    """Audit FUND-2: a refreshed parquet must reach the decisions.

    The weekly refresh publishes current filings and the daily run
    extracts them, but ``edgar_filings.sqlite`` rides between runs in the
    price cache and ``list_filings`` served its stored list without
    asking the adapter. A ticker listed once was listed that way forever:
    UVV was bought on 2026-11-10 on net income of $95.0M from its 10-K
    for the year to March 2025, while the parquet that run had extracted
    already carried the 10-K filed 2026-06-01 ($32.6M).
    """

    def test_filing_list_from_an_earlier_run_does_not_hide_a_newer_filing(
        self, tmp_path: Path
    ) -> None:
        fc_dir, sqlite = tmp_path / "fundamentals_cache", tmp_path / "edgar.sqlite"
        EdgarCache(cache_dir=fc_dir).save_facts(
            "UVV", [_UVV_FY2025]
        )
        # An earlier run lists UVV and stores that list in the sqlite.
        first = _parquet_loader(fc_dir, sqlite).get_financials(
            "UVV", date(2026, 5, 1)
        )
        assert first is not None and first.net_income == pytest.approx(95_047_000.0)

        # The refresh lands a new 10-K in the parquet; the sqlite survives.
        EdgarCache(cache_dir=fc_dir).save_facts(
            "UVV",
            [_UVV_FY2025, _UVV_FY2026],
        )

        result = _parquet_loader(fc_dir, sqlite).get_financials(
            "UVV", date(2026, 11, 10)
        )

        assert result is not None
        assert result.source_filing.accession_number == "ACC-10K-2026"
        assert result.source_filing.filing_date == date(2026, 6, 1)
        assert result.net_income == pytest.approx(32_637_000.0)

    def test_stored_list_holding_filings_the_parquet_lacks_is_not_served(
        self, tmp_path: Path
    ) -> None:
        """Rows another adapter wrote into the shared sqlite do not leak in.

        The real file holds synthetic ``FMP-<T>-FY-...`` rows for ten
        holdings (audit FUND-9); a parquet-backed loader must answer from
        the parquet alone.
        """
        fc_dir, sqlite = tmp_path / "fundamentals_cache", tmp_path / "edgar.sqlite"
        EdgarCache(cache_dir=fc_dir).save_facts(
            "UVV", [_UVV_FY2026]
        )
        loader = _parquet_loader(fc_dir, sqlite)
        loader._store_filings(
            [
                FilingMetadata(
                    ticker="UVV",
                    cik=None,
                    form_type="10-K",
                    filing_date=date(2026, 9, 1),
                    period_of_report=date(2026, 6, 30),
                    accession_number="FMP-UVV-FY-2026-06-30",
                )
            ]
        )

        filing = loader.latest_filing_before("UVV", date(2026, 11, 10))

        assert filing is not None
        assert filing.accession_number == "ACC-10K-2026"

    def test_parquet_is_listed_once_per_loader_not_once_per_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fc_dir, sqlite = tmp_path / "fundamentals_cache", tmp_path / "edgar.sqlite"
        EdgarCache(cache_dir=fc_dir).save_facts(
            "UVV", [_UVV_FY2026]
        )
        loader = _parquet_loader(fc_dir, sqlite)
        calls: list[str] = []
        original = CachedEdgarAdapter.list_filings

        def counting(self: CachedEdgarAdapter, ticker: str, *, form_types: tuple[str, ...]) -> list[FilingMetadata]:
            calls.append(ticker)
            return original(self, ticker, form_types=form_types)

        monkeypatch.setattr(CachedEdgarAdapter, "list_filings", counting)

        loader.get_financials("UVV", date(2026, 11, 10))
        loader.get_financials("UVV", date(2026, 11, 11))

        assert calls == ["UVV"]

    def test_ticker_with_no_parquet_lists_nothing(self, tmp_path: Path) -> None:
        """No parquet, no filings — even when the sqlite remembers some."""
        loader = _parquet_loader(tmp_path / "fundamentals_cache", tmp_path / "e.sqlite")
        loader._store_filings(
            [
                FilingMetadata(
                    ticker="GONE",
                    cik=None,
                    form_type="10-K",
                    filing_date=date(2024, 3, 1),
                    period_of_report=date(2023, 12, 31),
                    accession_number="ACC-GONE",
                )
            ]
        )

        assert loader.list_filings("GONE") == []

    def test_network_adapter_still_reads_the_stored_list(
        self, adapter: FakeAdapter, tmp_path: Path
    ) -> None:
        """Unchanged for adapters that are not a local cache.

        EdgartoolsAdapter lists over the network; its stored list is what
        keeps a backtest offline, and this fix does not touch it.
        """
        sqlite = tmp_path / "edgar.sqlite"
        PointInTimeLoader(adapter=adapter, cache_path=sqlite).list_filings("AAPL")
        adapter._filings["AAPL"] = []

        again = PointInTimeLoader(adapter=adapter, cache_path=sqlite).list_filings(
            "AAPL"
        )

        assert {f.accession_number for f in again} == {
            "ACC-10K-2014",
            "ACC-10Q-2015Q1",
        }


#: MSFT as the audit found it (FUND-9): the shared sqlite held only a
#: synthetic ``FMP-MSFT-FY-2025-06-30`` row, while the parquet carried
#: the real 10-K and two later 10-Qs. Accessions and filing dates are
#: MSFT's own, read from the parquet; the values are its net income.
_MSFT_10K_FY2025 = XbrlFact(
    concept="NetIncomeLoss",
    namespace="us-gaap",
    unit="USD",
    value=101_832_000_000.0,
    period_start=date(2024, 7, 1),
    period_end=date(2025, 6, 30),
    filed=date(2025, 7, 30),
    form="10-K",
    fiscal_year=2025,
    fiscal_period="FY",
    accession_number="0000950170-25-100235",
)
_MSFT_10Q_Q2_FY2026 = XbrlFact(
    concept="NetIncomeLoss",
    namespace="us-gaap",
    unit="USD",
    value=38_458_000_000.0,
    period_start=date(2025, 10, 1),
    period_end=date(2025, 12, 31),
    filed=date(2026, 1, 28),
    form="10-Q",
    fiscal_year=2026,
    fiscal_period="Q2",
    accession_number="0001193125-26-027207",
)
_MSFT_FMP_ROW = FilingMetadata(
    ticker="MSFT",
    cik="0000789019",
    form_type="10-K",
    filing_date=date(2025, 7, 30),
    period_of_report=date(2025, 6, 30),
    accession_number="FMP-MSFT-FY-2025-06-30",
)
#: A net income no real MSFT filing reports, so a payload served from
#: the synthetic row cannot pass for one parsed from the parquet.
_FMP_SENTINEL_NET_INCOME = 1.0


class TestStaleSqliteFilingList:
    """Audit FUND-9: a stale sqlite filing list must not hide the parquet.

    ``list_filings`` used to return the sqlite's list whenever it held
    one. For ten holdings that list was five synthetic ``FMP-<T>-FY``
    10-K rows and no 10-Q, so the live run read MSFT's fiscal 2025 10-K
    when the parquet it had extracted already held the 10-Q filed
    2026-01-28, two quarters newer.
    """

    def _seed_stale_sqlite(self, fc_dir: Path, sqlite: Path) -> None:
        """Write what an earlier FMP-backed run left in the shared file."""
        seeding = _parquet_loader(fc_dir, sqlite)
        seeding._store_filings([_MSFT_FMP_ROW])
        seeding._store_financials(
            _MSFT_FMP_ROW.accession_number,
            {"net_income": _FMP_SENTINEL_NET_INCOME, "revenue": 1.0},
        )

    def test_stale_sqlite_filing_list_is_refreshed_from_parquet(
        self, tmp_path: Path
    ) -> None:
        fc_dir, sqlite = tmp_path / "fundamentals_cache", tmp_path / "edgar.sqlite"
        EdgarCache(cache_dir=fc_dir).save_facts(
            "MSFT", [_MSFT_10K_FY2025, _MSFT_10Q_Q2_FY2026]
        )
        self._seed_stale_sqlite(fc_dir, sqlite)

        result = _parquet_loader(fc_dir, sqlite).get_financials(
            "MSFT", date(2026, 2, 2)
        )

        assert result is not None
        assert result.source_filing.form_type == "10-Q"
        assert result.source_filing.filing_date == date(2026, 1, 28)
        assert result.source_filing.accession_number == "0001193125-26-027207"
        assert result.net_income != _FMP_SENTINEL_NET_INCOME

    def test_synthetic_row_is_never_listed_beside_the_parquet(
        self, tmp_path: Path
    ) -> None:
        """Edge: the synthetic row shares its date with the real 10-K.

        Between 2025-07-30 and the first 10-Q the only eligible filings
        are the FMP row and the real 10-K, filed the same day; ``max``
        would pick whichever came first, so the row must not be listed.
        """
        fc_dir, sqlite = tmp_path / "fundamentals_cache", tmp_path / "edgar.sqlite"
        EdgarCache(cache_dir=fc_dir).save_facts(
            "MSFT", [_MSFT_10K_FY2025, _MSFT_10Q_Q2_FY2026]
        )
        self._seed_stale_sqlite(fc_dir, sqlite)
        loader = _parquet_loader(fc_dir, sqlite)

        listed = {f.accession_number for f in loader.list_filings("MSFT")}
        before_q = loader.latest_filing_before("MSFT", date(2025, 8, 15))

        assert listed == {"0000950170-25-100235", "0001193125-26-027207"}
        assert before_q is not None
        assert before_q.accession_number == "0000950170-25-100235"

    def test_stale_sqlite_does_not_shadow_a_10q_on_a_second_call(
        self, tmp_path: Path
    ) -> None:
        """Failure mode: the parquet answer was stored; a later call in
        the same loader must not fall back to the sqlite's FMP row."""
        fc_dir, sqlite = tmp_path / "fundamentals_cache", tmp_path / "edgar.sqlite"
        EdgarCache(cache_dir=fc_dir).save_facts(
            "MSFT", [_MSFT_10K_FY2025, _MSFT_10Q_Q2_FY2026]
        )
        self._seed_stale_sqlite(fc_dir, sqlite)
        loader = _parquet_loader(fc_dir, sqlite)

        loader.list_filings("MSFT")
        second = loader.latest_filing_before("MSFT", date(2026, 2, 2))

        assert second is not None
        assert second.accession_number == "0001193125-26-027207"


class TestLatestFilingBefore:
    def test_returns_none_when_no_eligible_filings(
        self, loader: PointInTimeLoader
    ) -> None:
        result = loader.latest_filing_before("AAPL", date(2014, 1, 1))
        assert result is None

    def test_returns_most_recent_eligible(self, loader: PointInTimeLoader) -> None:
        result = loader.latest_filing_before("AAPL", date(2015, 6, 1))
        assert result is not None
        assert result.form_type == "10-Q"  # Q1 2015 is the most recent
        assert result.filing_date == date(2015, 4, 25)


_UNPARSEABLE = FilingMetadata(
    ticker="AAPL",
    cik="320193",
    form_type="10-K",
    filing_date=date(2015, 10, 28),
    period_of_report=date(2015, 9, 26),
    accession_number="ACC-UNPARSEABLE",
)


class TestEmptyPayloadIsNotData:
    """A filing that parses to nothing must never look like real data.

    ``get_financials`` used to build a :class:`PointInTimeFinancials`
    out of an empty payload, so every field came back ``None`` while the
    object itself looked valid. Callers had no way to tell that apart
    from a company that genuinely reports sparse XBRL.
    """

    def test_unparseable_filing_raises_instead_of_all_none_object(
        self, adapter: FakeAdapter, loader: PointInTimeLoader
    ) -> None:
        # FakeAdapter returns {} for any accession it has no financials for.
        adapter._filings["AAPL"].append(_UNPARSEABLE)
        with pytest.raises(PointInTimeError, match="no usable financial data"):
            loader.get_financials("AAPL", date(2015, 11, 1))

    def test_missing_filing_still_returns_none(
        self, loader: PointInTimeLoader
    ) -> None:
        """The other half of the distinction: no filing is not an error."""
        assert loader.get_financials("AAPL", date(2015, 1, 1)) is None

    def test_partially_populated_payload_is_returned(
        self, adapter: FakeAdapter, loader: PointInTimeLoader
    ) -> None:
        """Sparse reporting is legitimate — only an empty payload is a failure."""
        sparse = FilingMetadata(
            ticker="AAPL",
            cik="320193",
            form_type="10-K",
            filing_date=date(2015, 10, 28),
            period_of_report=date(2015, 9, 26),
            accession_number="ACC-SPARSE",
        )
        adapter._filings["AAPL"].append(sparse)
        adapter._financials["ACC-SPARSE"] = {"eps_basic": 9.22}

        result = loader.get_financials("AAPL", date(2015, 11, 1))

        assert result is not None
        assert result.eps_basic == pytest.approx(9.22)
        assert result.revenue is None  # sparse, but the filing still parsed

    def test_empty_payload_is_not_written_to_the_cache(
        self, adapter: FakeAdapter, loader: PointInTimeLoader
    ) -> None:
        """Otherwise the failure outlives the bug that caused it.

        An accession is immutable, so a cached empty payload would keep
        being served even after the adapter is repaired.
        """
        adapter._filings["AAPL"].append(_UNPARSEABLE)
        with pytest.raises(PointInTimeError):
            loader.get_financials("AAPL", date(2015, 11, 1))

        assert loader._cached_financials("ACC-UNPARSEABLE") is None

    def test_empty_cached_payload_is_re_parsed_not_replayed(
        self, adapter: FakeAdapter, loader: PointInTimeLoader
    ) -> None:
        """A cache poisoned by an earlier broken adapter must heal itself.

        Real caches already hold rows written when parsing silently
        returned ``{}``. Serving them back forever would keep the
        failure alive long after the adapter that caused it was fixed.
        """
        poisoned = FilingMetadata(
            ticker="AAPL",
            cik="320193",
            form_type="10-K",
            filing_date=date(2015, 10, 28),
            period_of_report=date(2015, 9, 26),
            accession_number="ACC-POISONED",
        )
        adapter._filings["AAPL"].append(poisoned)
        loader._store_financials("ACC-POISONED", {})
        # The adapter can parse it now — the stale empty row must not win.
        adapter._financials["ACC-POISONED"] = {"eps_basic": 8.31}

        result = loader.get_financials("AAPL", date(2015, 11, 1))

        assert result is not None
        assert result.eps_basic == pytest.approx(8.31)
        assert "ACC-POISONED" in adapter.parse_calls


class TestDefaultAdapterPath:
    """Regression: ``PointInTimeLoader()`` — the obvious construction.

    ``EdgartoolsAdapter.parse_financials`` called edgartools' ``Filing()``
    with kwargs that never matched the installed signature, swallowed the
    resulting ``TypeError``, and returned ``{}``. Every field of the
    resulting :class:`PointInTimeFinancials` was ``None``, and callers
    got that back as though it were data.
    """

    def test_default_adapter_parse_financials_fails_loudly(self) -> None:
        with pytest.raises(PointInTimeError, match="CachedEdgarAdapter"):
            EdgartoolsAdapter().parse_financials(_UNPARSEABLE)

    def test_default_constructed_loader_never_yields_all_none_financials(
        self, tmp_path: Path
    ) -> None:
        # Default adapter — only cache_path is overridden, to keep the
        # test off the real project cache. Seeding the filing directly
        # keeps list_filings from reaching the network.
        loader = PointInTimeLoader(cache_path=tmp_path / "edgar.sqlite")
        loader._store_filings([_UNPARSEABLE])

        with pytest.raises(PointInTimeError):
            loader.get_financials("AAPL", date(2015, 11, 1))


class TestFilingMetadataDataclass:
    def test_round_trip_dict(self) -> None:
        f = FilingMetadata(
            ticker="AAPL",
            cik="320193",
            form_type="10-K",
            filing_date=date(2015, 1, 30),
            period_of_report=date(2014, 12, 27),
            accession_number="ACC-1",
        )
        d = f.to_dict()
        f2 = FilingMetadata.from_dict(d)
        assert f2 == f


class TestPayloadVersioning:
    """An accession is immutable, so nothing invalidates a cached payload
    on its own. When parse_financials starts producing a new field, old
    rows keep being served without it — which is how sic_code stayed
    None even after it was populated upstream."""

    def test_stored_payload_carries_the_version(self, tmp_path: Path) -> None:
        loader = PointInTimeLoader(cache_path=tmp_path / "pit.sqlite")
        loader._store_financials("acc-1", {"revenue": 100.0})

        round_tripped = loader._cached_financials("acc-1")

        assert round_tripped is not None
        assert round_tripped["revenue"] == 100.0
        assert round_tripped[_VERSION_KEY] == _PAYLOAD_VERSION

    def test_unversioned_payload_is_a_miss(self, tmp_path: Path) -> None:
        import json
        import sqlite3

        path = tmp_path / "pit.sqlite"
        loader = PointInTimeLoader(cache_path=path)
        loader._store_financials("acc-1", {"revenue": 100.0})
        # Simulate a row written before versioning existed.
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE financials SET payload_json = ? WHERE accession_number = ?",
                (json.dumps({"revenue": 100.0, "sic_code": None}), "acc-1"),
            )

        assert loader._cached_financials("acc-1") is None

    def test_stale_version_is_a_miss(self, tmp_path: Path) -> None:
        import json
        import sqlite3

        path = tmp_path / "pit.sqlite"
        loader = PointInTimeLoader(cache_path=path)
        loader._store_financials("acc-1", {"revenue": 100.0})
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE financials SET payload_json = ? WHERE accession_number = ?",
                (
                    json.dumps({"revenue": 100.0, _VERSION_KEY: _PAYLOAD_VERSION - 1}),
                    "acc-1",
                ),
            )

        assert loader._cached_financials("acc-1") is None

    def test_payload_written_before_the_debt_rollups_is_a_miss(
        self, tmp_path: Path
    ) -> None:
        """Audit FUND-4: version-3 rows were parsed before total_debt
        read the lease-inclusive and per-instrument elements, so the live
        sqlite serves total_debt=None for FISV, HRI and RPD. They must be
        re-parsed, not served."""
        import json
        import sqlite3

        path = tmp_path / "pit.sqlite"
        loader = PointInTimeLoader(cache_path=path)
        loader._store_financials("acc-1", {"revenue": 100.0})
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE financials SET payload_json = ? WHERE accession_number = ?",
                (
                    json.dumps({"revenue": 100.0, "total_debt": None, _VERSION_KEY: 3}),
                    "acc-1",
                ),
            )

        assert loader._cached_financials("acc-1") is None


def _calm_fact(
    concept: str,
    value: float,
    period_end: date,
    *,
    filing: str,
    period_start: date | None = None,
    namespace: str = "us-gaap",
    unit: str = "USD",
) -> XbrlFact:
    """One CALM fact as the parquet holds it, attached to ``filing``."""
    form, filed, accession, fy, fp = _CALM_FILINGS[filing]
    return XbrlFact(
        concept=concept,
        namespace=namespace,
        unit=unit,
        value=value,
        period_start=period_start,
        period_end=period_end,
        filed=filed,
        form=form,
        fiscal_year=fy,
        fiscal_period=fp,
        accession_number=accession,
    )


#: Cal-Maine's FY2025 10-K and its fiscal Q3 2026 10-Q as the audit found
#: them (FUND-6). Accessions, dates and values are CALM's own, read from
#: the fundamentals parquet.
_CALM_FILINGS: dict[str, tuple[str, date, str, int, str]] = {
    "10-K": ("10-K", date(2025, 7, 22), "0001562762-25-000170", 2025, "FY"),
    "10-Q": ("10-Q", date(2026, 4, 1), "0001562762-26-000046", 2026, "Q3"),
}
_CALM_FY2024_END = date(2024, 6, 1)
_CALM_FY2025_END = date(2025, 5, 31)
_CALM_Q3_END = date(2026, 2, 28)


def _calm_facts() -> list[XbrlFact]:
    k, q = "10-K", "10-Q"
    return [
        # The 10-K: FY2025 balance sheet beside its FY2024 comparative,
        # the annual flows, and two cover facts dated neither period end.
        _calm_fact("Assets", 2_184_761_000.0, _CALM_FY2024_END, filing=k),
        _calm_fact("Liabilities", 387_718_000.0, _CALM_FY2024_END, filing=k),
        _calm_fact("StockholdersEquity", 1_800_147_000.0, _CALM_FY2024_END, filing=k),
        _calm_fact("Assets", 3_084_619_000.0, _CALM_FY2025_END, filing=k),
        _calm_fact("Liabilities", 518_604_000.0, _CALM_FY2025_END, filing=k),
        _calm_fact("StockholdersEquity", 2_560_624_000.0, _CALM_FY2025_END, filing=k),
        _calm_fact(
            "CashAndCashEquivalentsAtCarryingValue", 499_392_000.0, _CALM_FY2025_END, filing=k
        ),
        _calm_fact(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            4_261_885_000.0,
            _CALM_FY2025_END,
            period_start=date(2024, 6, 2),
            filing=k,
        ),
        _calm_fact(
            "NetIncomeLoss",
            1_220_048_000.0,
            _CALM_FY2025_END,
            period_start=date(2024, 6, 2),
            filing=k,
        ),
        _calm_fact(
            "EntityPublicFloat", 4_128_739_000.0, date(2024, 11, 29),
            namespace="dei", filing=k,
        ),
        _calm_fact(
            "EntityCommonStockSharesOutstanding", 48_497_480.0, date(2025, 7, 22),
            namespace="dei", unit="shares", filing=k,
        ),
        # The 10-Q: the 2026-02-28 balance sheet beside the year-end
        # comparative it reprints, year-to-date flows, the cover count.
        _calm_fact("Assets", 3_084_619_000.0, _CALM_FY2025_END, filing=q),
        _calm_fact("Liabilities", 518_604_000.0, _CALM_FY2025_END, filing=q),
        _calm_fact("StockholdersEquity", 2_560_624_000.0, _CALM_FY2025_END, filing=q),
        _calm_fact("Assets", 3_139_261_000.0, _CALM_Q3_END, filing=q),
        _calm_fact("Liabilities", 432_129_000.0, _CALM_Q3_END, filing=q),
        _calm_fact("StockholdersEquity", 2_700_168_000.0, _CALM_Q3_END, filing=q),
        _calm_fact(
            "CashAndCashEquivalentsAtCarryingValue", 392_159_000.0, _CALM_Q3_END, filing=q
        ),
        _calm_fact(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            2_359_051_000.0,
            _CALM_Q3_END,
            period_start=date(2025, 6, 1),
            filing=q,
        ),
        _calm_fact(
            "NetIncomeLoss",
            352_558_000.0,
            _CALM_Q3_END,
            period_start=date(2025, 6, 1),
            filing=q,
        ),
        _calm_fact(
            "EntityCommonStockSharesOutstanding", 47_376_588.0, date(2026, 4, 1),
            namespace="dei", unit="shares", filing=q,
        ),
    ]


class TestBalanceSheetFollowsTheNewestFiling:
    """Audit FUND-6: a newer 10-Q's balance sheet beats an older 10-K's.

    Instant concepts were looked up with ``prefer_annual=True``, so CALM
    on 2026-04-15 was read with its 2025-05-31 balance sheet, three
    quarters old, while the 10-Q filed 2026-04-01 carried 2026-02-28's.
    The payload was stored under the 10-Q's accession all the same, so
    ``source_filing`` named a filing whose numbers it did not hold, and
    its ``period_of_report`` was the cover-page date 2026-04-01 rather
    than any period the filing reports.
    """

    AS_OF = date(2026, 4, 15)

    def _loader(self, tmp_path: Path) -> PointInTimeLoader:
        fc_dir = tmp_path / "fundamentals_cache"
        EdgarCache(cache_dir=fc_dir).save_facts("CALM", _calm_facts())
        return _parquet_loader(fc_dir, tmp_path / "edgar.sqlite")

    def test_calm_balance_sheet_is_10q_2026_02_28_and_source_filing_matches(
        self, tmp_path: Path
    ) -> None:
        result = self._loader(tmp_path).get_financials("CALM", self.AS_OF)

        assert result is not None
        assert result.source_filing.form_type == "10-Q"
        assert result.source_filing.accession_number == "0001562762-26-000046"
        assert result.source_filing.filing_date == date(2026, 4, 1)
        assert result.source_filing.period_of_report == _CALM_Q3_END
        assert result.total_assets == pytest.approx(3_139_261_000.0)
        assert result.total_liabilities == pytest.approx(432_129_000.0)
        assert result.total_equity == pytest.approx(2_700_168_000.0)
        assert result.cash_and_equivalents == pytest.approx(392_159_000.0)
        assert result.shares_outstanding == pytest.approx(47_376_588.0)
        # Flows stay annual: a nine-month year-to-date is not a year.
        assert result.revenue == pytest.approx(4_261_885_000.0)

    def test_before_the_10q_the_10k_balance_sheet_and_period_are_used(
        self, tmp_path: Path
    ) -> None:
        result = self._loader(tmp_path).get_financials("CALM", date(2025, 8, 1))

        assert result is not None
        assert result.source_filing.accession_number == "0001562762-25-000170"
        assert result.source_filing.period_of_report == _CALM_FY2025_END
        assert result.total_assets == pytest.approx(3_084_619_000.0)

    def test_payload_cached_with_the_10k_balance_sheet_is_reparsed(
        self, tmp_path: Path
    ) -> None:
        """A version-4 row under the 10-Q's accession holds 10-K values.

        Every such row in the live sqlite was written by the old lookup,
        so it must be a miss rather than replayed.
        """
        import json
        import sqlite3

        loader = self._loader(tmp_path)
        loader._store_financials("0001562762-26-000046", {"total_assets": 0.0})
        with sqlite3.connect(tmp_path / "edgar.sqlite") as conn:
            conn.execute(
                "UPDATE financials SET payload_json = ? WHERE accession_number = ?",
                (
                    json.dumps({"total_assets": 3_084_619_000.0, _VERSION_KEY: 4}),
                    "0001562762-26-000046",
                ),
            )

        result = loader.get_financials("CALM", self.AS_OF)

        assert result is not None
        assert result.total_assets == pytest.approx(3_139_261_000.0)
