"""Tests for the security-master ticker filters."""

from __future__ import annotations

from core.data.ticker_filter import (
    is_currently_listed,
    is_primary_listing,
)


class TestIsCurrentlyListed:
    """The renamed-ticker gap that is_primary_listing cannot close.

    ASGN is absent from the SEC map because the issuer renamed itself
    Everforth and moved to EFOR under the same CIK 890564.
    is_primary_listing falls open for unmapped tickers - deliberately,
    so a symbol nobody has an opinion on is left to the other filters -
    and that fall-open let both symbols into one portfolio at the same
    entry price, reporting -9.2% and +50.2% because the dead one stopped
    updating.
    """

    def test_a_live_ticker_is_listed(self) -> None:
        assert is_currently_listed("PRU")

    def test_a_renamed_ticker_is_not(self) -> None:
        assert not is_currently_listed("ASGN")
        # ...while the symbol it became still is.
        assert is_currently_listed("EFOR")

    def test_it_is_case_and_space_insensitive(self) -> None:
        assert is_currently_listed("  pru  ")

    def test_it_differs_from_is_primary_listing_on_exactly_this_case(
        self,
    ) -> None:
        # The two disagree only for a ticker the map does not cover,
        # which is the whole reason this function exists.
        assert is_primary_listing("ASGN")
        assert not is_currently_listed("ASGN")

    def test_it_falls_closed_on_an_unknown_symbol(self) -> None:
        assert not is_currently_listed("NOTATICKER1")
