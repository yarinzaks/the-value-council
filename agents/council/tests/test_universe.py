"""Universe rules must exclude for a stated reason, or say they cannot tell.

The rules split in two: U1-U6 decide whether a name may be held at all,
U7-U8 whether the mechanical sleeve may buy it. Conflating those would
either let the machine buy a $200bn index constituent or stop the
Council reading one, and both are wrong.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agents.council.screen import Outcome
from agents.council.universe import (
    EXPECTED_UNIVERSE_RANGE,
    MAX_FILING_AGE_DAYS,
    MAX_MARKET_CAP,
    MIN_MARKET_CAP,
    MIN_MEDIAN_DOLLAR_VOLUME,
    MIN_PRICE_USD,
    MIN_QUARTERS_OF_FUNDAMENTALS,
    UniverseInputs,
    build_universe,
    check,
    stale_filing_cutoff,
)

AS_OF = date(2026, 8, 14)


def row(ticker: str = "T", **kw) -> UniverseInputs:
    """A company that clears all eight rules."""
    base = dict(
        major_us_listing=True,
        files_10k=True,
        quarters_of_fundamentals=20,
        latest_filing=AS_OF - timedelta(days=45),
        price=25.0,
        median_dollar_volume_63d=2_000_000.0,
        sic=3500,
        market_cap=800_000_000.0,
        in_sp500=False,
    )
    return UniverseInputs(ticker=ticker, **{**base, **kw})


def outcome_of(result, rule: str) -> Outcome:
    return next(r.outcome for r in result.rules if r.rule == rule)


class TestBaseline:
    def test_a_clean_company_clears_everything(self) -> None:
        r = check(row(), AS_OF)
        assert r.tradeable
        assert r.mechanical
        assert len(r.rules) == 8

    def test_every_rule_is_evaluated_even_after_a_failure(self) -> None:
        r = check(row(price=0.01, sic=6020, market_cap=1.0), AS_OF)
        assert len(r.rules) == 8
        assert len(r.failures) >= 3


class TestU1Exchange:
    def test_otc_is_excluded(self) -> None:
        r = check(row(major_us_listing=False), AS_OF)
        assert outcome_of(r, "U1") is Outcome.FAIL
        assert not r.tradeable

    def test_an_unknown_exchange_is_unknown_not_a_pass(self) -> None:
        r = check(row(major_us_listing=None), AS_OF)
        assert outcome_of(r, "U1") is Outcome.UNKNOWN
        assert not r.tradeable


class TestU2Filer:
    def test_a_20f_filer_is_excluded(self) -> None:
        """IFRS answers UNKNOWN, and UNKNOWN cannot be screened."""
        r = check(row(files_10k=False), AS_OF)
        assert outcome_of(r, "U2") is Outcome.FAIL


class TestU3History:
    def test_too_few_quarters_fails(self) -> None:
        r = check(
            row(quarters_of_fundamentals=MIN_QUARTERS_OF_FUNDAMENTALS - 1), AS_OF
        )
        assert outcome_of(r, "U3") is Outcome.FAIL
        assert "quarters" in next(x.detail for x in r.rules if x.rule == "U3")

    def test_exactly_eight_quarters_passes(self) -> None:
        r = check(row(quarters_of_fundamentals=MIN_QUARTERS_OF_FUNDAMENTALS), AS_OF)
        assert outcome_of(r, "U3") is Outcome.PASS

    def test_a_stale_filer_fails(self) -> None:
        r = check(
            row(latest_filing=AS_OF - timedelta(days=MAX_FILING_AGE_DAYS + 1)), AS_OF
        )
        assert outcome_of(r, "U3") is Outcome.FAIL
        assert "days old" in next(x.detail for x in r.rules if x.rule == "U3")

    def test_at_the_staleness_boundary_it_passes(self) -> None:
        r = check(
            row(latest_filing=AS_OF - timedelta(days=MAX_FILING_AGE_DAYS)), AS_OF
        )
        assert outcome_of(r, "U3") is Outcome.PASS

    def test_the_successor_entity_trap(self) -> None:
        """XOM resolves to a 2025 holdco with about a year of filings.

        The 150-year operating history sits under a CIK whose ticker
        list is now empty, so the quarter count is the detection.
        """
        r = check(row(ticker="XOM", quarters_of_fundamentals=4), AS_OF)
        assert outcome_of(r, "U3") is Outcome.FAIL

    def test_unreadable_history_is_unknown(self) -> None:
        r = check(row(quarters_of_fundamentals=None), AS_OF)
        assert outcome_of(r, "U3") is Outcome.UNKNOWN

    def test_the_cutoff_helper_matches_the_rule(self) -> None:
        assert stale_filing_cutoff(AS_OF) == AS_OF - timedelta(
            days=MAX_FILING_AGE_DAYS
        )


class TestU4Price:
    def test_a_bankruptcy_shell_is_excluded(self) -> None:
        """FRCB continued into OTC at $0.0004 with a live price feed."""
        r = check(row(ticker="FRCB", price=0.0004), AS_OF)
        assert outcome_of(r, "U4") is Outcome.FAIL

    def test_exactly_a_dollar_passes(self) -> None:
        assert outcome_of(check(row(price=MIN_PRICE_USD), AS_OF), "U4") is Outcome.PASS

    def test_no_price_is_unknown(self) -> None:
        assert outcome_of(check(row(price=None), AS_OF), "U4") is Outcome.UNKNOWN


class TestU5Liquidity:
    def test_a_thin_name_is_excluded(self) -> None:
        r = check(row(median_dollar_volume_63d=100_000.0), AS_OF)
        assert outcome_of(r, "U5") is Outcome.FAIL

    def test_at_the_threshold_it_passes(self) -> None:
        r = check(row(median_dollar_volume_63d=MIN_MEDIAN_DOLLAR_VOLUME), AS_OF)
        assert outcome_of(r, "U5") is Outcome.PASS

    def test_no_volume_history_is_unknown(self) -> None:
        r = check(row(median_dollar_volume_63d=None), AS_OF)
        assert outcome_of(r, "U5") is Outcome.UNKNOWN


class TestU6Financials:
    @pytest.mark.parametrize("sic", [6000, 6020, 6500, 6999])
    def test_banks_insurers_and_reits_are_excluded(self, sic: int) -> None:
        assert outcome_of(check(row(sic=sic), AS_OF), "U6") is Outcome.FAIL

    @pytest.mark.parametrize("sic", [5999, 7000, 3500])
    def test_everything_else_passes(self, sic: int) -> None:
        assert outcome_of(check(row(sic=sic), AS_OF), "U6") is Outcome.PASS

    def test_no_sic_is_unknown(self) -> None:
        assert outcome_of(check(row(sic=None), AS_OF), "U6") is Outcome.UNKNOWN


class TestU7SizeBand:
    def test_a_mega_cap_is_out_of_the_sleeve(self) -> None:
        r = check(row(market_cap=3_000_000_000_000.0), AS_OF)
        assert outcome_of(r, "U7") is Outcome.FAIL
        # But still tradeable: the Council may read and buy it as Core.
        assert r.tradeable
        assert not r.mechanical

    def test_a_nano_cap_is_out_of_the_sleeve(self) -> None:
        r = check(row(market_cap=MIN_MARKET_CAP - 1), AS_OF)
        assert outcome_of(r, "U7") is Outcome.FAIL

    @pytest.mark.parametrize("cap", [MIN_MARKET_CAP, MAX_MARKET_CAP])
    def test_the_band_is_inclusive(self, cap: float) -> None:
        assert outcome_of(check(row(market_cap=cap), AS_OF), "U7") is Outcome.PASS


class TestU8IndexExclusion:
    def test_an_index_member_is_out_of_the_sleeve(self) -> None:
        r = check(row(in_sp500=True), AS_OF)
        assert outcome_of(r, "U8") is Outcome.FAIL
        assert r.tradeable
        assert not r.mechanical

    def test_unknown_membership_is_unknown(self) -> None:
        """Before the history file starts, "we do not know" is the answer."""
        r = check(row(in_sp500=None), AS_OF)
        assert outcome_of(r, "U8") is Outcome.UNKNOWN
        assert not r.mechanical


class TestTradeableVersusMechanical:
    """The split that lets the Council read what the machine may not buy."""

    def test_u7_and_u8_do_not_affect_tradeability(self) -> None:
        r = check(row(market_cap=1e12, in_sp500=True), AS_OF)
        assert r.tradeable
        assert not r.mechanical

    def test_a_u1_failure_stops_both(self) -> None:
        r = check(row(major_us_listing=False), AS_OF)
        assert not r.tradeable
        assert not r.mechanical

    def test_an_empty_result_is_neither(self) -> None:
        from agents.council.universe import MembershipResult

        empty = MembershipResult(ticker="T")
        assert not empty.tradeable
        assert not empty.mechanical


class TestBuildUniverse:
    def test_it_partitions_the_roster(self) -> None:
        rows = [
            row("KEEP"),
            row("BIG", market_cap=1e12),
            row("OTC", major_us_listing=False),
        ]
        report = build_universe(rows, AS_OF)
        assert set(report.tradeable) == {"KEEP", "BIG"}
        assert report.mechanical == ["KEEP"]

    def test_exclusions_are_attributed_to_the_first_rule(self) -> None:
        """Counts must sum to the number excluded, not double-count."""
        rows = [row("A", major_us_listing=False, price=0.01, sic=6020)]
        report = build_universe(rows, AS_OF)
        assert report.excluded_by == {"U1": 1}

    def test_the_breakdown_names_each_rule(self) -> None:
        rows = [
            row("A", major_us_listing=False),
            row("B", price=0.01),
            row("C", sic=6020),
        ]
        report = build_universe(rows, AS_OF)
        assert report.excluded_by == {"U1": 1, "U4": 1, "U6": 1}

    def test_a_count_outside_the_band_is_flagged(self) -> None:
        """Section 1: under 500 means a gate is miswired, not a changed market."""
        report = build_universe([row("ONLY")], AS_OF)
        assert not report.within_expected_range

    def test_a_count_inside_the_band_is_not_flagged(self) -> None:
        rows = [row(f"T{i:04d}") for i in range(EXPECTED_UNIVERSE_RANGE[0] + 1)]
        report = build_universe(rows, AS_OF)
        assert report.within_expected_range

    def test_an_empty_roster_is_not_an_error(self) -> None:
        report = build_universe([], AS_OF)
        assert report.tradeable == []
        assert not report.within_expected_range
