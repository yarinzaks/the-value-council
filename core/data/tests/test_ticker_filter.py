"""Tests for the common-equity ticker filter."""

from __future__ import annotations

import pytest

from core.data.ticker_filter import (
    filter_common_equity,
    is_common_equity,
    is_primary_listing,
    primary_listings,
)


class TestCommonEquityAccept:
    @pytest.mark.parametrize(
        "ticker",
        ["AAPL", "MSFT", "META", "GOOGL", "AMZN", "BRK", "T", "F", "PG", "WMT", "NVDA", "INTC"],
    )
    def test_accepts(self, ticker: str) -> None:
        assert is_common_equity(ticker) is True


class TestRejectsByLength:
    def test_too_long(self) -> None:
        assert is_common_equity("ABCDEF") is False

    def test_empty(self) -> None:
        assert is_common_equity("") is False


class TestRejectsByDigits:
    @pytest.mark.parametrize("ticker", ["BRK1", "ABC2", "1ABC", "A1B2"])
    def test_rejects_digits(self, ticker: str) -> None:
        assert is_common_equity(ticker) is False


class TestRejectsByNonAlpha:
    @pytest.mark.parametrize("ticker", ["BRK.B", "ABC-A", "AB_C", "AB C", "AB/C"])
    def test_rejects_non_alpha(self, ticker: str) -> None:
        assert is_common_equity(ticker) is False


class TestNasdaqClassIndicators5Letter:
    @pytest.mark.parametrize(
        "ticker",
        [
            "RSMDF",  # foreign
            "ENBNF",  # foreign
            "WFCNP",  # preferred
            "BHFAM",  # preferred
            "ACMRF",
            "ABCDX",  # mutual fund
            "ABCDY",  # ADR
            "ABCDW",  # warrant
            "ABCDQ",  # bankruptcy
            "ABCDR",  # rights
            "ABCDO",  # preferred
        ],
    )
    def test_rejects_class_indicators(self, ticker: str) -> None:
        assert is_common_equity(ticker) is False

    @pytest.mark.parametrize("ticker", ["GOOGL", "BRKB", "TSLAA"])  # 5-letter common
    def test_accepts_5_letter_common(self, ticker: str) -> None:
        # Last letter not in {F, M, N, O, P, Q, R, W, X, Y, Z}
        assert is_common_equity(ticker) is True


class TestNasdaqClassIndicators4Letter:
    @pytest.mark.parametrize("ticker", ["ABCQ", "ABCW"])
    def test_rejects_class_indicators(self, ticker: str) -> None:
        assert is_common_equity(ticker) is False


class TestExplicitDenyList:
    @pytest.mark.parametrize(
        "ticker",
        ["MGR", "MGRE", "AFGE", "AFGC", "AFGD", "RZB", "RZC", "SOJD", "SOJE", "DTB", "DTG"],
    )
    def test_denies_known_baby_bonds(self, ticker: str) -> None:
        assert is_common_equity(ticker) is False


class TestCaseSensitivity:
    def test_lowercase_normalized(self) -> None:
        assert is_common_equity("aapl") is True

    def test_whitespace_stripped(self) -> None:
        assert is_common_equity("  AAPL  ") is True


class TestFilterBatch:
    def test_filters_mixed_list(self) -> None:
        tickers = ["AAPL", "MGR", "MSFT", "AFGE", "GOOGL", "WFCNP"]
        kept = filter_common_equity(tickers)
        assert kept == ["AAPL", "MSFT", "GOOGL"]


class TestPrimaryListing:
    """Symbol shape cannot see that ENBFF and ENB are one company.

    Measured on the bundled SEC map (10,412 rows): 1,464 CIKs carry more
    than one ticker; 418 still had more than one survive is_common_equity,
    contributing 579 redundant symbols. In the live books, 54 of 210
    positions were non-primary listings.
    """

    def test_enbridge_keeps_only_the_common(self) -> None:
        assert is_primary_listing("ENB")
        for foreign_class in ("ENBFF", "ENBGF", "ENBOF", "ENBRF", "ENNPF"):
            assert not is_primary_listing(foreign_class), foreign_class

    def test_freddie_and_fannie_keep_only_the_common(self) -> None:
        assert is_primary_listing("FMCC")
        assert is_primary_listing("FNMA")
        for pref in ("FMCCG", "FMCKL", "FNMAS", "FNMAH"):
            assert not is_primary_listing(pref), pref

    def test_etn_issuer_keeps_only_the_bank(self) -> None:
        # Bank of Montreal's CIK carries 26 exchange-traded notes.
        assert is_primary_listing("BMO")
        for etn in ("FNGU", "BULZ", "GDXU", "CARU"):
            assert not is_primary_listing(etn), etn

    def test_ordinary_single_listing_companies_are_kept(self) -> None:
        for t in ("AAPL", "MSFT", "NVDA", "KO", "JNJ"):
            assert is_primary_listing(t), t

    def test_unknown_ticker_falls_open(self) -> None:
        # A symbol the SEC map does not cover is left to the other
        # filters rather than silently dropped.
        assert is_primary_listing("ZZZQQ")

    def test_primary_set_is_a_strict_subset_of_common_equity(self) -> None:
        primaries = primary_listings()
        assert primaries
        assert all(is_common_equity(t) for t in primaries)

    def test_no_cik_contributes_two_primaries(self) -> None:
        import json

        from core.paths import PROJECT_ROOT

        raw = json.loads(
            (PROJECT_ROOT / "data_bundled" / "company_tickers.json").read_text()
        )
        primaries = primary_listings()
        seen: dict[int, str] = {}
        for row in raw.values():
            t = str(row["ticker"]).upper().strip()
            if t not in primaries:
                continue
            cik = int(row["cik_str"])
            assert cik not in seen, f"CIK {cik}: {seen.get(cik)} and {t}"
            seen[cik] = t

    def test_known_baby_bonds_from_the_live_books_are_rejected(self) -> None:
        # Every one of these was actually held by an agent.
        for t in (
            "PFH", "PRS", "PRH", "AIZN", "UNMA", "SOJF",
            "XELLL", "KMPB", "UZE", "UZF", "UZD", "BHFAL",
        ):
            assert not is_primary_listing(t), t
