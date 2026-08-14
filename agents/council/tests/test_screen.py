"""Every gate must be able to fail, and to say it could not tell.

The distinction the whole screen rests on is UNKNOWN versus FAIL. Both
stop a name, so a test suite that only checked ``passed`` would not
notice if an unreadable balance sheet started reading as a clean one —
which is the failure that hands you the companies whose filings are
worst.
"""

from __future__ import annotations

import pytest

from agents.council.screen import (
    MAX_ACCRUALS_TO_ASSETS,
    MAX_EV_TO_EBIT,
    MAX_NET_DEBT_TO_EBIT,
    MAX_SHARES_CAGR_3Y,
    MIN_CASH_BOX_RUNWAY_YEARS,
    MIN_NET_CASH_TO_MARKET_CAP,
    FilingFlags,
    Financials,
    Outcome,
    ev_to_ebit_ceiling,
    gate_a,
    gate_b,
    gate_c,
    gate_d,
    screen,
)

CLEAN_FLAGS = FilingFlags(
    ticker="T",
    restatement_8k_402=False,
    going_concern=False,
    material_weakness=False,
    late_filing=False,
)


def fin(**kw) -> Financials:
    """A company that passes everything, overridden per test."""
    base = dict(
        ticker="T",
        market_cap=1_000.0,
        enterprise_value=700.0,
        ebit_ttm=100.0,
        cfo_ttm=120.0,
        fcf_ttm=90.0,
        net_income_ttm=100.0,
        total_assets=2_000.0,
        tangible_book=400.0,
        net_cash=300.0,
        net_debt=-300.0,
        shares_cagr_3y=0.0,
    )
    return Financials(**{**base, **kw})


# ---------------------------------------------------------------- gate A


class TestGateA:
    def test_cheap_on_ev_to_ebit_passes(self) -> None:
        g = gate_a(fin(enterprise_value=700.0, ebit_ttm=100.0))
        assert g.outcome is Outcome.PASS
        assert "EV/EBIT" in g.detail

    def test_expensive_on_every_path_fails(self) -> None:
        g = gate_a(
            fin(
                enterprise_value=5_000.0,
                ebit_ttm=100.0,
                net_cash=10.0,
                tangible_book=100.0,
            )
        )
        assert g.outcome is Outcome.FAIL

    def test_a_negative_ebit_closes_only_that_path(self) -> None:
        """Path 1 needs positive EBIT; the other two are untouched."""
        g = gate_a(fin(ebit_ttm=-50.0, net_cash=300.0, market_cap=1_000.0))
        assert g.outcome is Outcome.PASS
        assert "net cash" in g.detail

    def test_net_cash_at_the_threshold_passes(self) -> None:
        g = gate_a(
            fin(
                enterprise_value=5_000.0,
                market_cap=1_000.0,
                net_cash=MIN_NET_CASH_TO_MARKET_CAP * 1_000.0,
                tangible_book=1.0,
            )
        )
        assert g.outcome is Outcome.PASS

    def test_below_tangible_book_passes(self) -> None:
        g = gate_a(
            fin(
                enterprise_value=5_000.0,
                market_cap=300.0,
                net_cash=0.0,
                tangible_book=400.0,
            )
        )
        assert g.outcome is Outcome.PASS
        assert "tangible book" in g.detail

    def test_a_negative_tangible_book_closes_that_path(self) -> None:
        g = gate_a(
            fin(
                enterprise_value=5_000.0,
                market_cap=300.0,
                net_cash=0.0,
                tangible_book=-100.0,
            )
        )
        assert g.outcome is Outcome.FAIL

    def test_nothing_computable_is_unknown_not_fail(self) -> None:
        """Nothing was tested, so nothing was found wanting."""
        g = gate_a(
            Financials(ticker="T", market_cap=None, enterprise_value=None)
        )
        assert g.outcome is Outcome.UNKNOWN

    def test_stale_goodwill_closes_a_door_it_does_not_disqualify(self) -> None:
        """Apple's goodwill was last tagged in 2017."""
        g = gate_a(fin(tangible_book=None))
        assert g.outcome is Outcome.PASS


class TestRateGuard:
    def test_the_default_ceiling_holds_at_normal_rates(self) -> None:
        assert ev_to_ebit_ceiling(0.04) == MAX_EV_TO_EBIT

    def test_no_yield_leaves_the_ceiling_alone(self) -> None:
        """A FRED outage must not close the cheapest path in the screen."""
        assert ev_to_ebit_ceiling(None) == MAX_EV_TO_EBIT

    def test_it_binds_only_above_eight_and_a_half_percent(self) -> None:
        assert ev_to_ebit_ceiling(0.08) == pytest.approx(MAX_EV_TO_EBIT)
        assert ev_to_ebit_ceiling(0.10) < MAX_EV_TO_EBIT

    def test_a_high_rate_tightens_what_counts_as_cheap(self) -> None:
        ceiling = ev_to_ebit_ceiling(0.16)
        assert ceiling == pytest.approx(5.0)
        g = gate_a(fin(enterprise_value=700.0, ebit_ttm=100.0, ten_year_yield=0.16,
                       net_cash=0.0, tangible_book=1.0))
        assert g.outcome is Outcome.FAIL

    def test_a_nonsense_yield_does_not_divide_by_zero(self) -> None:
        assert ev_to_ebit_ceiling(-0.04) == MAX_EV_TO_EBIT


