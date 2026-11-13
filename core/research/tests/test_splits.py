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
    share_count_by_filing,
    split_adjusted_share_count,
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


# ---------------------------------------------------------------------------
# Audit FUND-8: the detector reading a company's own facts.
#
# The rows below are real, copied from the local EDGAR parquet cache
# (``fundamentals_cache/{AOS,FOXA,RYN,NVDA}.parquet``) and trimmed to the
# filings that decide each case.
# ---------------------------------------------------------------------------

DEI = "EntityCommonStockSharesOutstanding"
CSO = "CommonStockSharesOutstanding"
WAB = "WeightedAverageNumberOfSharesOutstandingBasic"

#: (concept, filed, period_start, period_end, value)
Fact = tuple[str, str, str | None, str, float]


def _facts(rows: list[Fact]) -> pd.DataFrame:
    """A facts frame shaped like ``EdgarCache.load_dataframe``'s."""
    frame = pd.DataFrame(
        rows, columns=["concept", "filed", "period_start", "period_end", "value"]
    )
    for col in ("filed", "period_start", "period_end"):
        frame[col] = pd.to_datetime(frame[col])
    return frame


#: FOXA: one cover-page row, value 1, from a 2019 10-Q; then quarterly
#: weighted averages in the hundreds of millions.
FOXA_DEI: list[Fact] = [(DEI, "2019-03-18", None, "2019-03-18", 1.0)]
FOXA_WA: list[Fact] = [
    (WAB, "2019-05-10", "2018-07-01", "2019-03-31", 621_000_000.0),
    (WAB, "2019-05-10", "2019-01-01", "2019-03-31", 621_000_000.0),
    (WAB, "2019-11-06", "2019-07-01", "2019-09-30", 622_000_000.0),
    (WAB, "2025-10-30", "2025-07-01", "2025-09-30", 447_000_000.0),
    (WAB, "2026-02-04", "2025-10-01", "2025-12-31", 433_000_000.0),
]

#: AOS: the cover series stops at the 2015-08-10 10-Q; the 2-for-1 of
#: October 2016 shows up only in the weighted averages, and the
#: 2016-11-08 10-Q restates the 2015 third quarter from 88.7M to 177.4M.
AOS_DEI: list[Fact] = [
    (DEI, "2014-11-03", None, "2014-10-28", 76_382_084.0),
    (DEI, "2015-05-05", None, "2015-04-30", 76_047_352.0),
    (DEI, "2015-08-10", None, "2015-08-05", 75_732_188.0),
]
AOS_WA: list[Fact] = [
    (WAB, "2015-08-10", "2015-04-01", "2015-06-30", 89_139_114.0),
    (WAB, "2015-11-09", "2015-01-01", "2015-09-30", 89_088_565.0),
    (WAB, "2015-11-09", "2015-07-01", "2015-09-30", 88_698_196.0),
    (WAB, "2016-02-17", "2015-01-01", "2015-12-31", 88_811_140.0),
    (WAB, "2016-08-08", "2016-04-01", "2016-06-30", 87_480_860.0),
    (WAB, "2016-11-08", "2015-07-01", "2015-09-30", 177_396_392.0),
    (WAB, "2016-11-08", "2016-07-01", "2016-09-30", 174_409_789.0),
    (WAB, "2017-02-17", "2016-01-01", "2016-12-31", 174_712_683.0),
    (WAB, "2025-10-28", "2025-07-01", "2025-09-30", 139_953_331.0),
    (WAB, "2026-02-10", "2025-01-01", "2025-12-31", 141_029_845.0),
]

#: RYN: the 2026-02-23 10-K's cover count doubles (the PotlatchDeltic
#: merger closed in stock), while the same 10-K reprints its 2023 and
#: 2024 weighted averages exactly as the earlier filings had them. A
#: split would have restated them two-for-one.
RYN_FACTS: list[Fact] = [
    (DEI, "2025-08-08", None, "2025-08-01", 154_162_341.0),
    (DEI, "2025-11-07", None, "2025-10-31", 153_899_831.0),
    (DEI, "2026-02-23", None, "2026-02-13", 302_298_328.0),
    (WAB, "2025-02-21", "2023-01-01", "2023-12-31", 148_046_673.0),
    (WAB, "2025-02-21", "2024-01-01", "2024-12-31", 148_839_858.0),
    (WAB, "2025-11-07", "2025-07-01", "2025-09-30", 154_306_240.0),
    (WAB, "2026-02-23", "2023-01-01", "2023-12-31", 148_046_673.0),
    (WAB, "2026-02-23", "2024-01-01", "2024-12-31", 148_839_858.0),
    (WAB, "2026-02-23", "2025-01-01", "2025-12-31", 154_760_442.0),
]

