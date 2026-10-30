"""The shape, and what it refuses to represent.

Two invariants carry the weight. A candidate whose trigger is not below
its price is a purchase and must not be constructible — the type is the
only thing standing between "Graham is waiting for $12" and a message
announcing that he waits for a price already on the screen. And a diff
must report a departure, not only an arrival: a name that silently
leaves the list leaves its reader believing a stale promise.
"""

from __future__ import annotations

import pytest

from core.watchlist import WatchCandidate, diff_watchlist


def a_candidate(ticker="XYZ", price=100.0, trigger_price=80.0, **kw):
    return WatchCandidate(
        ticker=ticker, price=price, trigger_price=trigger_price, **kw
    )


class TestTheCandidate:
    def test_the_required_fall_is_the_gap_to_the_trigger(self):
        assert a_candidate(price=100.0, trigger_price=80.0).required_fall_pct == pytest.approx(20.0)
        assert a_candidate(price=20.0, trigger_price=12.0).required_fall_pct == pytest.approx(40.0)

    def test_a_trigger_at_or_above_the_price_is_refused(self):
        """A purchase, not a watch. The screen owns it."""
        with pytest.raises(ValueError, match="not below the price"):
            a_candidate(price=10.0, trigger_price=10.0)
        with pytest.raises(ValueError, match="not below the price"):
            a_candidate(price=10.0, trigger_price=12.0)

    @pytest.mark.parametrize(
        "price,trigger", [(0.0, 1.0), (-5.0, 1.0), (10.0, 0.0), (10.0, -1.0)]
    )
    def test_a_non_positive_price_is_refused(self, price, trigger):
        with pytest.raises(ValueError):
            a_candidate(price=price, trigger_price=trigger)

    def test_the_reasons_are_carried_verbatim(self):
        """Phrased by the agent, never parsed or rewritten here."""
        got = a_candidate(
            blocking=("P/B=2.50 (> 1.5)",),
            passing=("current ratio=2.50 (≥ 2.0)",),
        )
        assert got.blocking == ("P/B=2.50 (> 1.5)",)
        assert got.passing == ("current ratio=2.50 (≥ 2.0)",)


class TestTheDiff:
    def test_a_new_name_is_an_arrival(self):
        change = diff_watchlist([], [a_candidate(ticker="AAA")])

        assert [c.ticker for c in change.entered] == ["AAA"]
        assert change.left == ()
        assert not change.is_empty

    def test_a_vanished_name_is_a_departure(self):
        change = diff_watchlist(["AAA", "BBB"], [a_candidate(ticker="AAA")])

        assert change.left == ("BBB",)
        assert [c.ticker for c in change.held] == ["AAA"]
        assert change.entered == ()

    def test_an_unchanged_list_says_nothing(self):
        """The whole reason the store exists.

        A notification every run would repeat the same names daily and
        teach its reader to ignore the channel.
        """
        change = diff_watchlist(["AAA", "BBB"], [
            a_candidate(ticker="AAA"), a_candidate(ticker="BBB")
        ])

        assert change.is_empty
        assert len(change.held) == 2

    def test_case_does_not_create_a_phantom_change(self):
        """A stored 'aaa' and a screened 'AAA' are one company."""
        change = diff_watchlist(["aaa"], [a_candidate(ticker="AAA")])

        assert change.is_empty, "a casing difference was read as a new name"

    def test_both_directions_at_once(self):
        change = diff_watchlist(
            ["AAA", "BBB"],
            [a_candidate(ticker="BBB"), a_candidate(ticker="CCC")],
        )

        assert [c.ticker for c in change.entered] == ["CCC"]
        assert change.left == ("AAA",)
        assert [c.ticker for c in change.held] == ["BBB"]

    def test_an_emptied_list_reports_every_departure(self):
        change = diff_watchlist(["AAA", "BBB"], [])

        assert change.left == ("AAA", "BBB")
        assert change.entered == ()
        assert not change.is_empty

    def test_departures_are_ordered_so_a_message_is_stable(self):
        change = diff_watchlist(["ZZZ", "AAA", "MMM"], [])
        assert change.left == ("AAA", "MMM", "ZZZ")
