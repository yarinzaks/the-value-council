"""Unit tests for Graham Net-Net filters."""

from __future__ import annotations

from datetime import date

import pytest

from agents.graham.filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MIN_MARKET_CAP_USD,
    DEFAULT_NCAV_DISCOUNT_FACTOR,
    debt_to_equity,
    filter_candidates,
    ncav_per_share,
    passes_filters,
    price_to_ncav,
)
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


def _fin(
    *,
    ticker: str = "TEST",
    current_assets: float | None = 500_000_000.0,
    total_liabilities: float | None = 200_000_000.0,
    total_equity: float | None = 800_000_000.0,
    shares_outstanding: float | None = 100_000_000.0,
    total_debt: float | None = 100_000_000.0,
    long_term_debt: float | None = 100_000_000.0,
    net_income: float | None = 50_000_000.0,
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
        current_assets=current_assets,
        total_liabilities=total_liabilities,
        total_equity=total_equity,
        shares_outstanding=shares_outstanding,
        total_debt=total_debt,
        long_term_debt=long_term_debt,
        net_income=net_income,
    )


class TestNcavPerShare:
    def test_basic(self) -> None:
        # NCAV = 500M - 200M = 300M; per share = 300M / 100M = 3.0
        assert ncav_per_share(_fin()) == pytest.approx(3.0)

    def test_negative_ncav_returns_value(self) -> None:
        # NCAV negative is a valid value (will be filtered later)
        v = ncav_per_share(_fin(current_assets=100_000_000, total_liabilities=200_000_000))
        assert v == pytest.approx(-1.0)

    def test_missing_components_return_none(self) -> None:
        assert ncav_per_share(_fin(current_assets=None)) is None
        assert ncav_per_share(_fin(total_liabilities=None)) is None
        assert ncav_per_share(_fin(shares_outstanding=None)) is None
        assert ncav_per_share(None) is None

    def test_zero_shares_returns_none(self) -> None:
        assert ncav_per_share(_fin(shares_outstanding=0)) is None


class TestPriceToNcav:
    def test_buy_zone(self) -> None:
        # NCAV/share = 3.0; price 1.5 → P/NCAV = 0.5 (well below ⅔)
        assert price_to_ncav(1.5, _fin()) == pytest.approx(0.5)

    def test_at_threshold(self) -> None:
        # Price 2.0 / NCAV 3.0 = 0.667 = exactly Graham's threshold
        v = price_to_ncav(2.0, _fin())
        assert v == pytest.approx(2.0 / 3.0)

    def test_above_book_value(self) -> None:
        # Price 5.0 / NCAV 3.0 = 1.67 (way above ⅔)
        assert price_to_ncav(5.0, _fin()) == pytest.approx(5 / 3)

    def test_negative_ncav_returns_none(self) -> None:
        assert price_to_ncav(2.0, _fin(current_assets=100, total_liabilities=200)) is None

    def test_missing_price(self) -> None:
        assert price_to_ncav(None, _fin()) is None

    def test_zero_price(self) -> None:
        assert price_to_ncav(0.0, _fin()) is None


class TestDebtToEquity:
    def test_basic(self) -> None:
        # 100M / 800M = 0.125
        assert debt_to_equity(_fin()) == pytest.approx(0.125)

    def test_falls_back(self) -> None:
        de = debt_to_equity(_fin(total_debt=None, long_term_debt=200_000_000))
        assert de == pytest.approx(0.25)

    def test_missing_treats_zero(self) -> None:
        assert debt_to_equity(_fin(total_debt=None, long_term_debt=None)) == 0.0


class TestPassesFilters:
    def _candidate(self, **overrides) -> tuple:
        defaults = dict(
            current_assets=500_000_000.0,
            total_liabilities=200_000_000.0,
            shares_outstanding=100_000_000.0,
            net_income=50_000_000.0,
            total_debt=100_000_000.0,
            total_equity=800_000_000.0,
        )
        defaults.update({k: v for k, v in overrides.items() if k in defaults})
        fin = _fin(**defaults)
        # Default price puts P/NCAV at 0.5 — well below ⅔
        price = overrides.get("price", 1.5)
        market_cap = price * defaults["shares_outstanding"]
        return fin, market_cap, price

    def test_clean_candidate_passes(self) -> None:
        fin, mcap, price = self._candidate()
        result = passes_filters(
            fin, mcap, price, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert result.passed is True

    def test_p_ncav_above_threshold_rejected(self) -> None:
        fin, mcap, price = self._candidate(price=2.5)  # P/NCAV = 0.83 > ⅔
        result = passes_filters(
            fin, mcap, price, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert result.passed is False
        assert "P/NCAV" in (result.rejection_reason or "")

    def test_negative_ncav_rejected(self) -> None:
        fin, mcap, price = self._candidate(
            current_assets=100_000_000, total_liabilities=200_000_000
        )
        result = passes_filters(
            fin, mcap, price, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert result.passed is False

    def test_negative_net_income_rejected(self) -> None:
        fin, mcap, price = self._candidate(net_income=-50_000_000)
        result = passes_filters(
            fin, mcap, price, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert result.passed is False
        assert "net income" in (result.rejection_reason or "")

    def test_high_debt_rejected(self) -> None:
        fin, mcap, price = self._candidate(total_debt=2_000_000_000)
        result = passes_filters(
            fin, mcap, price, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert result.passed is False
        assert "D/E" in (result.rejection_reason or "")

    def test_share_class_rejected(self) -> None:
        fin = _fin(ticker="ABC-A")
        result = passes_filters(fin, 1_000_000_000, 1.0, as_of=date(2024, 6, 30))
        assert result.passed is False
        assert "share class" in (result.rejection_reason or "")

    def test_below_market_cap_floor_rejected(self) -> None:
        fin, _, price = self._candidate()
        result = passes_filters(fin, 100_000_000, price, as_of=date(2024, 6, 30))
        assert result.passed is False
        assert "market cap" in (result.rejection_reason or "")


class TestFilterBatch:
    def test_only_passers_returned(self) -> None:
        ok = _fin(ticker="OK", current_assets=500_000_000, total_liabilities=200_000_000)
        bad_ncav = _fin(
            ticker="BAD",
            current_assets=100_000_000,
            total_liabilities=200_000_000,
        )
        candidates = [
            (ok, 1.5 * 100_000_000, 1.5),
            (bad_ncav, 5 * 100_000_000, 5.0),
        ]
        result = filter_candidates(
            candidates, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert len(result) == 1
        assert result[0][0].ticker == "OK"


class TestDefaults:
    def test_defaults_documented(self) -> None:
        assert DEFAULT_NCAV_DISCOUNT_FACTOR == pytest.approx(2 / 3)
        assert DEFAULT_MAX_DE == 1.0
        assert DEFAULT_MIN_MARKET_CAP_USD == 500_000_000.0