#: NVDA: the 10-for-1 of June 2024. The 2024-08-28 10-Q restates the
#: July 2023 quarter from 2.473bn to 24.729bn — the filer confirming
#: the split in its own comparatives.
NVDA_FACTS: list[Fact] = [
    (DEI, "2023-08-28", None, "2023-08-18", 2_470_000_000.0),
    (DEI, "2024-05-29", None, "2024-05-17", 2_460_000_000.0),
    (DEI, "2024-08-28", None, "2024-08-16", 24_530_000_000.0),
    (WAB, "2023-08-28", "2023-05-01", "2023-07-30", 2_473_000_000.0),
    (WAB, "2024-08-28", "2023-05-01", "2023-07-30", 24_729_000_000.0),
    (WAB, "2024-08-28", "2024-04-29", "2024-07-28", 24_578_000_000.0),
]


class TestSanityFloor:
    def test_foxa_single_dei_row_of_one_yields_none(self) -> None:
        # A company with one share is a tagging error, not a company.
        # With nothing else on file there is no count at all, and a
        # caller must read that as "cannot tell".
        assert share_count_by_filing(_facts(FOXA_DEI)).empty
        assert split_adjusted_share_count(_facts(FOXA_DEI)).empty

    def test_foxa_reads_its_weighted_averages_once_the_one_is_dropped(self) -> None:
        series = split_adjusted_share_count(_facts(FOXA_DEI + FOXA_WA))
        assert date(2019, 3, 18) not in series.index
        assert series.iloc[-1] == pytest.approx(433_000_000.0)
        assert series.min() >= 400_000_000.0

    def test_a_balance_sheet_count_below_the_floor_is_dropped_too(self) -> None:
        rows: list[Fact] = [
            (CSO, "2020-02-01", None, "2019-12-31", 500_000_000.0),
            (CSO, "2020-05-01", None, "2020-03-31", 512.0),
            (CSO, "2020-08-01", None, "2020-06-30", 498_000_000.0),
        ]
        series = share_count_by_filing(_facts(rows))
        assert date(2020, 5, 1) not in series.index
        # Without the floor, 500M -> 512 -> 498M was a 1-for-a-million
        # reverse split followed by a million-for-one forward one.
        assert detect_splits(series) == []


class TestWeightedAverageFallback:
    def test_aos_falls_back_to_weighted_average_when_dei_stale(self) -> None:
        series = split_adjusted_share_count(_facts(AOS_DEI + AOS_WA))
        # Today's count is the weighted average, not the 2015 cover
        # count of 75.7M the audit found being served.
        assert series.index[-1] == date(2026, 2, 10)
        assert series.iloc[-1] == pytest.approx(141_029_845.0)
        # And the 2016 two-for-one, invisible to the cover series, is
        # found and pushed back through the earlier history.
        assert series.loc[date(2015, 8, 10)] == pytest.approx(2 * 75_732_188.0)
        assert series.loc[date(2016, 8, 8)] == pytest.approx(2 * 87_480_860.0)

    def test_a_fresh_cover_series_is_not_replaced(self) -> None:
        # RYN's cover series runs to the newest filing; its weighted
        # averages must not leak in between cover counts.
        series = share_count_by_filing(_facts(RYN_FACTS))
        assert date(2025, 2, 21) not in series.index
        assert series.loc[date(2025, 11, 7)] == pytest.approx(153_899_831.0)

    def test_a_recast_filing_does_not_step_backwards_in_time(self) -> None:
        # An 8-K recasting old annual figures, filed after a newer 10-Q,
        # carries a period_end older than one already used. Taking it
        # would read a count that was stale when it was filed.
        rows = [
            *FOXA_WA,
            (WAB, "2025-11-21", "2018-07-01", "2019-03-31", 300_000_000.0),
        ]
        series = share_count_by_filing(_facts(rows))
        assert date(2025, 11, 21) not in series.index


