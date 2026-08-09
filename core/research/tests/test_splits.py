"""Tests for restating share counts in the price series' units.

The failure this guards against is silent and directional: a market cap
that is wrong by the split factor is too small for companies that split
forward and too large for companies that split backward. Since winners
split forward and failures split backward, a size screen built on the
raw product excludes the winners and admits the failures, and it looks
perfectly reasonable while doing it.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from core.research.splits import (
    adjusted_shares,
    cumulative_factors,
    detect_splits,
)


def _series(pairs: list[tuple[str, float]]) -> pd.Series:
    """A share-count series indexed by filing date, as filed."""
    return pd.Series(
        [v for _, v in pairs], index=[d for d, _ in pairs], dtype="float64"
    )


class TestDetectSplits:
    def test_a_steady_share_count_has_no_splits(self) -> None:
        s = _series(
            [("2020-01-31", 1_000.0), ("2020-04-30", 1_000.0), ("2020-07-31", 1_000.0)]
        )
        assert detect_splits(s) == []

    def test_buybacks_are_not_splits(self) -> None:
        # A company retiring 3% a quarter is doing something ordinary.
        s = _series(
            [("2020-01-31", 1_000.0), ("2020-04-30", 970.0), ("2020-07-31", 941.0)]
        )
        assert detect_splits(s) == []

    def test_a_large_secondary_offering_is_not_a_split(self) -> None:
        # 30% dilution in one quarter is dramatic but real, and it sits
        # below the 1.4x threshold on purpose.
        s = _series([("2020-01-31", 1_000.0), ("2020-04-30", 1_300.0)])
        assert detect_splits(s) == []

    def test_a_forward_split_is_found(self) -> None:
        s = _series([("2020-01-31", 100.0), ("2020-04-30", 1_000.0)])
        events = detect_splits(s)
        assert len(events) == 1
        assert events[0].ratio == pytest.approx(10.0)
        assert events[0].observed_at == date(2020, 4, 30)

    def test_a_reverse_split_is_found(self) -> None:
        s = _series([("2020-01-31", 1_000.0), ("2020-04-30", 100.0)])
        events = detect_splits(s)
        assert len(events) == 1
        assert events[0].ratio == pytest.approx(0.1)

    def test_a_messy_ratio_snaps_to_the_clean_one(self) -> None:
        # A split filed alongside a small buyback lands near, not on,
        # the round number. The reconstruction wants exactly 10.
        s = _series([("2020-01-31", 100.0), ("2020-04-30", 1_020.0)])
        assert detect_splits(s)[0].ratio == pytest.approx(10.0)

    def test_a_ratio_far_from_any_clean_one_is_kept_as_measured(self) -> None:
        s = _series([("2020-01-31", 100.0), ("2020-04-30", 1_800.0)])
        assert detect_splits(s)[0].ratio == pytest.approx(18.0)

    def test_zero_share_counts_are_dropped_not_treated_as_a_split(self) -> None:
        # A parse failure reads as zero. Dividing by it would
        # manufacture an infinite split and destroy the whole series.
        s = _series(
            [("2020-01-31", 1_000.0), ("2020-04-30", 0.0), ("2020-07-31", 1_000.0)]
        )
        assert detect_splits(s) == []

    def test_a_single_observation_has_no_splits(self) -> None:
        assert detect_splits(_series([("2020-01-31", 1_000.0)])) == []

    def test_an_empty_series_has_no_splits(self) -> None:
        assert detect_splits(pd.Series(dtype="float64")) == []


class TestCumulativeFactors:
    def test_no_splits_is_the_identity(self) -> None:
        s = _series([("2020-01-31", 1_000.0), ("2020-04-30", 990.0)])
        assert (cumulative_factors(s) == 1.0).all()

    def test_dates_before_a_split_are_scaled_and_dates_after_are_not(self) -> None:
        s = _series(
            [("2020-01-31", 100.0), ("2020-04-30", 1_000.0), ("2020-07-31", 1_000.0)]
        )
        f = cumulative_factors(s)
        assert f.loc[date(2020, 1, 31)] == pytest.approx(10.0)
        assert f.loc[date(2020, 4, 30)] == pytest.approx(1.0)
        assert f.loc[date(2020, 7, 31)] == pytest.approx(1.0)

    def test_two_splits_compound(self) -> None:
        # NVDA's shape: 4-for-1, then 10-for-1. A date before both is
        # quoted in units forty times coarser than today's.
        s = _series(
            [
                ("2020-02-20", 600.0),
                ("2021-08-20", 2_400.0),
                ("2024-08-28", 24_000.0),
            ]
        )
        f = cumulative_factors(s)
        assert f.loc[date(2020, 2, 20)] == pytest.approx(40.0)
        assert f.loc[date(2021, 8, 20)] == pytest.approx(10.0)
        assert f.loc[date(2024, 8, 28)] == pytest.approx(1.0)


class TestAdjustedShares:
    def test_the_most_recent_count_is_left_alone(self) -> None:
        # Today's filing is already in today's units; adjusting it would
        # mean the price series and the share count disagree at the one
        # date they are guaranteed to agree on.
        s = _series([("2020-01-31", 100.0), ("2024-08-28", 1_000.0)])
        assert adjusted_shares(s).iloc[-1] == pytest.approx(1_000.0)

    def test_history_is_restated_into_todays_units(self) -> None:
        s = _series([("2020-01-31", 100.0), ("2024-08-28", 1_000.0)])
        assert adjusted_shares(s).loc[date(2020, 1, 31)] == pytest.approx(1_000.0)

    def test_a_market_cap_survives_a_forward_split(self) -> None:
        # The whole point. A stock at $400 with 100 shares is a $40,000
        # company. After a 10-for-1 the price series restates that date
        # as $40, so the share count has to become 1,000 for the product
        # to still say $40,000.
        shares = _series([("2020-01-31", 100.0), ("2024-08-28", 1_000.0)])
        adjusted_price_in_2020 = 40.0
        cap = adjusted_price_in_2020 * adjusted_shares(shares).loc[date(2020, 1, 31)]
        assert cap == pytest.approx(40_000.0)

    def test_a_market_cap_survives_a_reverse_split(self) -> None:
        # The other direction, which is where a naive size floor lets in
        # companies that have already failed.
        shares = _series([("2015-01-31", 30_000.0), ("2020-01-31", 100.0)])
        adjusted_price_in_2015 = 5.0  # restated upward by the 1-for-300
        cap = adjusted_price_in_2015 * adjusted_shares(shares).loc[date(2015, 1, 31)]
        assert cap == pytest.approx(500.0)

    def test_duplicate_filing_dates_keep_the_last_value(self) -> None:
        s = pd.Series(
            [100.0, 120.0], index=["2020-01-31", "2020-01-31"], dtype="float64"
        )
        assert adjusted_shares(s).loc[date(2020, 1, 31)] == pytest.approx(120.0)

    def test_the_index_is_dates_regardless_of_what_came_in(self) -> None:
        # The share count arrives with string dates from a parquet
        # column. Leaving them as strings works by accident until a
        # caller compares against a real date, which raises.
        s = _series([("2020-01-31", 100.0), ("2024-08-28", 1_000.0)])
        assert all(isinstance(i, date) for i in adjusted_shares(s).index)
