"""Tests for the one market-capitalisation helper every screen uses.

Audit FUND-5: eleven agents computed ``price * fin.shares_outstanding``
with a split-adjusted price and a share count taken as filed — and taken
from the last 10-K even when a newer 10-Q had printed a newer cover
count. NVDA on 2024-06-05 came out at $306bn against roughly $3,000bn,
and BMNR, which issued a third of its share count in two quarters, came
out 28.6% small.

The share counts below are NVDA's and BMNR's own cover-page
(``dei:EntityCommonStockSharesOutstanding``) facts as filed, read from
the local EDGAR cache; the NVDA price is its split-adjusted close for
2024-06-05 in ``prices.sqlite``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agents.market_core.shares import market_cap_at
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact

#: NVDA's split-adjusted close on 2024-06-05 (the tape said $1,224.40;
#: the 10-for-1 of 2024-06-10 has been pushed back through history).
NVDA_PRICE_2024_06_05 = 122.44

#: NVDA's cover-page counts by filing date. The 10-for-1 shows up as the
#: jump between the May and August 2024 10-Qs.
NVDA_COVER: list[tuple[date, str, float]] = [
    (date(2023, 2, 24), "10-K", 2.470e9),
    (date(2023, 5, 26), "10-Q", 2.470e9),
    (date(2023, 8, 28), "10-Q", 2.470e9),
    (date(2023, 11, 21), "10-Q", 2.470e9),
    (date(2024, 2, 21), "10-K", 2.500e9),
    (date(2024, 5, 29), "10-Q", 2.460e9),
    (date(2024, 8, 28), "10-Q", 24.530e9),
    (date(2024, 11, 20), "10-Q", 24.490e9),
    (date(2025, 2, 26), "10-K", 24.400e9),
]

#: BMNR's cover-page counts. The July 2025 10-Q/A and the November 10-K
#: straddle an issuance the split detector reads as a split; the newest
#: count is unaffected by it, which is the property tested here.
BMNR_COVER: list[tuple[date, str, float]] = [
    (date(2025, 7, 3), "10-Q/A", 6_162_804.0),
    (date(2025, 11, 21), "10-K", 384_067_823.0),
    (date(2026, 1, 13), "10-Q", 454_862_451.0),
    (date(2026, 4, 14), "10-Q", 537_628_819.0),
]


def _cover_fact(
    filed: date, form: str, value: float, *, concept: str
) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace="dei" if concept.startswith("Entity") else "us-gaap",
        unit="shares",
        value=value,
        period_start=None,
        period_end=filed,
        filed=filed,
        form=form,
        fiscal_year=filed.year,
        fiscal_period=None,
        accession_number=f"acc-{filed.isoformat()}",
    )


def _seed(
    cache: EdgarCache,
    ticker: str,
    rows: list[tuple[date, str, float]],
    *,
    concept: str = "EntityCommonStockSharesOutstanding",
) -> None:
    cache.save_facts(
        ticker, [_cover_fact(d, f, v, concept=concept) for d, f, v in rows]
    )


@pytest.fixture
def cache(tmp_path: Path) -> EdgarCache:
    return EdgarCache(cache_dir=tmp_path)


class TestSplitUnits:
    def test_nvda_2024_06_05_market_cap_is_split_adjusted(
        self, cache: EdgarCache
    ) -> None:
        _seed(cache, "NVDA", NVDA_COVER)
        # What the PIT loader hands the agents: the last 10-K's count,
        # pre-split. The product with an adjusted price was $306bn.
        filed_shares = 2.500e9
        assert NVDA_PRICE_2024_06_05 * filed_shares == pytest.approx(306.1e9, rel=1e-3)

        mcap = market_cap_at(
            cache, "NVDA", date(2024, 6, 5), NVDA_PRICE_2024_06_05, filed_shares
        )

        assert mcap is not None
        # Newest count known that day is the May 10-Q's 2.46bn, restated
        # by the later 10-for-1: $3.01trn. NVDA was a ~$3trn company.
        assert mcap == pytest.approx(NVDA_PRICE_2024_06_05 * 24.6e9, rel=1e-9)
        assert 2.8e12 < mcap < 3.2e12

    def test_a_reverse_split_after_the_filing_shrinks_the_count(
        self, cache: EdgarCache
    ) -> None:
        # A 1-for-20 after the as_of filing: the adjusted price is twenty
        # times the tape, so the count must be divided by twenty.
        _seed(
            cache,
            "REV",
            [
                (date(2024, 1, 10), "10-Q", 40_000_000.0),
                (date(2024, 4, 10), "10-Q", 2_000_000.0),
            ],
        )
        mcap = market_cap_at(cache, "REV", date(2024, 2, 1), 100.0, 40_000_000.0)
        assert mcap == pytest.approx(100.0 * 2_000_000.0)


class TestNewestCount:
    def test_bmnr_uses_newest_cover_count(self, cache: EdgarCache) -> None:
        _seed(cache, "BMNR", BMNR_COVER)
        # The loader's prefer_annual count is the 10-K's 384M.
        mcap = market_cap_at(cache, "BMNR", date(2026, 11, 13), 30.0, 384_067_823.0)
        assert mcap == pytest.approx(30.0 * 537_628_819.0)

    def test_a_count_filed_after_as_of_is_not_used(self, cache: EdgarCache) -> None:
        _seed(cache, "BMNR", BMNR_COVER)
        mcap = market_cap_at(cache, "BMNR", date(2026, 2, 1), 30.0, 384_067_823.0)
        assert mcap == pytest.approx(30.0 * 454_862_451.0)

    def test_the_balance_sheet_count_serves_when_there_is_no_cover_fact(
        self, cache: EdgarCache
    ) -> None:
        _seed(
            cache,
            "BS",
            [(date(2025, 3, 1), "10-K", 1_000_000.0)],
            concept="CommonStockSharesOutstanding",
        )
        mcap = market_cap_at(cache, "BS", date(2025, 6, 1), 10.0, None)
        assert mcap == pytest.approx(10_000_000.0)


class TestFallback:
    def test_without_a_cache_the_filed_count_is_used(self) -> None:
        assert market_cap_at(None, "X", date(2025, 1, 2), 10.0, 5.0) == pytest.approx(50.0)

    def test_a_ticker_absent_from_the_cache_uses_the_filed_count(
        self, cache: EdgarCache
    ) -> None:
        assert market_cap_at(cache, "NONE", date(2025, 1, 2), 10.0, 5.0) == pytest.approx(50.0)

    def test_a_stale_series_is_not_preferred_over_the_filed_count(
        self, cache: EdgarCache
    ) -> None:
        # AOS's cover series stops in 2015 in the cache; an eleven-year-old
        # count is not "the newest known" in any useful sense, and the
        # loader's own age bound would not have served it either.
        _seed(cache, "OLD", [(date(2015, 8, 10), "10-Q", 75_732_188.0)])
        mcap = market_cap_at(cache, "OLD", date(2026, 11, 13), 50.0, 141_000_000.0)
        assert mcap == pytest.approx(50.0 * 141_000_000.0)


class TestUnusableInputs:
    @pytest.mark.parametrize("price", [None, 0.0, -1.0, float("nan")])
    def test_no_usable_price_means_no_market_cap(
        self, cache: EdgarCache, price: float | None
    ) -> None:
        _seed(cache, "NVDA", NVDA_COVER)
        assert market_cap_at(cache, "NVDA", date(2024, 6, 5), price, 2.5e9) is None

    @pytest.mark.parametrize("shares", [None, 0.0, -5.0])
    def test_no_usable_share_count_means_no_market_cap(
        self, cache: EdgarCache, shares: float | None
    ) -> None:
        assert market_cap_at(cache, "NONE", date(2024, 6, 5), 10.0, shares) is None


def _fact(
    concept: str, filed: date, start: date | None, end: date, value: float
) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace="dei" if concept.startswith("Entity") else "us-gaap",
        unit="shares",
        value=value,
        period_start=start,
        period_end=end,
        filed=filed,
        form="10-Q",
        fiscal_year=end.year,
        fiscal_period=None,
        accession_number=f"acc-{filed.isoformat()}",
    )


_WAB = "WeightedAverageNumberOfSharesOutstandingBasic"
_DEI = "EntityCommonStockSharesOutstanding"


class TestSplitDetectorSanity:
    """Audit FUND-8, seen through the helper the screens call.

    Rows are FOXA's, RYN's and AOS's own facts from the local EDGAR
    cache, trimmed to the filings that decide each case.
    """

    def test_foxa_cover_count_of_one_is_not_a_market_cap(
        self, cache: EdgarCache
    ) -> None:
        cache.save_facts(
            "FOXA",
            [
                _fact(_DEI, date(2019, 3, 18), None, date(2019, 3, 18), 1.0),
                _fact(
                    _WAB, date(2019, 5, 10), date(2019, 1, 1), date(2019, 3, 31),
                    621_000_000.0,
                ),
            ],
        )
        # Between the two filings the only count on file was "1"; the
        # loader's count is what is left.
        assert market_cap_at(
            cache, "FOXA", date(2019, 4, 1), 40.0, 620_000_000.0
        ) == pytest.approx(40.0 * 620_000_000.0)
        assert market_cap_at(
            cache, "FOXA", date(2019, 6, 1), 40.0, None
        ) == pytest.approx(40.0 * 621_000_000.0)

    def test_ryn_merger_issuance_does_not_double_the_old_count(
        self, cache: EdgarCache
    ) -> None:
        cache.save_facts(
            "RYN",
            [
                _fact(_DEI, date(2025, 11, 7), None, date(2025, 10, 31), 153_899_831.0),
                _fact(_DEI, date(2026, 2, 23), None, date(2026, 2, 13), 302_298_328.0),
                _fact(
                    _WAB, date(2025, 2, 21), date(2024, 1, 1), date(2024, 12, 31),
                    148_839_858.0,
                ),
                _fact(
                    _WAB, date(2026, 2, 23), date(2024, 1, 1), date(2024, 12, 31),
                    148_839_858.0,
                ),
            ],
        )
        mcap = market_cap_at(cache, "RYN", date(2025, 12, 1), 22.0, None)
        assert mcap == pytest.approx(22.0 * 153_899_831.0)

    def test_aos_newest_count_comes_from_weighted_average(
        self, cache: EdgarCache
    ) -> None:
        cache.save_facts(
            "AOS",
            [
                _fact(_DEI, date(2015, 8, 10), None, date(2015, 8, 5), 75_732_188.0),
                _fact(
                    _WAB, date(2025, 10, 28), date(2025, 7, 1), date(2025, 9, 30),
                    139_953_331.0,
                ),
                _fact(
                    _WAB, date(2026, 2, 10), date(2025, 1, 1), date(2025, 12, 31),
                    141_029_845.0,
                ),
            ],
        )
        # The loader's figure is deliberately wrong here: the helper
        # must not need it once a fresh weighted average is on file.
        mcap = market_cap_at(cache, "AOS", date(2026, 3, 2), 70.0, 1.0)
        assert mcap == pytest.approx(70.0 * 141_029_845.0)


_CSSO = "CommonStockSharesOutstanding"


class TestNewestFilingAcrossConcepts:
    """The newest count is the newest *filing's*, weighted average included.

    Review of item 08 (audit FUND-5): a multi-class filer's cover and
    balance-sheet concepts carry one class, and once its 10-K stops
    tagging them the only fresh count is the basic weighted average,
    which covers every class. Taking the cover series alone swapped
    MIR's all-class 229,959,000 for its one-class 18,750,000 and turned
    a $4.2bn company into $0.34bn, under every $500M floor. Rows are
    MIR's own facts from the local EDGAR cache, trimmed.
    """

    def test_mir_weighted_average_newer_than_the_one_class_count_wins(
        self, cache: EdgarCache
    ) -> None:
        cache.save_facts(
            "MIR",
            [
                _fact(_CSSO, date(2025, 10, 29), None, date(2025, 9, 30), 18_750_000.0),
                _fact(
                    _WAB, date(2025, 10, 29), date(2025, 7, 1), date(2025, 9, 30),
                    224_583_000.0,
                ),
                _fact(
                    _WAB, date(2026, 2, 19), date(2025, 1, 1), date(2025, 12, 31),
                    229_959_000.0,
                ),
            ],
        )
        mcap = market_cap_at(cache, "MIR", date(2026, 10, 30), 18.37, 229_959_000.0)
        assert mcap == pytest.approx(18.37 * 229_959_000.0)
        assert mcap is not None and mcap > 5e8

    def test_a_newer_weighted_average_is_restated_for_a_later_split(
        self, cache: EdgarCache
    ) -> None:
        # The 10-K carries no cover count; a 2-for-1 shows up at the next
        # 10-Q's cover. A date between the two must read the 10-K's
        # weighted average in today's (post-split) units.
        cache.save_facts(
            "TWO",
            [
                _fact(_DEI, date(2025, 5, 1), None, date(2025, 4, 25), 10_000_000.0),
                _fact(
                    _WAB, date(2025, 8, 1), date(2025, 1, 1), date(2025, 6, 30),
                    10_100_000.0,
                ),
                _fact(_DEI, date(2026, 2, 1), None, date(2026, 1, 25), 20_400_000.0),
            ],
        )
        mcap = market_cap_at(cache, "TWO", date(2025, 10, 1), 50.0, 10_100_000.0)
        assert mcap == pytest.approx(50.0 * 20_200_000.0)

    def test_in_the_same_filing_the_cover_count_beats_the_average(
        self, cache: EdgarCache
    ) -> None:
        # BMNR's April 2026 10-Q: issuance during the quarter leaves the
        # average below the count on the cover, which is the fresher one.
        cache.save_facts(
            "BMNR",
            [
                _fact(_DEI, date(2026, 4, 14), None, date(2026, 4, 10), 537_628_819.0),
                _fact(
                    _WAB, date(2026, 4, 14), date(2026, 1, 1), date(2026, 2, 28),
                    454_620_613.0,
                ),
            ],
        )
        mcap = market_cap_at(cache, "BMNR", date(2026, 11, 13), 30.0, None)
        assert mcap == pytest.approx(30.0 * 537_628_819.0)

    def test_a_stale_weighted_average_is_not_preferred_over_the_filed_count(
        self, cache: EdgarCache
    ) -> None:
        cache.save_facts(
            "OLDW",
            [
                _fact(
                    _WAB, date(2018, 3, 1), date(2017, 1, 1), date(2017, 12, 31),
                    3_000_000.0,
                ),
            ],
        )
        mcap = market_cap_at(cache, "OLDW", date(2026, 10, 30), 10.0, 7_000_000.0)
        assert mcap == pytest.approx(10.0 * 7_000_000.0)


def _filed_fact(
    concept: str,
    filed: date,
    end: date,
    value: float,
    *,
    form: str,
    unit: str = "shares",
) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace="dei" if concept.startswith("Entity") else "us-gaap",
        unit=unit,
        value=value,
        period_start=None,
        period_end=end,
        filed=filed,
        form=form,
        fiscal_year=end.year,
        fiscal_period=None,
        accession_number=f"acc-{form}-{filed.isoformat()}",
    )


class TestSameEvidenceAsTheLoader:
    """The helper reads the facts the point-in-time loader reads.

    The loader takes ``shares_outstanding`` only from 10-K and 10-Q
    filings, in ``shares``, from a period ending within
    ``MAX_FACT_AGE_DAYS``. The helper's series took every form and
    judged age by filing date, so an amendment or a registration
    statement reprinting an old count was read as the newest one. Rows
    are EONR's and VLGEA's own facts from the local EDGAR cache.
    """

    def test_an_amendment_reprinting_an_old_count_is_not_the_newest(
        self, cache: EdgarCache
    ) -> None:
        cache.save_facts(
            "EONR",
            [
                _filed_fact(
                    _DEI, date(2025, 11, 17), date(2025, 11, 12), 49_968_344.0,
                    form="10-Q",
                ),
                # A 10-K/A filed in April 2026 carrying the 2024 year-end count.
                _filed_fact(
                    _CSSO, date(2026, 4, 27), date(2024, 12, 31), 10_323_205.0,
                    form="10-K/A",
                ),
            ],
        )
        mcap = market_cap_at(cache, "EONR", date(2026, 10, 30), 0.306, 49_968_344.0)
        assert mcap == pytest.approx(0.306 * 49_968_344.0)

    def test_a_fresh_filing_of_a_stale_period_is_not_a_current_count(
        self, cache: EdgarCache
    ) -> None:
        # VLGEA's FY2025 10-K carries only the prior year-end's count
        # (one class of two). Its period is 825 days before as_of.
        cache.save_facts(
            "VLGEA",
            [
                _filed_fact(
                    _CSSO, date(2025, 10, 9), date(2024, 7, 27), 4_204_000.0,
                    form="10-K",
                ),
            ],
        )
        assert market_cap_at(cache, "VLGEA", date(2026, 10, 30), 42.77, None) is None
        assert market_cap_at(
            cache, "VLGEA", date(2026, 10, 30), 42.77, 14_500_000.0
        ) == pytest.approx(42.77 * 14_500_000.0)

    def test_a_count_in_the_wrong_unit_is_ignored(self, cache: EdgarCache) -> None:
        cache.save_facts(
            "UNIT",
            [
                _filed_fact(
                    _DEI, date(2026, 2, 1), date(2026, 1, 25), 8_000_000.0,
                    form="10-K",
                ),
                _filed_fact(
                    _DEI, date(2026, 5, 1), date(2026, 4, 25), 8_000.0,
                    form="10-Q", unit="pure",
                ),
            ],
        )
        mcap = market_cap_at(cache, "UNIT", date(2026, 6, 1), 10.0, None)
        assert mcap == pytest.approx(10.0 * 8_000_000.0)


class TestWeightedAverageUnits:
    """A weighted average is restated by real splits, not by unit repairs.

    VRNO's 2023 and 2024 10-Ks each reprint a 100,000-share balance-sheet
    count from 2020. The pair of jumps around that row brings it into
    its neighbours' units, and it must not multiply the weighted average
    the company filed in between by 3,632 (a $4.1trn VRNO on 2025-06-02).
    Rows are VRNO's own facts from the local EDGAR cache, trimmed.
    """

    def test_vrno_weighted_average_is_not_multiplied_by_a_unit_repair(
        self, cache: EdgarCache
    ) -> None:
        def wa(filed: date, start: date, end: date, value: float, form: str) -> XbrlFact:
            return XbrlFact(
                concept=_WAB,
                namespace="us-gaap",
                unit="shares",
                value=value,
                period_start=start,
                period_end=end,
                filed=filed,
                form=form,
                fiscal_year=end.year,
                fiscal_period=None,
                accession_number=f"acc-{form}-{filed.isoformat()}",
            )

        fy22 = (date(2022, 1, 1), date(2022, 12, 31))
        fy23 = (date(2023, 1, 1), date(2023, 12, 31))
        fy24 = (date(2024, 1, 1), date(2024, 12, 31))
        cache.save_facts(
            "VRNO",
            [
                _filed_fact(_DEI, date(2023, 3, 30), date(2023, 3, 28), 341_774_865.0, form="10-K"),
                _filed_fact(_CSSO, date(2023, 3, 30), date(2020, 12, 14), 100_000.0, form="10-K"),
                wa(date(2023, 3, 30), *fy22, 331_409_315.0, "10-K"),
                _filed_fact(_CSSO, date(2024, 3, 15), date(2020, 12, 14), 100_000.0, form="10-K"),
                wa(date(2024, 3, 15), *fy22, 331_409_315.0, "10-K"),
                wa(date(2024, 3, 15), *fy23, 342_774_236.0, "10-K"),
                wa(date(2025, 2, 27), *fy24, 349_584_820.0, "10-K"),
                wa(date(2025, 5, 8), date(2025, 1, 1), date(2025, 3, 31), 359_145_607.0, "10-Q"),
                _filed_fact(_DEI, date(2026, 3, 12), date(2026, 3, 10), 363_245_512.0, form="10-K"),
                wa(date(2026, 3, 12), *fy24, 349_584_820.0, "10-K"),
            ],
        )
        mcap = market_cap_at(cache, "VRNO", date(2025, 6, 2), 3.15, 359_145_607.0)
        assert mcap == pytest.approx(3.15 * 359_145_607.0)
