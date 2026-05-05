"""Tests for the common-equity ticker filter."""

from __future__ import annotations

import pytest

from core.data.ticker_filter import filter_common_equity, is_common_equity


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
