"""Unit tests for FullMarketUniverse."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.backtest.full_market_universe import FullMarketUniverse
from core.backtest.universe_protocol import Universe
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact


def _fact(
    *,
    concept: str = "Revenues",
    namespace: str = "us-gaap",
    value: float = 1_000_000.0,
    period_end: date = date(2020, 12, 31),
    filed: date = date(2021, 2, 15),
    form: str = "10-K",
    accession_number: str = "acc-1",
) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace=namespace,
        unit="USD",
        value=value,
        period_start=date(period_end.year, 1, 1),
        period_end=period_end,
        filed=filed,
        form=form,
        fiscal_year=period_end.year,
        fiscal_period="FY",
        accession_number=accession_number,
    )


@pytest.fixture
def populated_cache(tmp_path: Path) -> EdgarCache:
    cache = EdgarCache(cache_dir=tmp_path)
    # AAPL — active operating company, multiple filings
    cache.save_facts(
        "AAPL",
        [
            _fact(filed=date(2010, 2, 15), accession_number="aapl-2010"),
            _fact(filed=date(2015, 2, 15), accession_number="aapl-2015"),
            _fact(filed=date(2024, 2, 15), accession_number="aapl-2024"),
            _fact(
                concept="Assets", filed=date(2024, 2, 15), accession_number="aapl-2024"
            ),
        ],
    )
    # OLDCO — went silent in 2014, never filed since (delisted)
    cache.save_facts(
        "OLDCO",
        [
            _fact(filed=date(2010, 2, 15), accession_number="old-2010"),
            _fact(filed=date(2014, 6, 15), accession_number="old-2014"),
        ],
    )
    # NEWCO — IPO'd in 2020
    cache.save_facts(
        "NEWCO",
        [
            _fact(
                period_end=date(2020, 12, 31),
                filed=date(2021, 3, 15),
                accession_number="new-2021",
            ),
            _fact(
                concept="Assets",
                period_end=date(2020, 12, 31),
                filed=date(2021, 3, 15),
                accession_number="new-2021",
            ),
        ],
    )
    # ETFCO — files 10-K but has no operating concepts
    cache.save_facts(
        "ETFCO",
        [
            _fact(
                concept="NetAssetValuePerShare",
                filed=date(2024, 2, 15),
                accession_number="etf-2024",
            ),
        ],
    )
    return cache


class TestRefresh:
    def test_indexes_all_tickers(self, populated_cache: EdgarCache) -> None:
        u = FullMarketUniverse(
            cache=populated_cache,
            index_path=populated_cache.cache_dir / "idx.json",
            require_positive_book_history=False,
            require_two_year_positive_revenue=False,
        require_common_equity=False,
            )
        n = u.refresh()
        assert n == 4

    def test_persists_index(self, populated_cache: EdgarCache, tmp_path: Path) -> None:
        idx = tmp_path / "idx.json"
        u = FullMarketUniverse(cache=populated_cache, index_path=idx, require_common_equity=False, require_positive_book_history=False, require_two_year_positive_revenue=False)
        u.refresh()
        assert idx.exists()


class TestConstituentsAt:
    def test_active_company_in_universe(self, populated_cache: EdgarCache) -> None:
        u = FullMarketUniverse(
            cache=populated_cache,
            index_path=populated_cache.cache_dir / "idx.json",
            require_positive_book_history=False,
            require_two_year_positive_revenue=False,
        require_common_equity=False,
            )
        # On 2024-06-30, AAPL filed Feb 2024 (within 18 months) — included.
        assert "AAPL" in u.constituents_at(date(2024, 6, 30))

    def test_delisted_company_excluded_after_silence(
        self, populated_cache: EdgarCache
    ) -> None:
        u = FullMarketUniverse(
            cache=populated_cache,
            index_path=populated_cache.cache_dir / "idx.json",
            require_positive_book_history=False,
            require_two_year_positive_revenue=False,
        require_common_equity=False,
            )
        # OLDCO last filed June 2014. By 2024, that's 10 years — outside 18-month window.
        assert "OLDCO" not in u.constituents_at(date(2024, 6, 30))

    def test_delisted_company_INCLUDED_when_querying_during_active_period(
        self, populated_cache: EdgarCache
    ) -> None:
        u = FullMarketUniverse(
            cache=populated_cache,
            index_path=populated_cache.cache_dir / "idx.json",
            require_positive_book_history=False,
            require_two_year_positive_revenue=False,
        require_common_equity=False,
            )
        # On 2014-12-31, OLDCO had filed in June 2014 — within 18 months.
        # This is the *retirement* rule working, not a survivorship
        # guarantee: it holds because OLDCO's facts are in the cache.
        # See TestSurvivorshipBiasIsNotClosed for what it does not cover.
        assert "OLDCO" in u.constituents_at(date(2014, 12, 31))

    def test_company_not_yet_public_excluded(
        self, populated_cache: EdgarCache
    ) -> None:
        u = FullMarketUniverse(
            cache=populated_cache,
            index_path=populated_cache.cache_dir / "idx.json",
            require_positive_book_history=False,
            require_two_year_positive_revenue=False,
        require_common_equity=False,
            )
        # NEWCO's earliest filing is 2021-03-15. Querying 2010 should exclude it.
        assert "NEWCO" not in u.constituents_at(date(2010, 12, 31))

    def test_company_in_universe_after_ipo(
        self, populated_cache: EdgarCache
    ) -> None:
        u = FullMarketUniverse(
            cache=populated_cache,
            index_path=populated_cache.cache_dir / "idx.json",
            require_positive_book_history=False,
            require_two_year_positive_revenue=False,
        require_common_equity=False,
            )
        assert "NEWCO" in u.constituents_at(date(2021, 6, 30))

    def test_etf_excluded_by_default(self, populated_cache: EdgarCache) -> None:
        u = FullMarketUniverse(
            cache=populated_cache, index_path=populated_cache.cache_dir / "idx.json", require_common_equity=False
        )
        # ETFCO has no operating concepts — excluded.
        assert "ETFCO" not in u.constituents_at(date(2024, 6, 30))

    def test_etf_INCLUDED_if_filter_disabled(
        self, populated_cache: EdgarCache
    ) -> None:
        u = FullMarketUniverse(
            cache=populated_cache,
            require_operating_concepts=False,
            require_positive_book_history=False,
            require_two_year_positive_revenue=False,
            require_common_equity=False,
            index_path=populated_cache.cache_dir / "idx.json",
        )
        assert "ETFCO" in u.constituents_at(date(2024, 6, 30))


class TestSurvivorshipBiasIsNotClosed:
    """Pins the gap the module docstring used to deny.

    ``test_delisted_company_INCLUDED_when_querying_during_active_period``
    reads like proof of a survivorship guarantee, and it is not: OLDCO
    comes back only because OLDCO is in the cache. The roster is built
    from the SEC's ``company_tickers.json``, which lists issuers
    registered on the day the prefetch ran, so a company that was
    acquired or failed earlier was never downloaded and has no row to
    retire. Measured on the live index at the time of writing, none of
    TWTR, ATVI, SIVB, FRC, CERN, XLNX, ANTM, MXIM, ALXN or TIF appears.

    These tests exist so the claim cannot be quietly restored: if
    somebody closes the gap, they will fail, and that is the signal to
    rewrite the warning rather than delete the tests.
    """

    def test_an_uncached_issuer_is_absent_at_every_date(
        self, populated_cache: EdgarCache
    ) -> None:
        # GONECO was a real, actively-filing company across this whole
        # span. It is simply not in the cache — the prefetch could not
        # see it, because it had already left the market.
        u = FullMarketUniverse(
            cache=populated_cache,
            index_path=populated_cache.cache_dir / "idx.json",
            require_positive_book_history=False,
            require_two_year_positive_revenue=False,
            require_common_equity=False,
        )
        for as_of in (date(2011, 6, 30), date(2015, 6, 30), date(2024, 6, 30)):
            assert "GONECO" not in u.constituents_at(as_of)

    def test_the_survivor_and_the_departed_are_indistinguishable(
        self, populated_cache: EdgarCache
    ) -> None:
        # The bias is one-sided and that is what makes it dangerous:
        # asked about 2014, the universe answers with companies that
        # were still registered in 2026. A backtest cannot tell that
        # anything is missing, so it reads the gap as a clean result.
        u = FullMarketUniverse(
            cache=populated_cache,
            index_path=populated_cache.cache_dir / "idx.json",
            require_positive_book_history=False,
            require_two_year_positive_revenue=False,
            require_common_equity=False,
        )
        members = u.constituents_at(date(2014, 12, 31))

        assert set(members) <= {"AAPL", "OLDCO", "NEWCO", "ETFCO"}
        assert "GONECO" not in members


class TestProtocolCompliance:
    def test_satisfies_universe_protocol(
        self, populated_cache: EdgarCache
    ) -> None:
        u = FullMarketUniverse(
            cache=populated_cache,
            index_path=populated_cache.cache_dir / "idx.json",
            require_positive_book_history=False,
            require_two_year_positive_revenue=False,
        require_common_equity=False,
            )
        assert isinstance(u, Universe)


class TestStats:
    def test_stats_reports_concept_split(self, populated_cache: EdgarCache) -> None:
        u = FullMarketUniverse(
            cache=populated_cache,
            index_path=populated_cache.cache_dir / "idx.json",
            require_positive_book_history=False,
            require_two_year_positive_revenue=False,
        require_common_equity=False,
            )
        s = u.stats()
        assert s["total_indexed"] == 4
        assert s["with_operating_concepts"] == 3  # AAPL, NEWCO have Assets/Revenues; OLDCO has Revenues; ETFCO does not
        assert s["without_operating_concepts"] == 1
