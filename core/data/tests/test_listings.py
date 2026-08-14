"""U1 and U8 must answer "unknown" out loud rather than guessing.

Both rules are exclusions, so a wrong "no" costs a candidate and a wrong
"yes" admits one that should never have been screened. The bundles can
be missing, stale or partial, and in every one of those cases the honest
answer is ``None`` — which section 2 turns into a failed gate.
"""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path
from typing import ClassVar

import pytest

from core.data import listings


@pytest.fixture(autouse=True)
def _isolated_bundles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the module at empty temp bundles and clear its caches.

    Both loaders are ``lru_cache``d for the life of the process, so a
    test that forgot this would read whatever the previous one left.
    """
    monkeypatch.setattr(listings, "EXCHANGE_BUNDLE", tmp_path / "company_exchange.json")
    monkeypatch.setattr(
        listings, "SP500_HISTORY_BUNDLE", tmp_path / "sp500_membership.csv.gz"
    )
    listings.load_exchange_map.cache_clear()
    listings.load_sp500_history.cache_clear()
    yield tmp_path
    listings.load_exchange_map.cache_clear()
    listings.load_sp500_history.cache_clear()


def write_exchanges(tmp_path: Path, mapping: dict[str, str]) -> None:
    (tmp_path / "company_exchange.json").write_text(json.dumps(mapping))
    listings.load_exchange_map.cache_clear()


def write_membership(tmp_path: Path, rows: list[tuple[str, list[str]]]) -> None:
    body = "date,tickers\n" + "\n".join(
        f'{when},"{",".join(tickers)}"' for when, tickers in rows
    )
    (tmp_path / "sp500_membership.csv.gz").write_bytes(gzip.compress(body.encode()))
    listings.load_sp500_history.cache_clear()


# ------------------------------------------------------------------- U1


class TestExchange:
    def test_a_major_listing_passes(self, tmp_path: Path) -> None:
        write_exchanges(tmp_path, {"AAPL": "Nasdaq"})
        assert listings.is_major_us_listing("AAPL") is True

    def test_otc_fails(self, tmp_path: Path) -> None:
        """2,514 of the SEC's 10,398 tickers are OTC."""
        write_exchanges(tmp_path, {"SHELLCO": "OTC"})
        assert listings.is_major_us_listing("SHELLCO") is False

    def test_cboe_fails_because_u1_names_three_exchanges(
        self, tmp_path: Path
    ) -> None:
        write_exchanges(tmp_path, {"XYZ": "CBOE"})
        assert listings.is_major_us_listing("XYZ") is False

    def test_an_unknown_ticker_is_none_not_false(self, tmp_path: Path) -> None:
        """A data gap is not a fact about the company."""
        write_exchanges(tmp_path, {"AAPL": "Nasdaq"})
        assert listings.is_major_us_listing("NOPE") is None

    def test_lookup_is_case_insensitive_both_ways(self, tmp_path: Path) -> None:
        write_exchanges(tmp_path, {"aapl": "nasdaq"})
        assert listings.exchange_for("AAPL") == "NASDAQ"
        assert listings.is_major_us_listing("aapl") is True

    def test_amex_is_carried_even_though_the_sec_spells_it_nyse(
        self, tmp_path: Path
    ) -> None:
        write_exchanges(tmp_path, {"OLD": "AMEX"})
        assert listings.is_major_us_listing("OLD") is True

    def test_a_missing_bundle_makes_everything_unknown(self) -> None:
        assert listings.is_major_us_listing("AAPL") is None

    def test_a_corrupt_bundle_does_not_raise(self, tmp_path: Path) -> None:
        (tmp_path / "company_exchange.json").write_text("{not json")
        listings.load_exchange_map.cache_clear()
        assert listings.is_major_us_listing("AAPL") is None

    def test_a_null_exchange_is_dropped_rather_than_stored(
        self, tmp_path: Path
    ) -> None:
        write_exchanges(tmp_path, {"AAPL": "Nasdaq", "BLANK": ""})
        assert listings.exchange_for("BLANK") is None


# ------------------------------------------------------------------- U8


