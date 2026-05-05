"""Unit tests for the deterministic scoring functions."""

from __future__ import annotations

import math

import pytest

from core.scoring import (
    altman_z_score,
    beneish_m_score,
    graham_number,
    piotroski_f_score,
)
from core.scoring.beneish import BeneishInputs
from core.scoring.piotroski import PiotroskiInputs


class TestGrahamNumber:
    def test_classic_calculation(self) -> None:
        # sqrt(22.5 * 5 * 20) = sqrt(2250) ≈ 47.43
        assert graham_number(eps=5.0, book_value_per_share=20.0) == pytest.approx(
            math.sqrt(2250)
        )

    @pytest.mark.parametrize(
        ("eps", "bvps"),
        [(0, 10), (-1, 5), (5, 0), (5, -1), (None, 5), (5, None)],
    )
    def test_nonpositive_or_missing_returns_none(self, eps, bvps) -> None:
        assert graham_number(eps, bvps) is None


class TestAltmanZScore:
    def test_safe_zone(self) -> None:
        # Healthy firm: large cap, low leverage, profitable.
        z = altman_z_score(
            working_capital=200,
            retained_earnings=500,
            ebit=300,
            market_cap=5_000,
            sales=2_000,
            total_assets=1_000,
            total_liabilities=300,
        )
        assert z > 2.99

    def test_distress_zone(self) -> None:
        z = altman_z_score(
            working_capital=-50,
            retained_earnings=-200,
            ebit=-100,
            market_cap=50,
            sales=200,
            total_assets=1_000,
            total_liabilities=900,
        )
        assert z < 1.81

    def test_zero_assets_raises(self) -> None:
        with pytest.raises(ValueError):
            altman_z_score(
                working_capital=0,
                retained_earnings=0,
                ebit=0,
                market_cap=0,
                sales=0,
                total_assets=0,
                total_liabilities=100,
            )


class TestPiotroskiFScore:
    def _strong_inputs(self) -> PiotroskiInputs:
        return PiotroskiInputs(
            net_income=100,
            operating_cash_flow=150,
            total_assets_current=1_000,
            total_assets_prior=900,
            long_term_debt_current=80,
            long_term_debt_prior=100,
            current_ratio_current=2.5,
            current_ratio_prior=2.0,
            shares_outstanding_current=1_000_000,
            shares_outstanding_prior=1_000_000,
            gross_margin_current=0.45,
            gross_margin_prior=0.40,
            asset_turnover_current=1.2,
            asset_turnover_prior=1.0,
            return_on_assets_prior=0.05,
        )

    def test_all_criteria_pass(self) -> None:
        score, breakdown = piotroski_f_score(self._strong_inputs())
        assert score == 9
        assert all(breakdown.values())

    def test_unprofitable_firm(self) -> None:
        bad = self._strong_inputs()
        score, breakdown = piotroski_f_score(
            PiotroskiInputs(**{**bad.__dict__, "net_income": -50, "operating_cash_flow": -10})
        )
        assert score < 9
        assert breakdown["positive_net_income"] is False
        assert breakdown["positive_operating_cash_flow"] is False


class TestBeneishMScore:
    def _clean_inputs(self) -> BeneishInputs:
        # Steady-state firm — all YoY ratios near 1, low accruals.
        return BeneishInputs(
            receivables_current=100,
            receivables_prior=100,
            sales_current=1_000,
            sales_prior=1_000,
            cogs_current=600,
            cogs_prior=600,
            current_assets_current=300,
            current_assets_prior=300,
            ppe_current=400,
            ppe_prior=400,
            total_assets_current=1_000,
            total_assets_prior=1_000,
            depreciation_current=40,
            depreciation_prior=40,
            sga_current=200,
            sga_prior=200,
            long_term_debt_current=200,
            long_term_debt_prior=200,
            net_income_current=100,
            operating_cash_flow_current=120,
        )

    def test_clean_company_below_threshold(self) -> None:
        m, ratios = beneish_m_score(self._clean_inputs())
        # A perfectly-stable firm should not trip the manipulation flag.
        assert m < -1.78
        assert set(ratios) == {"DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "LVGI", "TATA"}

    def test_aggressive_receivables_growth_raises_score(self) -> None:
        clean = self._clean_inputs()
        suspicious = BeneishInputs(
            **{**clean.__dict__, "receivables_current": 300, "sales_current": 1_050}
        )
        m_clean, _ = beneish_m_score(clean)
        m_susp, _ = beneish_m_score(suspicious)
        assert m_susp > m_clean
