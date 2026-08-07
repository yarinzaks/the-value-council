"""Tests for Lynch's exit rules.

The behaviour these pin down is the one Lynch names most often as the
amateur's cardinal error: selling a position because it went up. Under
the previous implementation ``select`` re-ranked the universe by PEG
every rebalance and kept the best N, so a stock that rose got a higher
P/E, a higher PEG, a worse rank, and was disposed of for having worked.
``held`` was accepted by ``select`` and never read.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents.lynch.exits import (
    DEFAULT_MAX_HELD_PEG,
    STALWART_PROFIT_TAKE_PCT,
    decide,
    retained,
)
from agents.lynch.ranking import LynchScore
from core.backtest.strategy_runner import HeldPosition

ENTRY = date(2025, 1, 15)
AS_OF = date(2026, 8, 5)


def _score(
    ticker: str,
    category: str,
    peg: float,
    *,
    pegy: float | None = None,
) -> LynchScore:
    return LynchScore(
        ticker=ticker,
        price=100.0,
        market_cap=5_000_000_000.0,
        pe=20.0,
        growth_rate_5yr_pct=20.0,
        growth_rate_3yr_pct=20.0,
        growth_acceleration_pct=0.0,
        dividend_yield_pct=0.0,
        peg=peg,
        pegy=pegy if pegy is not None else peg,
        debt_to_equity=0.3,
        net_income=1.0,
        lynch_category=category,  # type: ignore[arg-type]
        peg_zone="hold",
        suggested_position_size_pct=5.0,
    )


def _position(ticker: str, *, entry: float, now: float) -> HeldPosition:
    return HeldPosition(
        ticker=ticker,
        shares=100.0,
        entry_price=entry,
        entry_date=ENTRY,
        current_price=now,
    )


class TestWinnersAreNotSoldForWinning:
    def test_a_fast_grower_that_doubled_is_kept(self) -> None:
        # Bought at PEG 0.6. The price doubled, so the multiple doubled
        # and PEG is now 1.2 — no longer a buy, and not a sell either.
        # This is the exact position rank-slippage exits destroyed.
        d = decide(
            _score("FAST", "Fast Grower", 1.2),
            _position("FAST", entry=50.0, now=100.0),
        )

        assert d.retained

    def test_the_hold_ceiling_is_looser_than_the_buy_bar(self) -> None:
        # Entry is PEG <= 1.0 per category; retention runs to 2.0.
        # Buying and holding are different decisions, and Lynch's own
        # zones already said so — nothing used the hold zone.
        assert DEFAULT_MAX_HELD_PEG == 2.0

        d = decide(
            _score("FAST", "Fast Grower", 1.9),
            _position("FAST", entry=40.0, now=100.0),
        )

        assert d.retained

    def test_past_the_hold_ceiling_it_goes(self) -> None:
        # Retention is not unconditional. Above 2.0 the zone function
        # already reads "avoid", and an avoid is a sell for something
        # you own.
        d = decide(
            _score("FAST", "Fast Grower", 2.4),
            _position("FAST", entry=30.0, now=100.0),
        )

        assert not d.retained
        assert "2.0" in d.reason


class TestStalwartsAreSoldOnPurpose:
    def test_a_stalwart_up_thirty_percent_is_rotated(self) -> None:
        # Lynch does not hold Stalwarts for tenbaggers; he takes 30-50%
        # and rotates into one that has not moved. Being sold to a rule
        # is not the same as being sold for slipping in a ranking.
        d = decide(
            _score("STAL", "Stalwart", 0.9),
            _position("STAL", entry=100.0, now=135.0),
        )

        assert not d.retained
        assert "rotate" in d.reason

    def test_a_stalwart_below_the_band_is_kept(self) -> None:
        d = decide(
            _score("STAL", "Stalwart", 0.9),
            _position("STAL", entry=100.0, now=115.0),
        )

        assert d.retained

    def test_the_band_starts_at_thirty(self) -> None:
        assert STALWART_PROFIT_TAKE_PCT == 30.0

    def test_a_fast_grower_up_thirty_percent_is_not_rotated(self) -> None:
        # The profit-take is a Stalwart rule specifically. Applying it
        # to a Fast Grower would reintroduce the original bug wearing a
        # different threshold.
        d = decide(
            _score("FAST", "Fast Grower", 1.1),
            _position("FAST", entry=100.0, now=180.0),
        )

        assert d.retained


class TestThesisBreaks:
    def test_a_name_that_fails_the_gates_is_sold(self) -> None:
        # No score means it did not survive the quality gates this
        # rebalance — earnings consistency, leverage, FCF. That is the
        # story breaking, not the multiple moving.
        d = decide(None, _position("GONE", entry=100.0, now=140.0))

        assert not d.retained
        assert "quality gates" in d.reason


class TestSlowGrowersUsePegy:
    def test_the_yield_adjusted_ratio_decides(self) -> None:
        # score_candidates sorts Slow Growers on PEGY; retention has to
        # use the same number or the two disagree about the same stock.
        keep_me = _score("SLOW", "Slow Grower", peg=2.6, pegy=1.4)

        assert decide(keep_me, _position("SLOW", entry=90.0, now=100.0)).retained


class TestRetained:
    def test_no_holdings_means_nothing_to_decide(self) -> None:
        assert retained([_score("A", "Stalwart", 0.8)], None) == ([], [])
        assert retained([_score("A", "Stalwart", 0.8)], {}) == ([], [])

    def test_it_reports_on_every_held_name(self) -> None:
        scores = [
            _score("KEEP", "Fast Grower", 1.3),
            _score("DROP", "Fast Grower", 2.9),
        ]
        held = {
            "KEEP": _position("KEEP", entry=50.0, now=100.0),
            "DROP": _position("DROP", entry=50.0, now=100.0),
            "GATED": _position("GATED", entry=50.0, now=100.0),
        }

        keep, decisions = retained(scores, held)

        assert [s.ticker for s in keep] == ["KEEP"]
        assert len(decisions) == 3
        assert {d.ticker for d in decisions if not d.retained} == {
            "DROP",
            "GATED",
        }

    def test_a_flat_position_is_kept(self) -> None:
        # Guard against over-correction in the other direction: a name
        # that has not moved and is still cheap has no reason to go.
        keep, _ = retained(
            [_score("FLAT", "Cyclical", 1.0)],
            {"FLAT": _position("FLAT", entry=100.0, now=100.0)},
        )

        assert [s.ticker for s in keep] == ["FLAT"]

    def test_an_unusable_cost_basis_does_not_force_a_stalwart_out(
        self,
    ) -> None:
        # return_pct is None when the basis is zero or negative. The
        # profit-take cannot fire on an unknown gain.
        d = decide(
            _score("STAL", "Stalwart", 0.9),
            HeldPosition(
                ticker="STAL",
                shares=10.0,
                entry_price=0.0,
                entry_date=ENTRY,
                current_price=100.0,
            ),
        )

        assert d.retained


class TestThresholdsAreConfigurable:
    def test_a_tighter_ceiling_sells_sooner(self) -> None:
        score = _score("FAST", "Fast Grower", 1.5)
        pos = _position("FAST", entry=50.0, now=100.0)

        assert decide(score, pos).retained
        assert not decide(score, pos, max_held_peg=1.2).retained

    def test_a_looser_profit_take_holds_longer(self) -> None:
        score = _score("STAL", "Stalwart", 0.9)
        pos = _position("STAL", entry=100.0, now=135.0)

        assert not decide(score, pos).retained
        assert decide(score, pos, stalwart_profit_take_pct=50.0).retained


@pytest.mark.parametrize(
    "category", ["Fast Grower", "Cyclical", "Turnaround", "Asset Play"]
)
def test_every_non_stalwart_category_survives_a_big_gain(
    category: str,
) -> None:
    d = decide(
        _score("X", category, 1.4), _position("X", entry=40.0, now=100.0)
    )

    assert d.retained
