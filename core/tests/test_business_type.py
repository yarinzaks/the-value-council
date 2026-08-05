"""Tests for SIC-based business-type routing.

Free cash flow is operating cash flow minus capex. For a bank the
first term moves with deposit inflows, for an insurer with premiums
collected before claims are paid. Valuing either on OCF - capex
capitalises other people's money as owner earnings.
"""

from __future__ import annotations

import pytest

from core.data.sic_codes import sic_for
from core.screener.business_type import (
    cash_flow_valuation_is_meaningful,
    is_financial,
    is_utility,
)


class TestIsFinancial:
    @pytest.mark.parametrize(
        "sic,label",
        [
            (6021, "national commercial bank"),
            (6141, "personal credit institution"),
            (6211, "security broker"),
            (6311, "life insurance"),
            (6411, "insurance agent"),
            (6512, "real estate operator"),
            (6798, "REIT"),
        ],
    )
    def test_division_h_is_financial(self, sic: int, label: str) -> None:
        assert is_financial(sic), label

    @pytest.mark.parametrize("sic", [2834, 3571, 5812, 7372, 4813, 1311])
    def test_operating_companies_are_not(self, sic: int) -> None:
        assert not is_financial(sic)

    def test_unknown_falls_open(self) -> None:
        # A company we cannot classify is left to the other filters.
        assert not is_financial(None)
        assert not is_financial("")
        assert not is_financial("not-a-code")

    def test_accepts_a_string_code(self) -> None:
        assert is_financial("6021")

    def test_resolves_from_a_ticker_when_no_code_given(self) -> None:
        assert is_financial(None, "JPM")
        assert not is_financial(None, "AAPL")

    def test_sic_66_is_unassigned_and_not_financial(self) -> None:
        assert not is_financial(6600)


class TestIsUtility:
    @pytest.mark.parametrize("sic", [4911, 4924, 4941, 4953])
    def test_regulated_services(self, sic: int) -> None:
        assert is_utility(sic)

    def test_telecoms_are_not_utilities(self) -> None:
        # 481x is communications, which Greenblatt does not exclude.
        assert not is_utility(4813)


class TestCashFlowValuation:
    def test_refused_for_financials(self) -> None:
        assert not cash_flow_valuation_is_meaningful(6021)

    def test_allowed_for_operating_companies(self) -> None:
        assert cash_flow_valuation_is_meaningful(3571)

    def test_allowed_for_utilities(self) -> None:
        # A utility's OCF is real operating cash. Greenblatt excludes
        # them for a different reason — rate regulation distorts return
        # on capital, not cash flow.
        assert cash_flow_valuation_is_meaningful(4911)

    def test_real_tickers(self) -> None:
        assert not cash_flow_valuation_is_meaningful(sic_for("JPM"))
        assert cash_flow_valuation_is_meaningful(sic_for("AAPL"))
