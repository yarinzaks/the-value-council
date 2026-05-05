"""Unit tests for Schloss ranking math."""

from __future__ import annotations

from datetime import date

import pytest

from agents.schloss.ranking import (
    SchlossScore,
    score_candidates,
    select_top_n,
)
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


def _fin(
    ticker: str,
    *,
    total_equity: float,
    shares_outstanding: float,
    total_debt: float = 0.0,
    net_income: float = 1_000_000.0,
) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=date(2024, 6, 30),
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=date(2010, 1, 1),
            period_of_report=date(2009, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        total_equity=total_equity,
        shares_outstanding=shares_outstanding,
        total_debt=total_debt,
        long_term_debt=total_debt,
        net_income=net_income,
    )


class TestScoreCandidates:
    def test_orders_by_pb_ascending(self) -> None:
        # Three candidates with known P/B values
        # A: P/B 0.3, B: P/B 0.5, C: P/B 0.7
        a = _fin("A", total_equity=1_000, shares_outstanding=100)  # BVPS 10
        b = _fin("B", total_equity=1_000, shares_outstanding=100)  # BVPS 10
        c = _fin("C", total_equity=1_000, shares_outstanding=100)  # BVPS 10
        candidates = [
            (a, 100 * 3, 3.0),  # P/B 0.3
            (b, 100 * 5, 5.0),  # P/B 0.5
            (c, 100 * 7, 7.0),  # P/B 0.7
        ]
        scored = score_candidates(candidates)
        assert [s.ticker for s in scored] == ["A", "B", "C"]
        assert scored[0].pb_ratio == pytest.approx(0.3)
        assert scored[2].pb_ratio == pytest.approx(0.7)

    def test_breaks_ties_by_lower_de(self) -> None:
        # Both at P/B 0.5, A has lower D/E
        a = _fin("A", total_equity=1_000, shares_outstanding=100, total_debt=100)
        b = _fin("B", total_equity=1_000, shares_outstanding=100, total_debt=500)
        candidates = [
            (a, 500.0, 5.0),
            (b, 500.0, 5.0),
        ]
        scored = score_candidates(candidates)
        # A first (lower D/E)
        assert scored[0].ticker == "A"

    def test_empty_input_returns_empty(self) -> None:
        assert score_candidates([]) == []

    def test_score_includes_all_metrics(self) -> None:
        a = _fin("A", total_equity=1_000, shares_outstanding=100, total_debt=200, net_income=50)
        scored = score_candidates([(a, 500.0, 5.0)])
        assert len(scored) == 1
        s = scored[0]
        assert s.ticker == "A"
        assert s.price == 5.0
        assert s.market_cap == 500.0
        assert s.book_value_per_share == pytest.approx(10.0)
        assert s.pb_ratio == pytest.approx(0.5)
        assert s.debt_to_equity == pytest.approx(0.2)
        assert s.net_income == 50.0

    def test_skips_undefined_metrics(self) -> None:
        # Equity zero → BVPS undefined → skipped silently
        a = _fin("A", total_equity=0, shares_outstanding=100)
        b = _fin("B", total_equity=1_000, shares_outstanding=100)
        candidates = [
            (a, 500.0, 5.0),
            (b, 500.0, 5.0),
        ]
        scored = score_candidates(candidates)
        assert len(scored) == 1
        assert scored[0].ticker == "B"


class TestSelectTopN:
    def _scores(self, n: int) -> list[SchlossScore]:
        return [
            SchlossScore(
                ticker=f"T{i:03d}",
                price=10.0 - i * 0.05,
                market_cap=1_000_000_000.0,
                book_value_per_share=20.0,
                pb_ratio=0.3 + 0.001 * i,
                debt_to_equity=0.2,
                net_income=10_000_000.0,
            )
            for i in range(n)
        ]

    def test_returns_first_n(self) -> None:
        top = select_top_n(self._scores(150), 100)
        assert len(top) == 100
        assert top[0].ticker == "T000"
        assert top[-1].ticker == "T099"

    def test_returns_all_when_fewer_available(self) -> None:
        top = select_top_n(self._scores(40), 100)
        assert len(top) == 40

    def test_zero_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], 0)

    def test_negative_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], -5)
