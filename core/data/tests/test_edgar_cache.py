"""Unit tests for the Parquet-backed EDGAR cache."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.data.edgar_cache import CacheStats, EdgarCache
from core.data.edgar_facts import XbrlFact


def _fact(
    *,
    concept: str = "Revenues",
    namespace: str = "us-gaap",
    unit: str = "USD",
    value: float = 1_000_000.0,
    period_start: date | None = date(2020, 1, 1),
    period_end: date = date(2020, 12, 31),
    filed: date = date(2021, 2, 15),
    form: str = "10-K",
    fiscal_year: int | None = 2020,
    fiscal_period: str | None = "FY",
    accession_number: str = "acc-1",
) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace=namespace,
        unit=unit,
        value=value,
        period_start=period_start,
        period_end=period_end,
        filed=filed,
        form=form,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        accession_number=accession_number,
    )


class TestSaveAndLoad:
    def test_round_trip(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        facts = [_fact(value=1.0), _fact(value=2.0, period_end=date(2021, 12, 31), filed=date(2022, 2, 15), accession_number="acc-2")]
        cache.save_facts("AAPL", facts)
        loaded = cache.load_facts("AAPL")
        assert len(loaded) == 2
        values = sorted(f.value for f in loaded)
        assert values == [1.0, 2.0]

    def test_save_empty_is_noop(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("AAPL", [])
        assert not cache.has_ticker("AAPL")

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        assert cache.load_facts("AAPL") == []

    def test_has_ticker_reflects_disk_state(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        assert not cache.has_ticker("AAPL")
        cache.save_facts("AAPL", [_fact()])
        assert cache.has_ticker("AAPL")

    def test_tickers_lists_cached(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("AAPL", [_fact()])
        cache.save_facts("MSFT", [_fact()])
        assert sorted(cache.tickers()) == ["AAPL", "MSFT"]


class TestLatestValueAt:
    def test_returns_most_recent_filed(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        facts = [
            _fact(  # FY 2019, filed Feb 2020
                value=1.0,
                period_end=date(2019, 12, 31),
                filed=date(2020, 2, 15),
                accession_number="acc-2019",
            ),
            _fact(  # FY 2020, filed Feb 2021
                value=2.0,
                period_end=date(2020, 12, 31),
                filed=date(2021, 2, 15),
                accession_number="acc-2020",
            ),
            _fact(  # Q1 2021, filed Apr 2021
                value=3.0,
                period_end=date(2021, 3, 31),
                filed=date(2021, 4, 25),
                form="10-Q",
                fiscal_year=2021,
                fiscal_period="Q1",
                accession_number="acc-q1-21",
            ),
        ]
        cache.save_facts("AAPL", facts)

        # On 2021-03-15, only the 2020 10-K is visible
        result = cache.latest_value_at("AAPL", "Revenues", date(2021, 3, 15))
        assert result is not None
        assert result.value == 2.0
        assert result.form == "10-K"

        # On 2021-05-01, the Q1 10-Q is visible — that's the most recent
        result = cache.latest_value_at("AAPL", "Revenues", date(2021, 5, 1))
        assert result is not None
        assert result.value == 3.0

    def test_filter_by_form(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        facts = [
            _fact(value=1.0, form="10-K"),
            _fact(
                value=2.0,
                form="10-Q",
                period_end=date(2021, 3, 31),
                filed=date(2021, 4, 25),
                accession_number="acc-q1",
            ),
        ]
        cache.save_facts("AAPL", facts)
        # Restrict to 10-K only — should pick up the older annual
        result = cache.latest_value_at(
            "AAPL", "Revenues", date(2021, 12, 31), forms=("10-K",)
        )
        assert result is not None
        assert result.form == "10-K"
        assert result.value == 1.0

    def test_prefer_annual(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        facts = [
            _fact(  # 10-K for 2020
                value=10.0,
                period_end=date(2020, 12, 31),
                filed=date(2021, 2, 15),
                accession_number="ann",
            ),
            _fact(  # later 10-Q
                value=3.0,
                form="10-Q",
                period_end=date(2021, 3, 31),
                filed=date(2021, 4, 25),
                fiscal_year=2021,
                fiscal_period="Q1",
                accession_number="q1",
            ),
        ]
        cache.save_facts("AAPL", facts)
        # With prefer_annual, we get the 10-K even though Q1 is later
        result = cache.latest_value_at(
            "AAPL", "Revenues", date(2021, 6, 1), prefer_annual=True
        )
        assert result is not None
        assert result.form == "10-K"
        assert result.value == 10.0

    def test_no_match_returns_none(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("AAPL", [_fact(filed=date(2025, 1, 1))])
        # Asking for a date BEFORE any filing
        assert cache.latest_value_at("AAPL", "Revenues", date(2020, 1, 1)) is None

    def test_namespace_filter(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts(
            "AAPL",
            [
                _fact(namespace="us-gaap", concept="Revenues", value=1.0),
                _fact(
                    namespace="dei",
                    concept="EntityCommonStockSharesOutstanding",
                    unit="shares",
                    value=16_000_000_000,
                    accession_number="acc-shares",
                ),
            ],
        )
        # us-gaap default
        result = cache.latest_value_at("AAPL", "Revenues", date(2025, 1, 1))
        assert result is not None and result.namespace == "us-gaap"
        # dei explicit
        result = cache.latest_value_at(
            "AAPL", "EntityCommonStockSharesOutstanding", date(2025, 1, 1), namespace="dei"
        )
        assert result is not None and result.namespace == "dei"
        assert result.value == 16_000_000_000


class TestLoadDataframe:
    def test_returns_typed_columns(self, tmp_path: Path) -> None:
        import pandas as pd

        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("AAPL", [_fact(), _fact(value=2.0, accession_number="acc-2")])
        df = cache.load_dataframe("AAPL")
        assert "filed" in df.columns
        assert pd.api.types.is_datetime64_any_dtype(df["filed"])
        assert len(df) == 2


class TestStats:
    def test_empty_cache(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        s = cache.stats()
        assert s.ticker_count == 0
        assert s.total_facts == 0

    def test_populated_cache(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        cache.save_facts("AAPL", [_fact(), _fact(accession_number="acc-2")])
        cache.save_facts("MSFT", [_fact()])
        s = cache.stats()
        assert s.ticker_count == 2
        assert s.total_facts == 3
        assert s.total_size_bytes > 0