# ---------------------------------------------------------------- gate B


class TestGateB:
    def test_carryable_debt_passes(self) -> None:
        g, cash_box = gate_b(fin(ebit_ttm=100.0, net_debt=200.0))
        assert g.outcome is Outcome.PASS
        assert cash_box is False

    def test_at_the_leverage_ceiling_passes(self) -> None:
        g, _ = gate_b(fin(ebit_ttm=100.0, net_debt=MAX_NET_DEBT_TO_EBIT * 100.0))
        assert g.outcome is Outcome.PASS

    def test_too_much_debt_for_the_earnings_fails(self) -> None:
        g, _ = gate_b(fin(ebit_ttm=100.0, net_debt=400.0))
        assert g.outcome is Outcome.FAIL

    def test_unreadable_debt_is_unknown(self) -> None:
        g, _ = gate_b(fin(ebit_ttm=100.0, net_debt=None))
        assert g.outcome is Outcome.UNKNOWN

    def test_a_loss_maker_with_no_net_cash_fails(self) -> None:
        g, cash_box = gate_b(fin(ebit_ttm=-10.0, net_cash=-5.0, fcf_ttm=-1.0))
        assert g.outcome is Outcome.FAIL
        assert cash_box is False

    def test_a_cash_box_with_runway_passes(self) -> None:
        g, cash_box = gate_b(
            fin(ebit_ttm=-10.0, net_cash=300.0, fcf_ttm=-100.0)
        )
        assert g.outcome is Outcome.PASS
        assert cash_box is True

    def test_a_cash_box_burning_too_fast_fails(self) -> None:
        g, cash_box = gate_b(fin(ebit_ttm=-10.0, net_cash=100.0, fcf_ttm=-100.0))
        assert g.outcome is Outcome.FAIL
        assert cash_box is False

    def test_the_runway_threshold_is_three_years(self) -> None:
        burn = -100.0
        g, _ = gate_b(
            fin(
                ebit_ttm=-10.0,
                net_cash=MIN_CASH_BOX_RUNWAY_YEARS * 100.0,
                fcf_ttm=burn,
            )
        )
        assert g.outcome is Outcome.PASS

    def test_a_loss_maker_generating_cash_passes(self) -> None:
        """Negative EBIT with positive FCF is a real and common shape."""
        g, cash_box = gate_b(fin(ebit_ttm=-10.0, net_cash=50.0, fcf_ttm=5.0))
        assert g.outcome is Outcome.PASS
        assert cash_box is True

    def test_no_ebit_at_all_is_unknown(self) -> None:
        g, _ = gate_b(fin(ebit_ttm=None))
        assert g.outcome is Outcome.UNKNOWN


# ---------------------------------------------------------------- gate C


class TestGateC:
    def test_clean_cash_conversion_passes(self) -> None:
        assert gate_c(fin(), cash_box=False).outcome is Outcome.PASS

    def test_negative_operating_cash_flow_fails(self) -> None:
        g = gate_c(fin(cfo_ttm=-10.0, net_income_ttm=-10.0), cash_box=False)
        assert g.outcome is Outcome.FAIL
        assert "CFO_TTM" in g.detail

    def test_the_cash_box_branch_waives_the_cash_flow_test(self) -> None:
        """Otherwise Gate B's cash-box path is unreachable in practice.

        A company with positive operating cash flow is not the
        loss-maker that branch was written for, so requiring it would
        make the branch dead code.
        """
        g = gate_c(
            fin(cfo_ttm=-50.0, net_income_ttm=-50.0, total_assets=2_000.0),
            cash_box=True,
        )
        assert g.outcome is Outcome.PASS

    def test_high_accruals_fail(self) -> None:
        # NI far above the cash that arrived.
        g = gate_c(
            fin(net_income_ttm=500.0, cfo_ttm=100.0, total_assets=1_000.0),
            cash_box=False,
        )
        assert g.outcome is Outcome.FAIL
        assert "accruals" in g.detail

    def test_accruals_at_the_ceiling_pass(self) -> None:
        assets = 1_000.0
        g = gate_c(
            fin(
                cfo_ttm=100.0,
                net_income_ttm=100.0 + MAX_ACCRUALS_TO_ASSETS * assets,
                total_assets=assets,
            ),
            cash_box=False,
        )
        assert g.outcome is Outcome.PASS

    def test_serial_dilution_fails(self) -> None:
        g = gate_c(fin(shares_cagr_3y=0.15), cash_box=False)
        assert g.outcome is Outcome.FAIL
        assert "shares" in g.detail

    def test_dilution_at_the_ceiling_passes(self) -> None:
        g = gate_c(fin(shares_cagr_3y=MAX_SHARES_CAGR_3Y), cash_box=False)
        assert g.outcome is Outcome.PASS

    def test_buybacks_are_not_penalised(self) -> None:
        g = gate_c(fin(shares_cagr_3y=-0.05), cash_box=False)
        assert g.outcome is Outcome.PASS

    def test_missing_share_history_is_unknown(self) -> None:
        g = gate_c(fin(shares_cagr_3y=None), cash_box=False)
        assert g.outcome is Outcome.UNKNOWN

    def test_missing_assets_is_unknown_not_a_pass(self) -> None:
        g = gate_c(fin(total_assets=None), cash_box=False)
        assert g.outcome is Outcome.UNKNOWN

    def test_zero_assets_does_not_divide_by_zero(self) -> None:
        g = gate_c(fin(total_assets=0.0), cash_box=False)
        assert g.outcome is Outcome.UNKNOWN

    def test_every_failure_is_reported_not_just_the_first(self) -> None:
        g = gate_c(
            fin(
                cfo_ttm=-10.0,
                net_income_ttm=500.0,
                total_assets=1_000.0,
                shares_cagr_3y=0.20,
            ),
            cash_box=False,
        )
        assert g.outcome is Outcome.FAIL
        assert g.detail.count(";") == 2


