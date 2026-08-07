"""Unit tests for Graham ranking."""

from __future__ import annotations

from datetime import date

import pytest

from agents.graham.ranking import (
    GrahamScore,
    score_candidates,
    score_defensive_candidates,
    select_top_n,
)
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


def _fin(
    ticker: str,
    *,
    current_assets: float,
    total_liabilities: float,
    shares: float = 100.0,
    total_debt: float = 0.0,
    total_equity: float | None = None,
    net_income: float = 1.0,
) -> PointInTimeFinancials:
    if total_equity is None:
        total_equity = current_assets - total_liabilities + 100.0
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
        shares_outstanding=shares,
        total_debt=total_debt,
        long_term_debt=total_debt,
        net_income=net_income,
    )


class TestScoreCandidates:
    def test_orders_by_pncav_ascending(self) -> None:
        # All have NCAV=3.0/share (CA=400, TL=100, shares=100), prices vary
        a = _fin("A", current_assets=400, total_liabilities=100)
        b = _fin("B", current_assets=400, total_liabilities=100)
        c = _fin("C", current_assets=400, total_liabilities=100)
        candidates = [
            (a, 100.0, 1.0),  # P/NCAV = 0.33
            (b, 200.0, 2.0),  # P/NCAV = 0.67
            (c, 50.0, 0.5),  # P/NCAV = 0.17
        ]
        scored = score_candidates(candidates)
        assert [s.ticker for s in scored] == ["C", "A", "B"]

    def test_breaks_tie_by_lower_de(self) -> None:
        a = _fin("A", current_assets=400, total_liabilities=100, total_debt=10)
        b = _fin("B", current_assets=400, total_liabilities=100, total_debt=200)
        candidates = [(a, 100.0, 1.0), (b, 100.0, 1.0)]
        scored = score_candidates(candidates)
        assert scored[0].ticker == "A"

    def test_drops_negative_ncav(self) -> None:
        a = _fin("A", current_assets=100, total_liabilities=200)  # NCAV negative
        b = _fin("B", current_assets=400, total_liabilities=100)
        candidates = [(a, 100.0, 1.0), (b, 100.0, 1.0)]
        scored = score_candidates(candidates)
        assert [s.ticker for s in scored] == ["B"]

    def test_empty_input(self) -> None:
        assert score_candidates([]) == []

    def test_score_fields_correct(self) -> None:
        a = _fin("A", current_assets=400, total_liabilities=100, shares=100)
        scored = score_candidates([(a, 100.0, 1.0)])
        assert len(scored) == 1
        s = scored[0]
        assert s.ticker == "A"
        assert s.ncav_per_share == pytest.approx(3.0)
        assert s.p_ncav == pytest.approx(1 / 3)


class TestSelectTopN:
    def _scores(self, n: int) -> list[GrahamScore]:
        return [
            GrahamScore(
                ticker=f"T{i:03d}",
                price=1.0,
                market_cap=1.0,
                ncav_per_share=3.0,
                p_ncav=0.1 + 0.001 * i,
                debt_to_equity=0.0,
                net_income=10.0,
            )
            for i in range(n)
        ]

    def test_returns_first_n(self) -> None:
        top = select_top_n(self._scores(50), 30)
        assert len(top) == 30

    def test_returns_all_when_fewer(self) -> None:
        top = select_top_n(self._scores(20), 30)
        assert len(top) == 20

    def test_zero_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], 0)

    def test_negative_n_raises(self) -> None:
        with pytest.raises(ValueError):
            select_top_n([], -1)


def _defensive_fin(
    *, total_equity: float = 800_000_000.0, eps: float = 2.0
) -> PointInTimeFinancials:
    """A Defensive candidate: EPS 2.00 on 100M shares, BVPS 8.00."""
    return PointInTimeFinancials(
        ticker="GN",
        as_of=date(2024, 6, 30),
        source_filing=FilingMetadata(
            ticker="GN",
            cik="1",
            form_type="10-K",
            filing_date=date(2024, 2, 15),
            period_of_report=date(2023, 12, 31),
            accession_number="a-GN",
        ),
        eps_diluted=eps,
        eps_basic=eps,
        total_equity=total_equity,
        current_assets=500_000_000.0,
        current_liabilities=200_000_000.0,
        total_liabilities=300_000_000.0,
        total_debt=100_000_000.0,
        long_term_debt=100_000_000.0,
        net_income=200_000_000.0,
        shares_outstanding=100_000_000.0,
        dividends_paid=-20_000_000.0,
    )


class TestGrahamNumber:
    """The decision log recorded pe x pb under the name graham_number.
    One is a dimensionless product, the other a price per share, and the
    playbook's sell trigger — "when the price approaches the Graham
    Number, sell" — was uncheckable against the wrong one."""

    def test_a_dollar_denominated_number_is_computed(self) -> None:
        # EPS 2.00, BVPS 800M/100M = 8.00.
        # sqrt(22.5 x 2 x 8) = sqrt(360) = 18.97
        fin = _defensive_fin()
        scores = score_defensive_candidates([(fin, 1_000_000_000.0, 10.0)])

        assert len(scores) == 1
        assert scores[0].graham_number == pytest.approx(18.9737, rel=1e-4)

    def test_it_is_not_the_composite(self) -> None:
        fin = _defensive_fin()
        s = score_defensive_candidates([(fin, 1_000_000_000.0, 10.0)])[0]

        assert s.composite == pytest.approx(s.pe * s.pb)
        assert s.graham_number != pytest.approx(s.composite)

    def test_margin_of_safety_is_the_discount_to_it(self) -> None:
        fin = _defensive_fin()
        s = score_defensive_candidates([(fin, 1_000_000_000.0, 10.0)])[0]

        # Price 10 against a Graham Number of 18.97 → 47.3% discount.
        assert s.margin_of_safety_pct == pytest.approx(
            (18.9737 - 10.0) / 18.9737 * 100.0, rel=1e-3
        )

    def test_a_price_above_the_number_gives_a_negative_margin(self) -> None:
        fin = _defensive_fin()
        s = score_defensive_candidates([(fin, 3_000_000_000.0, 30.0)])[0]

        assert s.margin_of_safety_pct is not None
        assert s.margin_of_safety_pct < 0

    def test_it_is_none_where_the_formula_is_meaningless(self) -> None:
        # Non-positive book value: sqrt of a negative product.
        fin = _defensive_fin(total_equity=-100_000_000.0)
        scores = score_defensive_candidates([(fin, 1_000_000_000.0, 10.0)])

        # The candidate is dropped before scoring on a negative book,
        # which is the correct outcome; nothing claims a number.
        assert scores == []
