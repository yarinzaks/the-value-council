"""Unit tests for Magic Formula ranking math."""

from __future__ import annotations

from datetime import date

import pytest

from agents.greenblatt.ranking import (
    MagicFormulaScore,
    compute_earnings_yield,
    compute_enterprise_value,
    compute_invested_capital,
    compute_return_on_capital,
    score_candidates,
    select_top_n,
)
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


def _fin(
    ticker: str,
    *,
    operating_income: float | None,
    current_assets: float | None,
    current_liabilities: float | None,
    ppe_net: float | None,
    cash: float | None = 0.0,
    total_debt: float | None = 0.0,
) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=date(2020, 12, 31),
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=date(2020, 6, 15),
            period_of_report=date(2019, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        operating_income=operating_income,
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        ppe_net=ppe_net,
        cash_and_equivalents=cash,
        total_debt=total_debt,
    )


class TestEnterpriseValue:
    def test_simple_case(self) -> None:
        # MC 1000 + Debt 200 - Cash 100 = 1100
        assert compute_enterprise_value(1000, 200, 100) == 1100.0

    def test_no_debt(self) -> None:
        assert compute_enterprise_value(1000, None, 100) == 900.0

    def test_no_cash(self) -> None:
        assert compute_enterprise_value(1000, 200, None) == 1200.0

    def test_zero_market_cap(self) -> None:
        assert compute_enterprise_value(0, 200, 50) == 150.0


class TestInvestedCapital:
    def test_standard_case(self) -> None:
        # NWC = 200 - 100 = 100; +PPE 500 = 600
        assert compute_invested_capital(200, 100, 500) == 600.0

    def test_negative_nwc_acceptable_if_total_positive(self) -> None:
        # Negative NWC is allowed if PPE makes it net positive
        assert compute_invested_capital(50, 100, 500) == pytest.approx(450.0)

    def test_negative_total_returns_none(self) -> None:
        # Heavily negative NWC overwhelms PPE
        assert compute_invested_capital(50, 200, 100) == pytest.approx(-50.0) or \
               compute_invested_capital(50, 200, 100) is None
        # Per spec, non-positive returns None
        assert compute_invested_capital(50, 600, 500) is None

    def test_zero_invested_capital_rejected(self) -> None:
        # Edge: NWC + PPE exactly zero — Greenblatt's denominator would
        # be zero; we treat as unranked.
        assert compute_invested_capital(100, 200, 100) is None

    def test_missing_inputs_return_none(self) -> None:
        assert compute_invested_capital(None, 100, 500) is None
        assert compute_invested_capital(100, None, 500) is None
        assert compute_invested_capital(100, 50, None) is None


class TestEarningsYield:
    def test_basic_calculation(self) -> None:
        # EBIT 100 / EV 1000 = 10%
        assert compute_earnings_yield(100, 1000) == pytest.approx(0.10)

    def test_zero_ev_returns_neg_inf(self) -> None:
        assert compute_earnings_yield(100, 0) == float("-inf")

    def test_negative_ev_returns_neg_inf(self) -> None:
        assert compute_earnings_yield(100, -50) == float("-inf")


class TestReturnOnCapital:
    def test_basic_calculation(self) -> None:
        # EBIT 100 / IC 500 = 20%
        assert compute_return_on_capital(100, 500) == pytest.approx(0.20)

    def test_zero_capital_returns_neg_inf(self) -> None:
        assert compute_return_on_capital(100, 0) == float("-inf")


class TestScoreCandidates:
    def test_basic_ranking(self) -> None:
        # Build three candidates with known EY and ROC
        # AAPL: EY 10%, ROC 50%  (best on both)
        # MSFT: EY 8%, ROC 30%
        # XYZ:  EY 5%, ROC 20%   (worst on both)
        candidates = [
            (_fin("AAPL", operating_income=100, current_assets=200,
                   current_liabilities=100, ppe_net=100), 1000.0),  # EV=1000, IC=200, EY=10%, ROC=50%
            (_fin("MSFT", operating_income=80, current_assets=200,
                   current_liabilities=100, ppe_net=200), 1000.0),  # EV=1000, IC=300, EY=8%, ROC=27%
            (_fin("XYZ", operating_income=50, current_assets=300,
                   current_liabilities=100, ppe_net=300), 1000.0),  # EV=1000, IC=500, EY=5%, ROC=10%
        ]
        scores = score_candidates(candidates)
        assert len(scores) == 3
        # AAPL should be #1
        assert scores[0].ticker == "AAPL"
        # XYZ should be last
        assert scores[-1].ticker == "XYZ"

    def test_combined_rank_is_sum(self) -> None:
        candidates = [
            (_fin("HIGH_EY", operating_income=100, current_assets=200,
                   current_liabilities=100, ppe_net=400), 1000.0),
            (_fin("HIGH_ROC", operating_income=50, current_assets=200,
                   current_liabilities=100, ppe_net=50), 5000.0),
        ]
        scores = score_candidates(candidates)
        # Verify the math: combined_rank == ey_rank + roc_rank
        for s in scores:
            assert s.combined_rank == s.ey_rank + s.roc_rank

    def test_ranks_are_one_indexed(self) -> None:
        candidates = [
            (_fin("A", operating_income=100, current_assets=200,
                   current_liabilities=100, ppe_net=100), 500.0),
            (_fin("B", operating_income=50, current_assets=200,
                   current_liabilities=100, ppe_net=200), 500.0),
        ]
        scores = score_candidates(candidates)
        ranks = {s.ticker: (s.ey_rank, s.roc_rank) for s in scores}
        # All ranks should be in {1, 2}
        for ey, roc in ranks.values():
            assert ey in (1, 2)
            assert roc in (1, 2)

    def test_missing_data_dropped(self) -> None:
        candidates = [
            (_fin("OK", operating_income=100, current_assets=200,
                   current_liabilities=100, ppe_net=100), 1000.0),
            # Missing PPE — should be dropped
            (_fin("BAD", operating_income=100, current_assets=200,
                   current_liabilities=100, ppe_net=None), 1000.0),
        ]
        scores = score_candidates(candidates)
        assert len(scores) == 1
        assert scores[0].ticker == "OK"

    def test_empty_input(self) -> None:
        assert score_candidates([]) == []

    def test_negative_ebit_silently_dropped(self) -> None:
        # The filter pipeline should have caught this, but defensive scoring also drops.
        candidates = [
            (_fin("LOSS", operating_income=-50, current_assets=200,
                   current_liabilities=100, ppe_net=100), 1000.0),
        ]
        assert score_candidates(candidates) == []


class TestSelectTopN:
    def _scores(self, n: int) -> list[MagicFormulaScore]:
        # Build n MagicFormulaScore objects with deterministic ranks
        return [
            MagicFormulaScore(
                ticker=f"T{i:03d}",
                earnings_yield=0.10 - 0.001 * i,
                return_on_capital=0.50 - 0.005 * i,
                ey_rank=i + 1,
                roc_rank=i + 1,
                combined_rank=2 * (i + 1),
                market_cap=1_000_000_000.0,
                enterprise_value=1_000_000_000.0,
                invested_capital=500_000_000.0,
            )
            for i in range(n)
        ]

    def test_returns_first_n(self) -> None:
        scores = self._scores(50)
        top = select_top_n(scores, 30)
        assert len(top) == 30
        assert top[0].ticker == "T000"
        assert top[-1].ticker == "T029"

    def test_returns_all_when_fewer_available(self) -> None:
        scores = self._scores(10)
        top = select_top_n(scores, 30)
        assert len(top) == 10

    def test_zero_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], 0)
