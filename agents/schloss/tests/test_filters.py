"""Unit tests for Schloss universe filters."""

from __future__ import annotations

from datetime import date

import pytest

from agents.schloss.filters import (
    DEFAULT_MAX_DE,
    DEFAULT_MAX_PB,
    DEFAULT_MIN_MARKET_CAP_USD,
    DEFAULT_MIN_YEARS_PUBLIC,
    book_value_per_share,
    debt_to_equity,
    filter_candidates,
    is_distressed_price,
    passes_filters,
    price_to_book,
    tangible_book_value_per_share,
    tangible_common_equity,
    years_public,
)
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


def _fin(
    *,
    ticker: str = "TEST",
    total_equity: float | None = 1_000_000_000.0,
    shares_outstanding: float | None = 100_000_000.0,
    total_debt: float | None = 200_000_000.0,
    long_term_debt: float | None = 200_000_000.0,
    net_income: float | None = 50_000_000.0,
    filing_date: date = date(2018, 6, 15),
    total_assets: float | None = 1_000_000_000.0,
    total_liabilities: float | None = 200_000_000.0,
    current_liabilities: float | None = 200_000_000.0,
) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=date(2024, 6, 30),
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=filing_date,
            period_of_report=date(2017, 12, 31),
            accession_number=f"acc-{ticker}",
        ),
        total_equity=total_equity,
        shares_outstanding=shares_outstanding,
        total_debt=total_debt,
        long_term_debt=long_term_debt,
        net_income=net_income,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        current_liabilities=current_liabilities,
    )


class TestBookValuePerShare:
    def test_basic(self) -> None:
        bvps = book_value_per_share(_fin())
        # 1B / 100M = 10.0
        assert bvps == pytest.approx(10.0)

    def test_missing_equity(self) -> None:
        assert book_value_per_share(_fin(total_equity=None)) is None

    def test_missing_shares(self) -> None:
        assert book_value_per_share(_fin(shares_outstanding=None)) is None

    def test_negative_equity_returns_none(self) -> None:
        assert book_value_per_share(_fin(total_equity=-100_000_000)) is None

    def test_zero_shares_returns_none(self) -> None:
        assert book_value_per_share(_fin(shares_outstanding=0)) is None

    def test_none_input_returns_none(self) -> None:
        assert book_value_per_share(None) is None


class TestPriceToBook:
    def test_basic(self) -> None:
        # BVPS = 10.0, price = 7.5 → P/B = 0.75
        pb = price_to_book(7.5, _fin())
        assert pb == pytest.approx(0.75)

    def test_low_price_low_pb(self) -> None:
        pb = price_to_book(5.0, _fin())
        assert pb == pytest.approx(0.5)

    def test_missing_price(self) -> None:
        assert price_to_book(None, _fin()) is None

    def test_zero_price(self) -> None:
        assert price_to_book(0.0, _fin()) is None

    def test_missing_fin(self) -> None:
        assert price_to_book(10.0, None) is None


class TestDebtToEquity:
    def test_basic(self) -> None:
        # 200M / 1B = 0.2
        assert debt_to_equity(_fin()) == pytest.approx(0.2)

    def test_falls_back_to_long_term_debt(self) -> None:
        de = debt_to_equity(_fin(total_debt=None, long_term_debt=300_000_000))
        assert de == pytest.approx(0.3)

    def test_missing_debt_on_a_tagged_balance_sheet_is_zero(self) -> None:
        # A filer that tagged assets, liabilities and current liabilities
        # but no debt concept has told us its liabilities are not
        # borrowings. XBRL does not require tagging a zero.
        assert debt_to_equity(
            _fin(
                total_debt=None,
                long_term_debt=None,
                total_assets=1_000_000_000.0,
                total_liabilities=200_000_000.0,
                current_liabilities=200_000_000.0,
            )
        ) == pytest.approx(0.0)

    def test_missing_debt_on_a_sparse_balance_sheet_is_none(self) -> None:
        # "Little or no debt" is one of Schloss's sixteen rules. It was
        # being satisfied by absence of data.
        assert (
            debt_to_equity(
                _fin(
                    total_debt=None,
                    long_term_debt=None,
                    total_assets=None,
                    total_liabilities=None,
                    current_liabilities=None,
                )
            )
            is None
        )

    def test_negative_equity_returns_none(self) -> None:
        assert debt_to_equity(_fin(total_equity=-1)) is None

    def test_none_input(self) -> None:
        assert debt_to_equity(None) is None