# ---------------------------------------------------------------- gate D


class TestGateD:
    def test_a_clean_filer_passes(self) -> None:
        assert gate_d(CLEAN_FLAGS).outcome is Outcome.PASS

    @pytest.mark.parametrize(
        "flag",
        [
            "restatement_8k_402",
            "going_concern",
            "material_weakness",
            "late_filing",
        ],
    )
    def test_any_one_disqualifies(self, flag: str) -> None:
        flags = FilingFlags(**{**CLEAN_FLAGS.__dict__, flag: True})
        assert gate_d(flags).outcome is Outcome.FAIL

    def test_an_unrun_check_is_unknown_not_clean(self) -> None:
        """The gate that was never executed must not read as a pass."""
        flags = FilingFlags(**{**CLEAN_FLAGS.__dict__, "going_concern": None})
        g = gate_d(flags)
        assert g.outcome is Outcome.UNKNOWN
        assert "going-concern" in g.detail

    def test_a_real_flag_outranks_an_unrun_check(self) -> None:
        flags = FilingFlags(
            ticker="T",
            restatement_8k_402=True,
            going_concern=None,
            material_weakness=None,
            late_filing=None,
        )
        assert gate_d(flags).outcome is Outcome.FAIL


# -------------------------------------------------------------- the screen


class TestScreen:
    def test_a_clean_candidate_passes_every_gate(self) -> None:
        r = screen(fin(), CLEAN_FLAGS)
        assert r.passed
        assert r.floor is not None
        assert len(r.gates) == 4

    def test_one_unknown_gate_is_enough_to_stop_it(self) -> None:
        r = screen(fin(shares_cagr_3y=None), CLEAN_FLAGS)
        assert not r.passed
        assert r.failures[0].gate == "C"

    def test_every_gate_is_evaluated_even_after_a_failure(self) -> None:
        """How many ways a name failed is what spots a stale gate."""
        r = screen(
            fin(
                enterprise_value=9_999.0,
                net_cash=0.0,
                tangible_book=1.0,
                net_debt=9_999.0,
                shares_cagr_3y=0.5,
            ),
            FilingFlags(
                ticker="T",
                restatement_8k_402=True,
                going_concern=False,
                material_weakness=False,
                late_filing=False,
            ),
        )
        assert len(r.gates) == 4
        assert len(r.failures) == 4

    def test_the_floor_is_recorded_for_the_journal(self) -> None:
        r = screen(
            fin(enterprise_value=9_999.0, market_cap=1_000.0, net_cash=400.0),
            CLEAN_FLAGS,
        )
        assert r.passed
        assert "net cash" in (r.floor or "")

    def test_the_cash_box_flag_survives_into_the_result(self) -> None:
        r = screen(
            fin(ebit_ttm=-10.0, net_cash=300.0, fcf_ttm=-50.0, cfo_ttm=-50.0,
                net_income_ttm=-50.0, market_cap=1_000.0),
            CLEAN_FLAGS,
        )
        assert r.cash_box is True
        assert r.passed

    def test_an_empty_result_is_not_a_pass(self) -> None:
        from agents.council.screen import ScreenResult

        assert ScreenResult(ticker="T").passed is False

    def test_the_summary_names_the_first_failure(self) -> None:
        r = screen(fin(shares_cagr_3y=0.5), CLEAN_FLAGS)
        assert "C" in str(r)
