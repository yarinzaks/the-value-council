"""The watch list must promise only what the screen would honour.

Its claim is specific — "Graham will buy this at $Y" — and there are
two ways to make it false. List a company that fails something a price
cannot fix, and no price makes it true. Compute a trigger the screen
would still reject, and the named price is wrong.

The first is covered by feeding the screen's own rejections through;
the second by a round trip: every trigger this module produces is fed
back into the real filter at the real thresholds, which must accept it.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents.graham.filters import (
    DEFAULT_DEFENSIVE_MAX_PB,
    DEFAULT_DEFENSIVE_MAX_PE,
    passes_defensive_filters,
)
from agents.graham.watch import defensive_watch_candidates
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials

AS_OF = date(2024, 6, 30)


def a_fin(
    *,
    ticker: str = "TEST",
    eps_diluted: float | None = 2.0,
    total_equity: float | None = 800_000_000.0,
    current_assets: float | None = 500_000_000.0,
    current_liabilities: float | None = 200_000_000.0,
    total_debt: float | None = 100_000_000.0,
    long_term_debt: float | None = 100_000_000.0,
    net_income: float | None = 50_000_000.0,
    shares_outstanding: float | None = 100_000_000.0,
    total_liabilities: float | None = 300_000_000.0,
    dividends_paid: float | None = -20_000_000.0,
) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=AS_OF,
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=date(2024, 2, 15),
            period_of_report=date(2023, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        eps_diluted=eps_diluted,
        eps_basic=eps_diluted,
        total_equity=total_equity,
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        total_debt=total_debt,
        long_term_debt=long_term_debt,
        net_income=net_income,
        shares_outstanding=shares_outstanding,
        total_liabilities=total_liabilities,
        dividends_paid=dividends_paid,
    )


# A healthy company: equity $800M over 100M shares is $8.00 of book, and
# $2.00 of EPS. At $20.00 that is P/E 10.0 and P/B 2.5 against a $2.0bn
# market cap — so P/B blocks it and nothing else does.
def blocked_on_pb(price: float = 20.0):
    return (a_fin(), price * 100_000_000.0, price)


def only(candidates):
    out = defensive_watch_candidates(candidates, as_of=AS_OF)
    assert len(out) == 1, f"expected exactly one candidate, got {len(out)}"
    return out[0]


class TestWhatGetsListed:
    def test_a_name_blocked_only_on_price_is_listed(self):
        got = only([blocked_on_pb()])

        assert got.ticker == "TEST"
        # P/B 2.5 must reach 1.5, so the price must reach 20 x 1.5/2.5.
        assert got.trigger_price == pytest.approx(12.0)
        assert got.required_fall_pct == pytest.approx(40.0)
        assert got.blocking == ("P/B=2.50 (> 1.5)",)

    def test_the_binding_test_sets_the_trigger(self):
        """Both failing: the price must satisfy the stricter one."""
        # $60 on $2 EPS and $8 book is P/E 30 and P/B 7.5. P/E needs
        # 15/30 = 0.5 of the price; P/B needs 1.5/7.5 = 0.2. P/B binds.
        got = only([(a_fin(), 60.0 * 100_000_000.0, 60.0)])

        assert got.trigger_price == pytest.approx(12.0)
        assert len(got.blocking) == 2

    def test_what_it_already_clears_is_carried(self):
        got = only([blocked_on_pb()])

        assert "P/E=10.00 (≤ 15.0)" in got.passing
        assert "current ratio=2.50 (≥ 2.0)" in got.passing
        assert "positive trailing net income" in got.passing
        assert "pays a dividend" in got.passing

    def test_a_name_that_already_passes_is_not_listed(self):
        """It is a purchase. The screen takes it today."""
        # $10 is P/E 5.0, P/B 1.25 — both inside.
        assert defensive_watch_candidates(
            [(a_fin(), 10.0 * 100_000_000.0, 10.0)], as_of=AS_OF
        ) == []


class TestWhatAPriceCannotFix:
    """The failures that must never appear, at any price."""

    @pytest.mark.parametrize(
        "kwargs,what",
        [
            ({"current_assets": 300_000_000.0}, "current ratio 1.5"),
            ({"long_term_debt": 400_000_000.0}, "debt over working capital"),
            ({"total_debt": 900_000_000.0}, "debt to equity above 1.0"),
            ({"net_income": -1.0}, "a loss"),
            ({"dividends_paid": 0.0}, "no dividend"),
            ({"eps_diluted": -1.0}, "negative earnings"),
        ],
    )
    def test_a_non_price_failure_is_never_a_watch_candidate(self, kwargs, what):
        fin = a_fin(**kwargs)
        assert (
            defensive_watch_candidates(
                [(fin, 20.0 * 100_000_000.0, 20.0)], as_of=AS_OF
            )
            == []
        ), f"listed a company Graham rejects for {what} — no price fixes it"

    def test_a_trigger_that_would_breach_the_size_floor_is_not_promised(self):
        """The one non-price criterion that moves, and the wrong way.

        A $600M company needing a 40% fall arrives at $360M, under the
        $500M floor, where the screen rejects it on size. Promising a
        purchase at that price would be promising a rejection.
        """
        # 30M shares x $20 = $600M, with book $8/share -> P/B 2.5.
        fin = a_fin(shares_outstanding=30_000_000.0, total_equity=240_000_000.0)
        assert (
            defensive_watch_candidates(
                [(fin, 600_000_000.0, 20.0)], as_of=AS_OF
            )
            == []
        )


class TestTheTriggerIsReal:
    @pytest.mark.parametrize("price", [16.0, 20.0, 35.0, 60.0, 120.0])
    def test_the_screen_accepts_the_price_this_module_names(self, price):
        """The round trip, against the real filter at real thresholds.

        The market cap is scaled with the price, because it is the price
        times the share count and a screen fed an unscaled cap would be
        answering about a company that does not exist.
        """
        shares = 100_000_000.0
        fin = a_fin()
        got = defensive_watch_candidates(
            [(fin, price * shares, price)], as_of=AS_OF
        )
        assert got, f"no candidate at {price}"
        trigger = got[0].trigger_price

        verdict = passes_defensive_filters(
            fin,
            trigger * shares,
            trigger,
            as_of=AS_OF,
            max_pe=DEFAULT_DEFENSIVE_MAX_PE,
            max_pb=DEFAULT_DEFENSIVE_MAX_PB,
        )
        assert verdict.passed, (
            f"the screen rejects the price the watch list promised: "
            f"{verdict.rejection_reason}"
        )

    def test_a_cent_above_the_trigger_still_fails(self):
        """The trigger is the threshold, not a comfortable distance."""
        shares = 100_000_000.0
        fin = a_fin()
        got = defensive_watch_candidates([(fin, 20.0 * shares, 20.0)], as_of=AS_OF)
        just_above = got[0].trigger_price * 1.001

        verdict = passes_defensive_filters(
            fin,
            just_above * shares,
            just_above,
            as_of=AS_OF,
            max_pe=DEFAULT_DEFENSIVE_MAX_PE,
            max_pb=DEFAULT_DEFENSIVE_MAX_PB,
        )
        assert not verdict.passed


class TestOrderAndSize:
    def test_the_closest_comes_first(self):
        """This list answers "what next", so nearness ranks it."""
        shares = 100_000_000.0
        rows = [
            (a_fin(ticker="FAR"), 60.0 * shares, 60.0),  # needs 80%
            (a_fin(ticker="NEAR"), 13.0 * shares, 13.0),  # needs ~8%
            (a_fin(ticker="MID"), 20.0 * shares, 20.0),  # needs 40%
        ]
        got = defensive_watch_candidates(rows, as_of=AS_OF)

        assert [c.ticker for c in got] == ["NEAR", "MID", "FAR"]
        assert got[0].required_fall_pct < got[-1].required_fall_pct

    def test_the_list_is_capped(self):
        shares = 100_000_000.0
        rows = [
            (a_fin(ticker=f"T{i}"), (20.0 + i) * shares, 20.0 + i)
            for i in range(25)
        ]
        got = defensive_watch_candidates(rows, as_of=AS_OF, limit=10)

        assert len(got) == 10
        assert [c.ticker for c in got] == [f"T{i}" for i in range(10)], (
            "the cap must keep the closest, not the first seen"
        )

    def test_an_empty_universe_is_an_empty_list(self):
        assert defensive_watch_candidates([], as_of=AS_OF) == []

    def test_rows_with_nothing_in_them_are_skipped(self):
        assert defensive_watch_candidates(
            [(None, None, None), (a_fin(), None, 20.0)], as_of=AS_OF
        ) == []