class TestSp500Membership:
    ROWS: ClassVar[list[tuple[str, list[str]]]] = [
        ("1996-01-02", ["AAMRQ", "AAPL", "GE"]),
        ("2013-01-02", ["AAPL", "GE", "TWTR"]),
        ("2026-06-30", ["AAPL", "NVDA"]),
    ]

    def test_a_member_today(self, tmp_path: Path) -> None:
        write_membership(tmp_path, self.ROWS)
        assert listings.in_sp500_on("NVDA", date(2026, 8, 1)) is True

    def test_a_non_member_today(self, tmp_path: Path) -> None:
        write_membership(tmp_path, self.ROWS)
        assert listings.in_sp500_on("CALM", date(2026, 8, 1)) is False

    def test_it_reads_the_snapshot_in_force_not_the_latest(
        self, tmp_path: Path
    ) -> None:
        """The whole point of carrying history."""
        write_membership(tmp_path, self.ROWS)
        assert listings.in_sp500_on("TWTR", date(2013, 6, 1)) is True
        assert listings.in_sp500_on("TWTR", date(2026, 8, 1)) is False

    def test_a_delisted_member_is_still_in_its_own_era(
        self, tmp_path: Path
    ) -> None:
        """AAMRQ is bankrupt AMR — the survivorship fix in one assertion."""
        write_membership(tmp_path, self.ROWS)
        assert listings.in_sp500_on("AAMRQ", date(1996, 6, 1)) is True

    def test_before_the_history_starts_is_unknown(self, tmp_path: Path) -> None:
        write_membership(tmp_path, self.ROWS)
        assert listings.in_sp500_on("AAPL", date(1990, 1, 1)) is None

    def test_the_boundary_date_itself_counts(self, tmp_path: Path) -> None:
        write_membership(tmp_path, self.ROWS)
        assert listings.in_sp500_on("AAMRQ", date(1996, 1, 2)) is True

    def test_a_missing_bundle_is_unknown_not_absent(self) -> None:
        # False here would quietly widen the universe by exactly the
        # names U8 exists to exclude.
        assert listings.in_sp500_on("AAPL", date(2026, 8, 1)) is None

    def test_lookup_is_case_insensitive(self, tmp_path: Path) -> None:
        write_membership(tmp_path, self.ROWS)
        assert listings.in_sp500_on("nvda", date(2026, 8, 1)) is True

    def test_rows_are_sorted_regardless_of_file_order(
        self, tmp_path: Path
    ) -> None:
        write_membership(tmp_path, list(reversed(self.ROWS)))
        assert listings.in_sp500_on("TWTR", date(2013, 6, 1)) is True

    def test_a_malformed_date_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        write_membership(
            tmp_path, [("not-a-date", ["X"]), ("2026-06-30", ["AAPL"])]
        )
        assert listings.in_sp500_on("AAPL", date(2026, 8, 1)) is True

    def test_a_plain_csv_is_read_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A developer who unpacks the bundle should not have to re-zip it."""
        path = tmp_path / "sp500_membership.csv"
        path.write_text('date,tickers\n2026-06-30,"AAPL,NVDA"')
        monkeypatch.setattr(listings, "SP500_HISTORY_BUNDLE", path)
        listings.load_sp500_history.cache_clear()
        assert listings.in_sp500_on("NVDA", date(2026, 8, 1)) is True


class TestBundledFilesAreReal:
    """The shipped bundles, not fixtures — a smoke test with numbers.

    These assert shape and floors rather than exact counts, so a routine
    refresh does not break the suite while an empty or truncated bundle
    still does.
    """

    def test_the_exchange_bundle_covers_the_market(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            listings, "EXCHANGE_BUNDLE", listings.PROJECT_ROOT / "data_bundled" / "company_exchange.json"
        )
        listings.load_exchange_map.cache_clear()
        mapping = listings.load_exchange_map()
        assert len(mapping) > 5_000
        assert listings.is_major_us_listing("AAPL") is True

    def test_the_membership_bundle_goes_back_to_1996(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            listings,
            "SP500_HISTORY_BUNDLE",
            listings.PROJECT_ROOT / "data_bundled" / "sp500_membership.csv.gz",
        )
        listings.load_sp500_history.cache_clear()
        history = listings.load_sp500_history()
        assert len(history) > 1_000
        assert history[0][0].year == 1996
        # If this ever fails, the bundle was rebuilt from a
        # survivorship-biased source.
        assert listings.in_sp500_on("AAMRQ", date(1996, 1, 2)) is True