class TestYearsPublic:
    def test_basic(self) -> None:
        yp = years_public(_fin(filing_date=date(2010, 1, 1)), as_of=date(2024, 6, 30))
        assert yp == 14

    def test_recent_filing_zero_years(self) -> None:
        yp = years_public(_fin(filing_date=date(2024, 6, 1)), as_of=date(2024, 6, 30))
        assert yp == 0

    def test_none_returns_none(self) -> None:
        assert years_public(None, as_of=date(2024, 6, 30)) is None


class TestPassesFilters:
    def _make(self, **overrides) -> tuple:
        # Returns (fin, market_cap, price)
        defaults = dict(
            total_equity=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            total_debt=200_000_000.0,
            net_income=50_000_000.0,
            filing_date=date(2010, 1, 1),
        )
        defaults.update({k: v for k, v in overrides.items() if k in defaults})
        fin = _fin(**defaults)
        # P/B 0.5 by default → comfortably below 0.75
        price = overrides.get("price", 5.0)
        market_cap = price * defaults["shares_outstanding"]
        return fin, market_cap, price

    def test_clean_candidate_passes(self) -> None:
        fin, mcap, price = self._make()
        result = passes_filters(fin, mcap, price, as_of=date(2024, 6, 30))
        assert result.passed is True
        assert result.rejection_reason is None

    def test_pb_above_threshold_rejected(self) -> None:
        fin, mcap, price = self._make(price=12.0)  # P/B = 1.2
        result = passes_filters(fin, mcap, price, as_of=date(2024, 6, 30))
        assert result.passed is False
        assert "P/B" in (result.rejection_reason or "")

    def test_de_above_threshold_rejected(self) -> None:
        fin, mcap, price = self._make(total_debt=2_000_000_000)  # D/E = 2.0
        result = passes_filters(fin, mcap, price, as_of=date(2024, 6, 30))
        assert result.passed is False
        assert "D/E" in (result.rejection_reason or "")

    def test_negative_book_rejected(self) -> None:
        fin, mcap, price = self._make(total_equity=-100_000_000)
        result = passes_filters(fin, mcap, price, as_of=date(2024, 6, 30))
        assert result.passed is False

    def test_negative_net_income_rejected(self) -> None:
        fin, mcap, price = self._make(net_income=-50_000_000)
        result = passes_filters(fin, mcap, price, as_of=date(2024, 6, 30))
        assert result.passed is False
        assert "net income" in (result.rejection_reason or "")

    def test_recent_ipo_rejected(self) -> None:
        # Filing date 2 years before as_of — fails the 5-year minimum
        fin, mcap, price = self._make(filing_date=date(2022, 6, 30))
        result = passes_filters(fin, mcap, price, as_of=date(2024, 6, 30))
        assert result.passed is False
        assert "years public" in (result.rejection_reason or "")

    def test_micro_cap_rejected(self) -> None:
        fin, mcap, price = self._make()
        # Override to small mcap
        result = passes_filters(
            fin, 50_000_000, price, as_of=date(2024, 6, 30)
        )
        assert result.passed is False
        assert "market cap" in (result.rejection_reason or "")

    def test_missing_fin_rejected(self) -> None:
        result = passes_filters(None, 1_000_000_000, 10.0, as_of=date(2024, 6, 30))
        assert result.passed is False


class TestFilterCandidatesBatch:
    def test_returns_only_passers(self) -> None:
        # Build 3 candidates: 1 passes, 2 fail
        ok = _fin(ticker="OK", filing_date=date(2010, 1, 1))
        expensive = _fin(
            ticker="EXP", total_equity=1_000_000_000, shares_outstanding=100_000_000
        )  # but price will make P/B too high
        loss = _fin(ticker="LOSS", net_income=-100_000_000, filing_date=date(2010, 1, 1))
        candidates = [
            (ok, 5 * 100_000_000, 5.0),  # P/B 0.5
            (expensive, 50 * 100_000_000, 50.0),  # P/B 5.0
            (loss, 5 * 100_000_000, 5.0),  # negative income
        ]
        result = filter_candidates(candidates, as_of=date(2024, 6, 30))
        assert len(result) == 1
        assert result[0][0].ticker == "OK"


class TestDefaults:
    def test_default_max_pb(self) -> None:
        assert DEFAULT_MAX_PB == 0.75

    def test_default_max_de(self) -> None:
        assert DEFAULT_MAX_DE == 1.0

    def test_default_min_years_public(self) -> None:
        assert DEFAULT_MIN_YEARS_PUBLIC >= 5

    def test_default_min_market_cap(self) -> None:
        assert DEFAULT_MIN_MARKET_CAP_USD >= 100_000_000.0


