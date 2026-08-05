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
    FilingMetadata,
    PointInTimeLoader,
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
