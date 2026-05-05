"""Unit tests for the cross-source cache validator."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.backtest.point_in_time import EdgarAdapter, FilingMetadata
from core.data.cache_validator import (
    CacheValidator,
    ValidationReport,
    _compare,
)
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact
from core.data.fundamentals_fetcher import FundamentalsFetcher


class _FakeFmpAdapter(EdgarAdapter):
    """Test adapter returning canned numbers via the EdgarAdapter Protocol."""

    def __init__(self, payload: dict[str, dict[str, float | None]]) -> None:
        # payload: {ticker: {field: value}}
        self._payload = payload

    def list_filings(
        self, ticker: str, *, form_types: tuple[str, ...]
    ) -> list[FilingMetadata]:
        if ticker.upper() not in self._payload:
            return []
        return [
            FilingMetadata(
                ticker=ticker.upper(),
                cik=None,
                form_type="10-K",
                filing_date=date(2021, 2, 15),
                period_of_report=date(2020, 12, 31),
                accession_number=f"fmp-{ticker.upper()}",
            )
        ]

    def parse_financials(
        self, filing: FilingMetadata
    ) -> dict[str, float | None]:
        return dict(self._payload.get(filing.ticker, {}))


def _seed_cache(cache: EdgarCache, ticker: str, fields: dict[str, float]) -> None:
    """Insert one fact per concept from the requested fields."""
    from core.data.fundamentals_fetcher import CONCEPT_MAP

    facts = []
    for field, value in fields.items():
        ns, concept = CONCEPT_MAP[field][0]
        facts.append(
            XbrlFact(
                concept=concept,
                namespace=ns,
                unit="USD" if ns == "us-gaap" else "shares",
                value=value,
                period_start=date(2020, 1, 1),
                period_end=date(2020, 12, 31),
                filed=date(2021, 2, 15),
                form="10-K",
                fiscal_year=2020,
                fiscal_period="FY",
                accession_number=f"acc-{ticker}",
            )
        )
    cache.save_facts(ticker, facts)


class TestCompare:
    def test_within_tolerance_passes(self) -> None:
        c = _compare("AAPL", "revenue", 100.0, 102.0, tolerance=0.05)
        assert c.within_tolerance is True
        # 2 / 102 ≈ 0.0196 — within tolerance
        assert c.relative_diff is not None
        assert c.relative_diff < 0.05

    def test_outside_tolerance_fails(self) -> None:
        c = _compare("AAPL", "revenue", 100.0, 200.0, tolerance=0.05)
        assert c.within_tolerance is False

    def test_missing_edgar_value(self) -> None:
        c = _compare("AAPL", "revenue", None, 100.0, tolerance=0.05)
        assert c.within_tolerance is False
        assert "EDGAR missing" in c.notes

    def test_missing_other_value(self) -> None:
        c = _compare("AAPL", "revenue", 100.0, None, tolerance=0.05)
        assert c.within_tolerance is False
        assert "comparison missing" in c.notes

    def test_both_zero_perfect_match(self) -> None:
        c = _compare("AAPL", "revenue", 0.0, 0.0, tolerance=0.05)
        assert c.within_tolerance is True
        assert c.relative_diff == 0.0


class TestValidationReport:
    def test_overall_pass_rate(self) -> None:
        from core.data.cache_validator import (
            FieldComparison,
            TickerValidation,
        )

        report = ValidationReport(
            sample_size=2,
            tolerance=0.05,
            fields_validated=("revenue", "net_income"),
        )
        report.per_ticker.append(
            TickerValidation(
                ticker="A",
                as_of=date(2021, 1, 1),
                comparisons=[
                    FieldComparison("A", "revenue", 100, 100, 0.0, True),
                    FieldComparison("A", "net_income", 50, 60, 0.166, False),
                ],
            )
        )
        report.per_ticker.append(
            TickerValidation(
                ticker="B",
                as_of=date(2021, 1, 1),
                comparisons=[
                    FieldComparison("B", "revenue", 200, 200, 0.0, True),
                    FieldComparison("B", "net_income", 100, 100, 0.0, True),
                ],
            )
        )
        # 3 of 4 within tolerance
        assert report.overall_pass_rate() == pytest.approx(0.75)


class TestCacheValidator:
    def test_validate_ticker_finds_close_match(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        _seed_cache(cache, "AAPL", {"revenue": 1000.0, "net_income": 200.0})
        edgar = FundamentalsFetcher(cache=cache, client=None)
        fmp = _FakeFmpAdapter(
            {"AAPL": {"revenue": 1010.0, "net_income": 198.0}}  # within 5%
        )
        v = CacheValidator(edgar, fmp, tolerance=0.05, fields=("revenue", "net_income"))
        result = v.validate_ticker("AAPL", date(2021, 6, 1))
        assert result.fields_compared == 2
        assert result.fields_within_tolerance == 2

    def test_validate_ticker_flags_large_discrepancy(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        _seed_cache(cache, "AAPL", {"revenue": 1000.0})
        edgar = FundamentalsFetcher(cache=cache, client=None)
        fmp = _FakeFmpAdapter({"AAPL": {"revenue": 5000.0}})  # 5x off
        v = CacheValidator(edgar, fmp, tolerance=0.05, fields=("revenue",))
        result = v.validate_ticker("AAPL", date(2021, 6, 1))
        assert result.fields_compared == 1
        assert result.fields_within_tolerance == 0

    def test_negative_tolerance_raises(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        edgar = FundamentalsFetcher(cache=cache, client=None)
        fmp = _FakeFmpAdapter({})
        with pytest.raises(ValueError):
            CacheValidator(edgar, fmp, tolerance=-0.01)

    def test_validate_sample_with_seed_is_deterministic(
        self, tmp_path: Path
    ) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        for t in ["A", "B", "C", "D", "E"]:
            _seed_cache(cache, t, {"revenue": 100.0})
        edgar = FundamentalsFetcher(cache=cache, client=None)
        fmp = _FakeFmpAdapter({t: {"revenue": 100.0} for t in ["A", "B", "C", "D", "E"]})
        v = CacheValidator(edgar, fmp, tolerance=0.05, fields=("revenue",))
        r1 = v.validate_sample(["A", "B", "C", "D", "E"], 3, date(2021, 6, 1), seed=42)
        r2 = v.validate_sample(["A", "B", "C", "D", "E"], 3, date(2021, 6, 1), seed=42)
        assert {t.ticker for t in r1.per_ticker} == {t.ticker for t in r2.per_ticker}

    def test_systematic_discrepancies_finds_bad_field(self, tmp_path: Path) -> None:
        cache = EdgarCache(cache_dir=tmp_path)
        for t in ["A", "B"]:
            _seed_cache(cache, t, {"revenue": 100.0, "net_income": 50.0})
        edgar = FundamentalsFetcher(cache=cache, client=None)
        # FMP agrees on revenue but disagrees wildly on net_income
        fmp = _FakeFmpAdapter({
            "A": {"revenue": 100.0, "net_income": 1000.0},  # 20× off
            "B": {"revenue": 100.0, "net_income": 1000.0},  # 20× off
        })
        v = CacheValidator(edgar, fmp, tolerance=0.05, fields=("revenue", "net_income"))
        report = v.validate_sample(["A", "B"], 2, date(2021, 6, 1), seed=1)
        bad = report.systematic_discrepancies(threshold=0.5)
        assert any("net_income" in s for s in bad)
