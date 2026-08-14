"""Part 4's limits — the floor that does not move."""

from __future__ import annotations

from agents.council.limits import (
    CIRCUIT_BREAKER_DRAWDOWN,
    MAX_POSITION_AT_ENTRY,
    TRIM_ABOVE,
    LimitState,
    Position,
    breaches,
    check_all,
    check_cash,
    check_clusters,
    check_drawdown,
    check_illiquid,
    check_leverage,
    check_single_names,
    entry_allowed,
    positions_from_portfolio,
    unknowns,
)


class TestSingleName:
    def test_entry_cap_and_trim_level_are_different_numbers(self) -> None:
        """Conflating them either forbids a position that grew into its
        size — which is what winning looks like — or permits entering at
        a size reserved for a holding that earned it."""
        assert MAX_POSITION_AT_ENTRY == 0.25
        assert TRIM_ABOVE == 0.35
        # 30% is too big to open and fine to hold.
        assert entry_allowed(0.30).state is LimitState.BREACH
        held = check_single_names([Position("X", 0.30)])[0]
        assert held.state is LimitState.PASS

    def test_appreciation_past_35_forces_a_trim(self) -> None:
        check = check_single_names([Position("X", 0.42)])[0]
        assert check.state is LimitState.BREACH
        assert check.forces_action


class TestClusters:
    def test_three_names_in_one_cluster_are_one_bet(self) -> None:
        checks = check_clusters(
            [
                Position("A", 0.20, cluster="ai"),
                Position("B", 0.18, cluster="ai"),
                Position("C", 0.10, cluster="ai"),
            ]
        )
        breach = next(c for c in checks if c.limit.endswith(":ai"))
        assert breach.observed == 0.48
        assert breach.state is LimitState.BREACH

    def test_unlabelled_names_are_unknown_not_uncorrelated(self) -> None:
        """Assuming independence is the error this limit exists for."""
        checks = check_clusters([Position("A", 0.60)])
        assert checks[0].state is LimitState.UNKNOWN
        assert "cannot be checked" in checks[0].note


class TestIlliquid:
    def test_aggregate_over_twenty_percent_breaches(self) -> None:
        c = check_illiquid(
            [
                Position("A", 0.15, adv_usd=1_000_000.0),
                Position("B", 0.10, adv_usd=2_000_000.0),
                Position("C", 0.30, adv_usd=50_000_000.0),
            ]
        )
        assert c.observed == 0.25
        assert c.state is LimitState.BREACH

    def test_missing_adv_is_unknown(self) -> None:
        c = check_illiquid([Position("A", 0.30, adv_usd=None)])
        assert c.state is LimitState.UNKNOWN


class TestCashAndLeverage:
    def test_fully_invested_breaches_the_cash_floor(self) -> None:
        assert check_cash(0.0).state is LimitState.BREACH

    def test_five_percent_exactly_passes(self) -> None:
        assert check_cash(0.05).state is LimitState.PASS

    def test_gross_over_one_is_leverage(self) -> None:
        c = check_leverage(1.02)
        assert c.state is LimitState.BREACH
        assert c.forces_action


class TestDrawdown:
    def test_circuit_breaker_at_minus_twenty_five(self) -> None:
        c = check_drawdown(nav=75.0, peak_nav=100.0)
        assert c.observed == CIRCUIT_BREAKER_DRAWDOWN
        assert c.state is LimitState.BREACH

    def test_it_never_forces_a_sale(self) -> None:
        """Volatility is not loss. A drawdown rule that liquidated would
        convert a time-horizon edge into a short-horizon defeat."""
        c = check_drawdown(nav=50.0, peak_nav=100.0)
        assert c.state is LimitState.BREACH
        assert c.forces_action is False

    def test_no_peak_is_unknown(self) -> None:
        assert check_drawdown(nav=10.0, peak_nav=0.0).state is LimitState.UNKNOWN


class TestCheckAll:
    def test_a_clean_book_has_no_breaches(self) -> None:
        positions = [
            Position("A", 0.20, adv_usd=9e6, cluster="a"),
            Position("B", 0.20, adv_usd=9e6, cluster="b"),
        ]
        checks = check_all(
            positions, cash_weight=0.60, nav=100.0, peak_nav=100.0
        )
        assert breaches(checks) == []
        assert unknowns(checks) == []

    def test_portfolio_json_shape_converts(self) -> None:
        positions = positions_from_portfolio(
            [{"ticker": "AAPL", "shares": 10.0, "current_price": 20.0}],
            nav=1_000.0,
        )
        assert positions[0].ticker == "AAPL"
        assert positions[0].weight == 0.2
        # No ADV supplied means unmeasurable, not liquid.
        assert positions[0].adv_usd is None
