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
