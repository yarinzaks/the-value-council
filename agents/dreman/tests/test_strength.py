"""Tests for Dreman's financial-strength battery (playbook §4.2).

Two of his six tests were implemented — the D/E ceiling and the
market-cap floor, both gates in ``filters`` — plus a check that the
latest net income is positive, a fragment of Test 5. So nothing between
the contrarian screen and the portfolio could do what §4.2 says the
battery is for: "distinguish overreaction-driven cheapness from
outright deteriorating businesses".
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agents.dreman.strength import (
    DEFAULT_MAX_FAILURES,
    DEFAULT_MIN_JUDGEABLE,
    assess_strength,
    current_ratio,
    earnings_cagr_pct,
    is_deteriorating,
    margin_erosion_pp,
    return_on_equity_pct,
)
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact

AS_OF = date(2026, 8, 5)


def _fin(
    *,
    ticker: str = "TEST",
    current_assets: float | None = 400_000_000.0,
    current_liabilities: float | None = 200_000_000.0,
    net_income: float | None = 120_000_000.0,
    total_equity: float | None = 800_000_000.0,
) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=AS_OF,
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=date(2026, 2, 15),
            period_of_report=date(2025, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        net_income=net_income,
        total_equity=total_equity,
    )


def _fact(concept: str, value: float, fy: int, ticker: str) -> XbrlFact:
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
        accession_number=f"{ticker}-{concept}-{fy}",
    )


def _cache(
    tmp_path: Path,
    ticker: str,
    *,
    revenue: list[float],
    pretax: list[float],
    net_income: list[float],
    first_year: int = 2020,
) -> EdgarCache:
    c = EdgarCache(cache_dir=tmp_path)
    facts: list[XbrlFact] = []
    for i, (r, p, n) in enumerate(zip(revenue, pretax, net_income, strict=True)):
        fy = first_year + i
        facts.append(_fact("Revenues", r, fy, ticker))
        facts.append(
            _fact(
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                p,
                fy,
                ticker,
            )
        )
        facts.append(_fact("NetIncomeLoss", n, fy, ticker))
    c.save_facts(ticker, facts)
    return c


def _healthy(tmp_path: Path, ticker: str = "TEST") -> EdgarCache:
    """Flat margin, growing earnings — passes tests 3 and 5."""
    return _cache(
        tmp_path,
        ticker,
        revenue=[1_000.0] * 6,
        pretax=[150.0] * 6,
        net_income=[100.0 * (1.08**i) for i in range(6)],
    )


class TestIndividualTests:
    def test_current_ratio(self) -> None:
        assert current_ratio(_fin()) == pytest.approx(2.0)

    def test_current_ratio_is_none_without_both_sides(self) -> None:
        assert current_ratio(_fin(current_assets=None)) is None
        assert current_ratio(_fin(current_liabilities=None)) is None

    def test_return_on_equity(self) -> None:
        assert return_on_equity_pct(_fin()) == pytest.approx(15.0)

    def test_roe_is_none_on_negative_book(self) -> None:
        # A negative denominator makes the ratio meaningless, not low.
        assert return_on_equity_pct(_fin(total_equity=-100.0)) is None

    def test_margin_erosion_is_positive_when_the_margin_shrank(
        self, tmp_path: Path
    ) -> None:
        c = _cache(
            tmp_path,
            "FADE",
            revenue=[1_000.0] * 6,
            pretax=[200.0, 190.0, 170.0, 140.0, 110.0, 80.0],
            net_income=[100.0] * 6,
        )

        # 20% down to 8% over five years.
        assert margin_erosion_pp(c, "FADE", AS_OF) == pytest.approx(12.0, abs=0.5)

    def test_a_collapse_in_earnings_reads_as_minus_one_hundred(
        self, tmp_path: Path
    ) -> None:
        c = _cache(
            tmp_path,
            "BUST",
            revenue=[1_000.0] * 6,
            pretax=[100.0] * 6,
            net_income=[100.0, 90.0, 60.0, 20.0, -10.0, -50.0],
        )

        assert earnings_cagr_pct(c, "BUST", AS_OF) == -100.0

    def test_an_unknown_ticker_yields_none(self, tmp_path: Path) -> None:
        c = EdgarCache(cache_dir=tmp_path)

        assert margin_erosion_pp(c, "NOPE", AS_OF) is None
        assert earnings_cagr_pct(c, "NOPE", AS_OF) is None


class TestMissingDataIsNotAFailure:
    """The trap this battery had to avoid.

    Measured over 548 tickers with point-in-time financials, the current
    ratio is computable for 58.6%, ROE for 69.5% and margin for 43.4%.
    Failing a company for a figure its filer never tagged would reject
    two in five on no evidence; passing it would hand a clean grade to a
    company nobody can read — the failure already fixed in the leverage
    helper, Fisher's dilution point and Marks's temperature.
    """

    def test_an_uncomputable_test_is_neither_passed_nor_failed(
        self, tmp_path: Path
    ) -> None:
        a = assess_strength(
            _fin(current_assets=None), _healthy(tmp_path), AS_OF
        )

        assert a.current_ratio is None
        assert a.judgeable == 3

    def test_too_few_judgeable_tests_means_no_verdict(
        self, tmp_path: Path
    ) -> None:
        # Nothing in the cache and no balance-sheet detail: one test at
        # most. "Cannot assess" is not "deteriorating".
        a = assess_strength(
            _fin(current_assets=None, current_liabilities=None),
            EdgarCache(cache_dir=tmp_path),
            AS_OF,
        )

        assert a.judgeable < DEFAULT_MIN_JUDGEABLE
        assert not is_deteriorating(a)

    def test_the_threshold_is_three_of_four(self) -> None:
        assert DEFAULT_MIN_JUDGEABLE == 3


class TestTheVerdict:
    def test_a_sound_business_survives(self, tmp_path: Path) -> None:
        a = assess_strength(_fin(), _healthy(tmp_path), AS_OF)

        assert a.judgeable == 4
        assert a.failed == 0
        assert not is_deteriorating(a)

    def test_one_bad_reading_is_tolerated(self, tmp_path: Path) -> None:
        # A contrarian candidate is by definition having a bad spell.
        # ROE of 5% alone is not deterioration.
        a = assess_strength(
            _fin(net_income=40_000_000.0), _healthy(tmp_path), AS_OF
        )

        assert a.failed == 1
        assert not is_deteriorating(a)

    def test_two_bad_readings_is_a_pattern(self, tmp_path: Path) -> None:
        # Thin liquidity and weak returns together: the value trap the
        # battery exists to catch.
        a = assess_strength(
            _fin(current_assets=150_000_000.0, net_income=40_000_000.0),
            _healthy(tmp_path),
            AS_OF,
        )

        assert a.failed == 2
        assert is_deteriorating(a)

    def test_a_collapsing_margin_and_falling_earnings_are_caught(
        self, tmp_path: Path
    ) -> None:
        c = _cache(
            tmp_path,
            "TEST",
            revenue=[1_000.0] * 6,
            pretax=[200.0, 180.0, 150.0, 120.0, 90.0, 60.0],
            net_income=[100.0, 90.0, 75.0, 60.0, 45.0, 30.0],
        )

        a = assess_strength(_fin(), c, AS_OF)

        assert a.margin_trend is False
        assert a.earnings_trend is False
        assert is_deteriorating(a)

    def test_the_tolerance_is_one(self) -> None:
        assert DEFAULT_MAX_FAILURES == 1

    def test_the_reason_carries_the_numbers(self, tmp_path: Path) -> None:
        a = assess_strength(_fin(), _healthy(tmp_path), AS_OF)

        assert "CR 2.00" in a.reason
        assert "ROE 15.0%" in a.reason


class TestTheBatteryIsWiredIn:
    """A gate nothing calls is a comment.

    ``core.scoring`` already ships a correct Piotroski F-Score, Altman Z
    and Beneish M that no agent imports. This battery must not join
    them, so this asserts the strategy actually runs it.
    """

    def test_the_strategy_drops_a_deteriorating_candidate(
        self, tmp_path: Path
    ) -> None:
        from agents.dreman.contrarian import DavidDreman

        c = _cache(
            tmp_path,
            "ROT",
            revenue=[1_000.0] * 6,
            pretax=[200.0, 180.0, 150.0, 120.0, 90.0, 60.0],
            net_income=[100.0, 90.0, 75.0, 60.0, 45.0, 30.0],
        )
        fin = _fin(ticker="ROT")
        s = DavidDreman(edgar_cache=c, min_market_cap=1.0, portfolio_size=5)

        class _P:
            def get(self, t: str) -> float | None:
                return 10.0

        class _F:
            def get(self, t: str) -> object | None:
                return fin

        out = s.select(
            as_of=AS_OF,
            universe=["ROT"],
            prices=_P(),  # type: ignore[arg-type]
            fundamentals=_F(),  # type: ignore[arg-type]
        )

        assert "ROT" not in out

    def test_without_a_cache_the_battery_is_skipped_not_crashed(
        self, tmp_path: Path
    ) -> None:
        # Backtests that do not supply a cache keep working; they just
        # do not get the battery.
        from agents.dreman.contrarian import DavidDreman

        s = DavidDreman(min_market_cap=1.0, portfolio_size=5)

        class _P:
            def get(self, t: str) -> float | None:
                return 10.0

        class _F:
            def get(self, t: str) -> object | None:
                return _fin(ticker="OK")

        s.select(
            as_of=AS_OF,
            universe=["OK"],
            prices=_P(),  # type: ignore[arg-type]
            fundamentals=_F(),  # type: ignore[arg-type]
        )
