"""Tests for the fast variant scorer.

Every design decision about the eleventh agent is made by reading
numbers out of this module, so a mistake here does not produce a wrong
answer — it produces a confident wrong answer, chosen over the right
one and then carried into a sealed holdout run that cannot be repeated.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.research.evaluate import (
    Design,
    Leg,
    build_weights,
    composite_score,
    evaluate,
    percentile_ranks,
    period_returns,
    summarize,
)

DATES = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"])


def _panel(rows: dict[tuple[str, str], dict[str, float]]) -> pd.DataFrame:
    """Build a panel from ``{(date, ticker): {column: value}}``."""
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(d), t) for d, t in rows], names=["date", "ticker"]
    )
    return pd.DataFrame(list(rows.values()), index=index).sort_index()


def _simple_panel(n_tickers: int = 6) -> pd.DataFrame:
    rows = {}
    for d in ("2020-01-31", "2020-02-29", "2020-03-31"):
        for i in range(n_tickers):
            rows[(d, f"T{i}")] = {
                "signal": float(i),
                "ivol_6m": 0.2 + 0.1 * i,
                "fwd_next": 0.01 * (i - 2),
            }
    return _panel(rows)


class TestPercentileRanks:
    def test_ranks_land_between_zero_and_one(self) -> None:
        r = percentile_ranks(pd.Series([1.0, 2.0, 3.0, 4.0]))
        assert r.min() > 0.0
        assert r.max() == pytest.approx(1.0)

    def test_ties_share_a_position(self) -> None:
        r = percentile_ranks(pd.Series([5.0, 5.0, 9.0]))
        assert r.iloc[0] == pytest.approx(r.iloc[1])

    def test_missing_values_stay_missing(self) -> None:
        # A NaN rank has to propagate so the row drops out of the
        # composite. Ranking it as zero would read "worst in the market"
        # for a company that simply has not filed.
        r = percentile_ranks(pd.Series([1.0, np.nan, 3.0]))
        assert bool(np.isnan(r.iloc[1]))


class TestCompositeScore:
    def test_direction_is_respected(self) -> None:
        frame = pd.DataFrame({"vol": [0.1, 0.5]}, index=["LOW", "HIGH"])
        lower_better = composite_score(frame, (Leg("vol", higher_is_better=False),))
        assert lower_better["LOW"] > lower_better["HIGH"]

        higher_better = composite_score(frame, (Leg("vol", higher_is_better=True),))
        assert higher_better["HIGH"] > higher_better["LOW"]

    def test_a_row_missing_one_leg_scores_nan(self) -> None:
        # Averaging the legs that happen to be present would rank a
        # company on whichever measurements it chose to disclose.
        frame = pd.DataFrame(
            {"a": [1.0, 2.0], "b": [1.0, np.nan]}, index=["FULL", "PARTIAL"]
        )
        score = composite_score(frame, (Leg("a"), Leg("b")))
        assert bool(np.isnan(score["PARTIAL"]))
        assert not bool(np.isnan(score["FULL"]))

    def test_leg_weights_shift_the_ordering(self) -> None:
        frame = pd.DataFrame(
            {"a": [1.0, 2.0], "b": [2.0, 1.0]}, index=["X", "Y"]
        )
        assert composite_score(frame, (Leg("a", 9.0), Leg("b", 1.0)))["Y"] > 0.5
        assert composite_score(frame, (Leg("a", 1.0), Leg("b", 9.0)))["X"] > 0.5


class TestBuildWeights:
    def test_it_holds_exactly_the_portfolio_size(self) -> None:
        w = build_weights(_simple_panel(6), Design(name="d", legs=(Leg("signal"),), portfolio_size=3))
        for _, group in w.groupby(level="date"):
            assert len(group) == 3

    def test_weights_sum_to_one_per_date(self) -> None:
        w = build_weights(_simple_panel(6), Design(name="d", legs=(Leg("signal"),), portfolio_size=4))
        for _, group in w.groupby(level="date"):
            assert float(group["weight"].sum()) == pytest.approx(1.0)

    def test_a_thin_cross_section_holds_what_it_has(self) -> None:
        w = build_weights(_simple_panel(2), Design(name="d", legs=(Leg("signal"),), portfolio_size=25))
        for _, group in w.groupby(level="date"):
            assert len(group) == 2
            assert float(group["weight"].sum()) == pytest.approx(1.0)

    def test_it_picks_the_top_of_the_ranking(self) -> None:
        w = build_weights(_simple_panel(6), Design(name="d", legs=(Leg("signal"),), portfolio_size=2))
        first = w.loc[pd.Timestamp("2020-01-31")]
        assert set(first.index) == {"T4", "T5"}

    def test_inverse_vol_gives_the_quieter_name_more_room(self) -> None:
        design = Design(
            name="d", legs=(Leg("signal"),), portfolio_size=2, weighting="inverse_vol"
        )
        w = build_weights(_simple_panel(6), design)
        first = w.loc[pd.Timestamp("2020-01-31")]
        # T4 has the lower ivol of the two picked, so it takes more.
        assert first.loc["T4", "weight"] > first.loc["T5", "weight"]

    def test_exposure_scales_the_whole_book(self) -> None:
        exposure = pd.Series(0.25, index=DATES)
        design = Design(
            name="d", legs=(Leg("signal"),), portfolio_size=2, exposure=exposure
        )
        w = build_weights(_simple_panel(6), design)
        for _, group in w.groupby(level="date"):
            assert float(group["weight"].sum()) == pytest.approx(0.25)


class TestPeriodReturns:
    def test_the_opening_book_is_charged_in_full(self) -> None:
        design = Design(name="d", legs=(Leg("signal"),), portfolio_size=2, cost_bps=10.0)
        panel = _simple_panel(6)
        periods = period_returns(panel, build_weights(panel, design), design)
        assert periods["turnover"].iloc[0] == pytest.approx(1.0)
        assert periods["cost"].iloc[0] == pytest.approx(0.001)

    def test_an_unchanged_book_costs_nothing_after_the_first_period(self) -> None:
        design = Design(name="d", legs=(Leg("signal"),), portfolio_size=2)
        panel = _simple_panel(6)
        periods = period_returns(panel, build_weights(panel, design), design)
        assert periods["turnover"].iloc[1] == pytest.approx(0.0)

    def test_net_is_gross_less_cost(self) -> None:
        design = Design(name="d", legs=(Leg("signal"),), portfolio_size=2)
        panel = _simple_panel(6)
        periods = period_returns(panel, build_weights(panel, design), design)
        assert periods["net"].equals(periods["gross"] - periods["cost"])

    def test_gross_is_the_weighted_average_of_holdings(self) -> None:
        design = Design(name="d", legs=(Leg("signal"),), portfolio_size=2)
        panel = _simple_panel(6)
        periods = period_returns(panel, build_weights(panel, design), design)
        # T4 returns +2%, T5 returns +3%, equally weighted.
        assert periods["gross"].iloc[0] == pytest.approx(0.025)


class TestSummarize:
    def test_a_flat_strategy_has_zero_cagr(self) -> None:
        flat = pd.Series([0.0] * 24, index=pd.date_range("2020-01-31", periods=24, freq="ME"))
        s = summarize(flat, flat, name="flat")
        assert s.cagr_pct == pytest.approx(0.0, abs=1e-9)
        assert s.max_drawdown_pct == pytest.approx(0.0)

    def test_a_steady_one_percent_a_month_compounds(self) -> None:
        r = pd.Series([0.01] * 12, index=pd.date_range("2020-01-31", periods=12, freq="ME"))
        s = summarize(r, pd.Series(0.0, index=r.index), name="steady")
        assert s.cagr_pct == pytest.approx(12.68, abs=0.05)

    def test_drawdown_is_measured_from_the_peak(self) -> None:
        r = pd.Series(
            [0.5, -0.5, 0.0], index=pd.date_range("2020-01-31", periods=3, freq="ME")
        )
        s = summarize(r, pd.Series(0.0, index=r.index), name="dd")
        assert s.max_drawdown_pct == pytest.approx(-50.0)

    def test_matching_the_benchmark_gives_no_alpha_and_no_t(self) -> None:
        r = pd.Series(
            [0.02, -0.01, 0.03] * 4,
            index=pd.date_range("2020-01-31", periods=12, freq="ME"),
        )
        s = summarize(r, r.copy(), name="clone")
        assert s.alpha_pct == pytest.approx(0.0, abs=1e-9)
        assert s.t_stat == pytest.approx(0.0)

    def test_a_single_period_is_refused(self) -> None:
        one = pd.Series([0.01], index=pd.date_range("2020-01-31", periods=1, freq="ME"))
        with pytest.raises(ValueError):
            summarize(one, one, name="too short")


class TestDesignValidation:
    def test_a_design_needs_a_leg(self) -> None:
        with pytest.raises(ValueError):
            Design(name="empty", legs=())

    def test_a_zero_portfolio_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Design(name="d", legs=(Leg("signal"),), portfolio_size=0)

    def test_a_non_positive_leg_weight_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Design(name="d", legs=(Leg("signal", weight=0.0),))


class TestEvaluateEndToEnd:
    def test_it_returns_a_summary_and_the_period_detail(self) -> None:
        panel = _simple_panel(6)
        bench = pd.Series(0.0, index=DATES)
        design = Design(name="e2e", legs=(Leg("signal"),), portfolio_size=3)
        summary, periods = evaluate(panel, design, bench)
        assert summary.name == "e2e"
        assert summary.periods == len(periods["net"].dropna())
        assert summary.turnover_per_period > 0.0