class TestTangibleBook:
    """Schloss bought below book because book estimated what the
    business would fetch broken up. Goodwill is the premium a prior
    management paid on an acquisition — no liquidation value, and the
    first thing written off when things go wrong. The screen was
    computing P/B on stated equity while the decision log told the
    reader it traded below *tangible* book."""

    def test_goodwill_and_intangibles_are_stripped(self) -> None:
        fin = _fin(total_equity=1_000_000_000.0)
        object.__setattr__(fin, "goodwill", 300_000_000.0)
        object.__setattr__(fin, "intangible_assets", 100_000_000.0)

        assert tangible_common_equity(fin) == pytest.approx(600_000_000.0)

    def test_absence_is_read_as_none_carried(self) -> None:
        # A filer that tags neither plausibly carries neither; the
        # corroboration is the equity figure, which must be present.
        fin = _fin(total_equity=1_000_000_000.0)

        assert tangible_common_equity(fin) == pytest.approx(1_000_000_000.0)

    def test_pb_uses_tangible_book_by_default(self) -> None:
        fin = _fin(total_equity=1_000_000_000.0)
        object.__setattr__(fin, "goodwill", 500_000_000.0)

        # BVPS 10.00 stated, 5.00 tangible. At $7.50 the stated ratio
        # says 0.75 — a bargain — and the tangible one says 1.50.
        assert price_to_book(7.5, fin, tangible=False) == pytest.approx(0.75)
        assert price_to_book(7.5, fin) == pytest.approx(1.50)

    def test_a_goodwill_heavy_name_is_now_rejected(self) -> None:
        fin = _fin(total_equity=1_000_000_000.0)
        object.__setattr__(fin, "goodwill", 500_000_000.0)

        result = passes_filters(
            fin, 750_000_000.0, 7.5, as_of=date(2024, 6, 30)
        )

        assert result.passed is False
        assert "P/B" in (result.rejection_reason or "")

    def test_intangibles_exceeding_equity_leave_no_book(self) -> None:
        fin = _fin(total_equity=1_000_000_000.0)
        object.__setattr__(fin, "goodwill", 1_200_000_000.0)

        assert tangible_book_value_per_share(fin) is None
        assert price_to_book(5.0, fin) is None


class TestDistressedPrice:
    """The playbook states the entry as a rejection — near the 52-week
    low, or 50% off the five-year high, and 'if neither, REJECT'. The
    two are alternatives, not a conjunction."""

    def test_near_the_52_week_low_qualifies(self) -> None:
        assert is_distressed_price(21.0, low_52w=20.0, high_5y=25.0) is True

    def test_far_off_the_five_year_high_qualifies(self) -> None:
        # Bounced well off the low, but still down 60% from the peak —
        # the case an AND would wrongly reject.
        assert is_distressed_price(40.0, low_52w=20.0, high_5y=100.0) is True

    def test_neither_is_rejected(self) -> None:
        assert is_distressed_price(90.0, low_52w=20.0, high_5y=100.0) is False

    def test_the_tolerance_band_is_inclusive(self) -> None:
        # 10% above the low still counts; Schloss bought capitulation,
        # not the exact tick.
        assert is_distressed_price(22.0, low_52w=20.0, high_5y=1_000.0) is True
        assert is_distressed_price(22.01, low_52w=20.0, high_5y=23.0) is False

    def test_one_reference_price_is_enough(self) -> None:
        assert is_distressed_price(21.0, low_52w=20.0, high_5y=None) is True
        assert is_distressed_price(40.0, low_52w=None, high_5y=100.0) is True

    def test_no_reference_prices_is_unknown_not_false(self) -> None:
        # So the caller can tell "does not qualify" from "cannot tell".
        assert is_distressed_price(21.0, low_52w=None, high_5y=None) is None

    def test_the_gate_is_opt_in(self) -> None:
        # Off by default: it needs price history the caller supplies.
        fin = _fin()
        assert passes_filters(
            fin, 750_000_000.0, 5.0, as_of=date(2024, 6, 30)
        ).passed is True

    def test_the_gate_rejects_when_enabled(self) -> None:
        fin = _fin()
        result = passes_filters(
            fin,
            750_000_000.0,
            5.0,
            as_of=date(2024, 6, 30),
            low_52w=1.0,
            high_5y=6.0,
            require_distressed_price=True,
        )

        assert result.passed is False
        assert "52-week low" in (result.rejection_reason or "")