class TestFilerRestatement:
    def test_ryn_2026_02_23_issuance_is_not_a_split(self) -> None:
        series = split_adjusted_share_count(_facts(RYN_FACTS))
        # No factor of two pushed back: the November count stays what
        # was filed, and the post-merger count is today's.
        assert series.loc[date(2025, 11, 7)] == pytest.approx(153_899_831.0)
        assert series.loc[date(2026, 2, 23)] == pytest.approx(302_298_328.0)

    def test_nvda_2024_split_is_confirmed_by_its_restated_comparatives(
        self,
    ) -> None:
        series = split_adjusted_share_count(_facts(NVDA_FACTS))
        assert series.loc[date(2024, 5, 29)] == pytest.approx(24_600_000_000.0)
        assert series.loc[date(2023, 8, 28)] == pytest.approx(24_700_000_000.0)

    def test_a_jump_with_no_comparatives_on_file_is_still_a_split(self) -> None:
        # Absence of evidence is not a contradiction: GOOGL's 20-for-1
        # has no reprinted comparatives in the cache and is real.
        rows: list[Fact] = [
            (DEI, "2022-04-27", None, "2022-04-20", 660_000_000.0),
            (DEI, "2022-07-27", None, "2022-07-20", 13_200_000_000.0),
        ]
        series = split_adjusted_share_count(_facts(rows))
        assert series.loc[date(2022, 4, 27)] == pytest.approx(13_200_000_000.0)

    def test_the_filing_that_shows_the_jump_is_the_witness(self) -> None:
        # CF's 5-for-1 of June 2015. Its 2015-08-06 10-Q restates the
        # 2014 first half x5; a December 8-K then reprints the old annual
        # figures in pre-split units and a stale 48M cover count. Pooling
        # the 8-K's reprints with the 10-Q's outvoted the real split.
        rows: list[Fact] = [
            (DEI, "2015-05-07", None, "2015-04-30", 47_069_448.0),
            (DEI, "2015-08-06", None, "2015-07-31", 233_047_785.0),
            (DEI, "2015-11-09", None, "2015-10-31", 233_075_336.0),
            (WAB, "2014-08-07", "2014-01-01", "2014-06-30", 53_000_000.0),
            (WAB, "2015-02-26", "2012-01-01", "2012-12-31", 63_900_000.0),
            (WAB, "2015-02-26", "2013-01-01", "2013-12-31", 58_900_000.0),
            (WAB, "2015-02-26", "2014-01-01", "2014-12-31", 51_200_000.0),
            (WAB, "2015-08-06", "2014-01-01", "2014-06-30", 264_500_000.0),
            (WAB, "2015-12-16", "2012-01-01", "2012-12-31", 63_900_000.0),
            (WAB, "2015-12-16", "2013-01-01", "2013-12-31", 58_900_000.0),
            (WAB, "2015-12-16", "2014-01-01", "2014-12-31", 51_200_000.0),
        ]
        series = split_adjusted_share_count(_facts(rows))
        assert series.loc[date(2015, 5, 7)] == pytest.approx(5 * 47_069_448.0)
        assert series.loc[date(2015, 8, 6)] == pytest.approx(233_047_785.0)

    def test_a_unit_glitch_that_reverts_is_still_rescaled(self) -> None:
        # RMD tagged two cover counts in thousands (145,681 for 145.7M).
        # They are not splits, and the filer's reprints say so; but the
        # rows themselves are a thousand times too small, and the pair of
        # opposite jumps around them is what puts them back in units.
        rows: list[Fact] = [
            (DEI, "2021-04-29", None, "2021-04-20", 145_400_000.0),
            (DEI, "2021-08-17", None, "2021-08-10", 145_681.0),
            (DEI, "2021-08-19", None, "2021-08-10", 145_681.0),
            (DEI, "2021-10-29", None, "2021-10-20", 145_723_142.0),
            (WAB, "2021-04-29", "2020-01-01", "2020-03-31", 144_900_000.0),
            (WAB, "2021-08-17", "2020-01-01", "2020-03-31", 144_900_000.0),
            (WAB, "2021-10-29", "2020-01-01", "2020-03-31", 144_900_000.0),
        ]
        series = split_adjusted_share_count(_facts(rows))
        assert series.loc[date(2021, 8, 17)] == pytest.approx(145_723_142.0, rel=1e-3)
        # Before the run the two raw ratios multiply to 1.0022, not 1: the
        # neighbours really did differ by 0.2%, a rounding the pair keeps.
        assert series.loc[date(2021, 4, 29)] == pytest.approx(145_400_000.0, rel=5e-3)
        assert series.loc[date(2021, 10, 29)] == pytest.approx(145_723_142.0)

    def test_an_empty_frame_has_no_counts(self) -> None:
        assert split_adjusted_share_count(pd.DataFrame()).empty
