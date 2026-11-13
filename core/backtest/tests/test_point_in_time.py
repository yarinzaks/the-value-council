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
