"""Tests for joining filings to prices and the ratios built from both.

The join is where a backtest usually acquires look-ahead bias, and it
does so silently: handing a January rebalance the numbers published in
April produces a strategy that knows earnings before they are announced
and reports the result as skill. Most of what follows exists to pin
that boundary down.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.research.factors import (
    MIN_EV_TO_MARKET_CAP,
    add_fundamental_factors,
    enterprise_value,
    market_capitalisation,
)

MONTHS = pd.to_datetime(
    ["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30", "2020-05-29"]
)


def _prices(tickers: tuple[str, ...] = ("AAA",), price: float = 10.0) -> pd.DataFrame:
    index = pd.MultiIndex.from_product([MONTHS, tickers], names=["date", "ticker"])
    return pd.DataFrame({"price": price, "mom_12_1": 0.1}, index=index)


def _fundamentals(rows: list[tuple[str, str, dict[str, float]]]) -> pd.DataFrame:
    """``[(date, ticker, {field: value})]`` as a quarterly panel."""
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(d), t) for d, t, _ in rows], names=["date", "ticker"]
    )
    return pd.DataFrame([v for _, _, v in rows], index=index).sort_index()


class TestMarketCapitalisation:
    def test_it_multiplies_price_by_the_restated_share_count(self) -> None:
        panel = pd.DataFrame(
            {"price": [10.0], "shares_split_adjusted": [1_000.0]}, index=[0]
        )
        assert market_capitalisation(panel).iloc[0] == pytest.approx(10_000.0)

    def test_a_missing_share_count_gives_no_capitalisation(self) -> None:
        panel = pd.DataFrame(
            {"price": [10.0], "shares_split_adjusted": [np.nan]}, index=[0]
        )
        assert bool(np.isnan(market_capitalisation(panel).iloc[0]))

    def test_a_non_positive_capitalisation_is_refused(self) -> None:
        panel = pd.DataFrame(
            {"price": [10.0], "shares_split_adjusted": [0.0]}, index=[0]
        )
        assert bool(np.isnan(market_capitalisation(panel).iloc[0]))


class TestEnterpriseValue:
    def test_it_adds_debt_and_subtracts_cash(self) -> None:
        panel = pd.DataFrame(
            {
                "price": [10.0],
                "shares_split_adjusted": [1_000.0],
                "total_debt": [3_000.0],
                "cash_and_equivalents": [1_000.0],
            },
            index=[0],
        )
        assert enterprise_value(panel).iloc[0] == pytest.approx(12_000.0)

    def test_missing_debt_and_cash_are_treated_as_zero(self) -> None:
        panel = pd.DataFrame(
            {
                "price": [10.0],
                "shares_split_adjusted": [1_000.0],
                "total_debt": [np.nan],
                "cash_and_equivalents": [np.nan],
            },
            index=[0],
        )
        assert enterprise_value(panel).iloc[0] == pytest.approx(10_000.0)

    def test_a_company_with_more_cash_than_market_value_is_refused(self) -> None:
        # EBIT over a near-zero or negative enterprise value is either a
        # nonsense sign or an enormous number, and either one sweeps the
        # top of a value ranking on arithmetic rather than cheapness.
        panel = pd.DataFrame(
            {
                "price": [10.0],
                "shares_split_adjusted": [1_000.0],
                "total_debt": [0.0],
                "cash_and_equivalents": [9_900.0],
            },
            index=[0],
        )
        assert bool(np.isnan(enterprise_value(panel).iloc[0]))

    def test_the_floor_is_a_fraction_of_market_cap(self) -> None:
        cap = 10_000.0
        just_above = cap * MIN_EV_TO_MARKET_CAP * 1.1
        panel = pd.DataFrame(
            {
                "price": [10.0],
                "shares_split_adjusted": [1_000.0],
                "total_debt": [0.0],
                "cash_and_equivalents": [cap - just_above],
            },
            index=[0],
        )
        assert enterprise_value(panel).iloc[0] == pytest.approx(just_above)


class TestTheJoinIsBackwardsOnly:
    def test_a_rebalance_sees_the_filing_before_it(self) -> None:
        prices = _prices()
        fundamentals = _fundamentals(
            [
                ("2020-01-31", "AAA", {"operating_income": 100.0, "total_assets": 1_000.0,
                                        "shares_split_adjusted": 1_000.0,
                                        "total_debt": 0.0, "cash_and_equivalents": 0.0}),
            ]
        )
        merged = add_fundamental_factors(prices, fundamentals)
        assert merged.loc[(MONTHS[0], "AAA"), "total_assets"] == pytest.approx(1_000.0)

    def test_a_rebalance_never_sees_a_later_filing(self) -> None:
        # The whole point. March's numbers must not reach February.
        prices = _prices()
        fundamentals = _fundamentals(
            [
                ("2020-03-31", "AAA", {"operating_income": 100.0, "total_assets": 1_000.0,
                                        "shares_split_adjusted": 1_000.0,
                                        "total_debt": 0.0, "cash_and_equivalents": 0.0}),
            ]
        )
        merged = add_fundamental_factors(prices, fundamentals)
        assert bool(np.isnan(merged.loc[(MONTHS[0], "AAA"), "total_assets"]))
        assert bool(np.isnan(merged.loc[(MONTHS[1], "AAA"), "total_assets"]))
        assert merged.loc[(MONTHS[2], "AAA"), "total_assets"] == pytest.approx(1_000.0)

    def test_the_last_filing_is_carried_forward(self) -> None:
        # A company files quarterly; the months in between hold the same
        # numbers rather than dropping out of the universe.
        prices = _prices()
        fundamentals = _fundamentals(
            [
                ("2020-01-31", "AAA", {"operating_income": 100.0, "total_assets": 1_000.0,
                                        "shares_split_adjusted": 1_000.0,
                                        "total_debt": 0.0, "cash_and_equivalents": 0.0}),
            ]
        )
        merged = add_fundamental_factors(prices, fundamentals)
        assert merged.loc[(MONTHS[3], "AAA"), "total_assets"] == pytest.approx(1_000.0)

    def test_a_new_filing_replaces_the_carried_one(self) -> None:
        prices = _prices()
        fundamentals = _fundamentals(
            [
                ("2020-01-31", "AAA", {"operating_income": 100.0, "total_assets": 1_000.0,
                                        "shares_split_adjusted": 1_000.0,
                                        "total_debt": 0.0, "cash_and_equivalents": 0.0}),
                ("2020-03-31", "AAA", {"operating_income": 200.0, "total_assets": 2_000.0,
                                        "shares_split_adjusted": 1_000.0,
                                        "total_debt": 0.0, "cash_and_equivalents": 0.0}),
            ]
        )
        merged = add_fundamental_factors(prices, fundamentals)
        assert merged.loc[(MONTHS[1], "AAA"), "total_assets"] == pytest.approx(1_000.0)
        assert merged.loc[(MONTHS[3], "AAA"), "total_assets"] == pytest.approx(2_000.0)

    def test_one_tickers_filings_never_reach_another(self) -> None:
        prices = _prices(("AAA", "BBB"))
        fundamentals = _fundamentals(
            [
                ("2020-01-31", "AAA", {"operating_income": 100.0, "total_assets": 1_000.0,
                                        "shares_split_adjusted": 1_000.0,
                                        "total_debt": 0.0, "cash_and_equivalents": 0.0}),
            ]
        )
        merged = add_fundamental_factors(prices, fundamentals)
        assert bool(np.isnan(merged.loc[(MONTHS[0], "BBB"), "total_assets"]))


class TestDerivedRatios:
    @staticmethod
    def _merged() -> pd.DataFrame:
        prices = _prices()
        fundamentals = _fundamentals(
            [
                (
                    "2020-01-31",
                    "AAA",
                    {
                        "operating_income": 1_000.0,
                        "total_assets": 5_000.0,
                        "shares_split_adjusted": 1_000.0,
                        "total_debt": 2_000.0,
                        "cash_and_equivalents": 1_000.0,
                    },
                ),
            ]
        )
        return add_fundamental_factors(prices, fundamentals)

    def test_earnings_yield_is_ebit_over_enterprise_value(self) -> None:
        # cap 10,000 + debt 2,000 - cash 1,000 = 11,000; 1,000/11,000.
        merged = self._merged()
        assert merged.loc[(MONTHS[0], "AAA"), "earnings_yield"] == pytest.approx(
            1_000.0 / 11_000.0 * 100.0
        )

    def test_operating_profitability_is_ebit_over_assets(self) -> None:
        merged = self._merged()
        assert merged.loc[(MONTHS[0], "AAA"), "op_profitability"] == pytest.approx(20.0)

    def test_price_features_survive_a_missing_filing(self) -> None:
        # A design with only price legs must keep working for names the
        # fundamentals panel never resolved.
        prices = _prices(("AAA", "BBB"))
        fundamentals = _fundamentals(
            [
                ("2020-01-31", "AAA", {"operating_income": 100.0, "total_assets": 1_000.0,
                                        "shares_split_adjusted": 1_000.0,
                                        "total_debt": 0.0, "cash_and_equivalents": 0.0}),
            ]
        )
        merged = add_fundamental_factors(prices, fundamentals)
        assert merged.loc[(MONTHS[0], "BBB"), "mom_12_1"] == pytest.approx(0.1)

    def test_an_absurd_earnings_yield_is_dropped(self) -> None:
        # EBIT the size of the whole enterprise means one of the two
        # numbers is a parse artefact, not that the company is cheap.
        prices = _prices()
        fundamentals = _fundamentals(
            [
                (
                    "2020-01-31",
                    "AAA",
                    {
                        "operating_income": 1_000_000.0,
                        "total_assets": 5_000.0,
                        "shares_split_adjusted": 1_000.0,
                        "total_debt": 0.0,
                        "cash_and_equivalents": 0.0,
                    },
                ),
            ]
        )
        merged = add_fundamental_factors(prices, fundamentals)
        assert bool(np.isnan(merged.loc[(MONTHS[0], "AAA"), "earnings_yield"]))


class TestEmptyInput:
    def test_no_fundamentals_returns_the_price_panel_unchanged(self) -> None:
        prices = _prices()
        merged = add_fundamental_factors(prices, pd.DataFrame())
        assert list(merged.columns) == list(prices.columns)
        assert len(merged) == len(prices)


class TestStaleFilingsAreDropped:
    """The bug this guards against did not raise, warn, or look wrong.

    A holdout run over 2019-2026 was scored against a fundamentals panel
    built only through 2018. Every filing was carried forward for seven
    years, so "earnings yield" became 2018 EBIT over today's enterprise
    value — a long-horizon reversal signal wearing a value label — and
    the value designs came back at 22.81% and 23.88%. Nothing in the
    output distinguished those numbers from real ones.
    """

    @staticmethod
    def _panel(rebalances: list[str], filing: str = "2020-01-31") -> pd.DataFrame:
        index = pd.MultiIndex.from_product(
            [pd.to_datetime(rebalances), ["AAA"]], names=["date", "ticker"]
        )
        prices = pd.DataFrame({"price": 10.0, "mom_12_1": 0.1}, index=index)
        fundamentals = _fundamentals(
            [
                (
                    filing,
                    "AAA",
                    {
                        "operating_income": 100.0,
                        "total_assets": 1_000.0,
                        "shares_split_adjusted": 1_000.0,
                        "total_debt": 0.0,
                        "cash_and_equivalents": 0.0,
                    },
                )
            ]
        )
        return add_fundamental_factors(prices, fundamentals)

    def test_a_filing_from_last_quarter_is_used(self) -> None:
        merged = self._panel(["2020-04-30"])
        assert merged.loc[(pd.Timestamp("2020-04-30"), "AAA"), "total_assets"] == (
            pytest.approx(1_000.0)
        )

    def test_a_filing_from_a_year_ago_is_still_used(self) -> None:
        # Companies are late, and a screen that drops anyone who missed
        # a quarter is a screen that selects on punctuality.
        merged = self._panel(["2021-01-29"])
        assert merged.loc[(pd.Timestamp("2021-01-29"), "AAA"), "total_assets"] == (
            pytest.approx(1_000.0)
        )

    def test_a_filing_from_two_years_ago_is_dropped(self) -> None:
        merged = self._panel(["2022-01-31"])
        assert bool(
            np.isnan(merged.loc[(pd.Timestamp("2022-01-31"), "AAA"), "total_assets"])
        )

    def test_a_filing_from_seven_years_ago_is_dropped(self) -> None:
        # The exact shape of the bug.
        merged = self._panel(["2026-06-30"], filing="2019-01-31")
        assert bool(
            np.isnan(merged.loc[(pd.Timestamp("2026-06-30"), "AAA"), "total_assets"])
        )
        assert bool(
            np.isnan(merged.loc[(pd.Timestamp("2026-06-30"), "AAA"), "earnings_yield"])
        )

    def test_the_usable_stretch_survives_and_the_stale_one_does_not(self) -> None:
        merged = self._panel(["2020-04-30", "2020-10-30", "2023-04-28"])
        assert merged.loc[(pd.Timestamp("2020-04-30"), "AAA"), "total_assets"] == (
            pytest.approx(1_000.0)
        )
        assert merged.loc[(pd.Timestamp("2020-10-30"), "AAA"), "total_assets"] == (
            pytest.approx(1_000.0)
        )
        assert bool(
            np.isnan(merged.loc[(pd.Timestamp("2023-04-28"), "AAA"), "total_assets"])
        )
