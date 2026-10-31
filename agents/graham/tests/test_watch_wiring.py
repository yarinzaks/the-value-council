"""The watch list reaches the adapter, and changes nothing on the way.

Two things are under test. That a near miss recorded by the strategy
becomes a watch entry carrying the price it would be bought at — the
end-to-end path, since each half was already covered alone. And that
adding it moved no money: the weights this strategy returns must hold
only names that passed the screen, never one the watch list named.

That second one is the whole reason this is safe to ship. The owner's
standing rule is that an agent's trading does not change without their
word, and "it only reads" is a claim a test should make, not a comment.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest

from agents.graham.net_net import BenjaminGraham
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.live.agent_adapter import (
    GrahamLive,
    GrahamSecondCohortLive,
    LiveTarget,
)

AS_OF = date(2024, 6, 30)

# Equity $800M over 100M shares is $8.00 of book, with $2.00 of EPS.
#   at $10 -> P/E  5.0, P/B 1.25  -> both inside, a purchase
#   at $20 -> P/E 10.0, P/B 2.50  -> P/B blocks, a near miss at $12.00
BUY_PRICE = 10.0
WATCH_PRICE = 20.0
WATCH_TRIGGER = 12.0


def a_fin(ticker: str) -> PointInTimeFinancials:
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
        eps_diluted=2.0,
        eps_basic=2.0,
        total_equity=800_000_000.0,
        current_assets=500_000_000.0,
        current_liabilities=200_000_000.0,
        total_debt=100_000_000.0,
        long_term_debt=100_000_000.0,
        net_income=50_000_000.0,
        shares_outstanding=100_000_000.0,
        total_liabilities=300_000_000.0,
        dividends_paid=-20_000_000.0,
    )


class _Prices:
    def __init__(self, prices: Mapping[str, float]) -> None:
        self._p = dict(prices)

    def get(self, ticker: str) -> float | None:
        return self._p.get(ticker.upper())


class _Fundamentals:
    def __init__(self, fin: Mapping[str, PointInTimeFinancials]) -> None:
        self._f = dict(fin)

    def get(self, ticker: str) -> PointInTimeFinancials | None:
        return self._f.get(ticker.upper())


def a_run(buyable: int = 2, near: int = 3, portfolio_size: int = 10):
    """Run the real strategy over a universe of purchases and near misses."""
    fins = {f"BUY{i}": a_fin(f"BUY{i}") for i in range(buyable)}
    prices = {t: BUY_PRICE for t in fins}
    for i in range(near):
        t = f"NEAR{i}"
        fins[t] = a_fin(t)
        prices[t] = WATCH_PRICE

    strategy = BenjaminGraham(
        portfolio_size=portfolio_size,
        min_market_cap=1_000.0,
        net_net_fallback_threshold=10,
    )
    weights = strategy.select(
        AS_OF, list(fins), _Prices(prices), _Fundamentals(fins)
    )
    return strategy, weights


class TestNothingMoved:
    """The invariant that makes this shippable."""

    def test_no_watched_name_is_ever_bought(self):
        _strategy, weights = a_run()

        assert weights, "the run bought nothing, so it proves nothing"
        assert all(t.startswith("BUY") for t in weights), (
            f"a name blocked on price was bought: "
            f"{[t for t in weights if not t.startswith('BUY')]}"
        )

    def test_the_weights_are_exactly_the_screen_survivors(self):
        strategy, weights = a_run()
        sel = strategy.selection_history[-1]

        passed = {s.ticker for s in sel.top_scores} | {
            s.ticker for s in sel.top_defensive
        }
        assert set(weights) == passed

    def test_a_universe_of_only_near_misses_buys_nothing(self):
        """And still records them. Holding cash is the right answer."""
        strategy, weights = a_run(buyable=0, near=4)

        assert weights == {}
        assert len(strategy.selection_history[-1].watch_candidates) == 4


class TestTheStrategyRecordsThem:
    def test_the_near_misses_are_recorded_with_their_trigger(self):
        strategy, _weights = a_run()
        candidates = strategy.selection_history[-1].watch_candidates

        assert sorted(c.ticker for c in candidates) == ["NEAR0", "NEAR1", "NEAR2"]
        assert all(
            c.trigger_price == pytest.approx(WATCH_TRIGGER) for c in candidates
        )

    def test_a_purchase_is_not_also_a_near_miss(self):
        strategy, _weights = a_run()
        tickers = {c.ticker for c in strategy.selection_history[-1].watch_candidates}

        assert not any(t.startswith("BUY") for t in tickers)


class TestTheAdapterCarriesThem:
    def test_the_entry_price_target_is_filled_at_last(self):
        """The field has been on LiveWatch from the start, always None."""
        strategy, _weights = a_run()
        watch = GrahamLive(strategy)._collect_watchlist([])

        assert [w.ticker for w in watch] == ["NEAR0", "NEAR1", "NEAR2"]
        assert all(
            w.entry_price_target == pytest.approx(WATCH_TRIGGER) for w in watch
        )
        assert [w.rank for w in watch] == [1, 2, 3]

    def test_the_quotation_travels_beside_the_target(self):
        """Without it the dashboard cannot say how far the price must fall.

        The drilldown has a "distance from trigger" column whose two
        branches both returned a dash, because the entry held the price
        it wanted and not the price it saw.
        """
        strategy, _weights = a_run()
        watch = GrahamLive(strategy)._collect_watchlist([])

        assert all(w.last_price == pytest.approx(WATCH_PRICE) for w in watch)
        assert all(
            w.last_price > w.entry_price_target for w in watch
        ), "a watch is a name the price has to fall to reach"

    def test_the_why_names_the_price_and_the_blocker(self):
        strategy, _weights = a_run()
        watch = GrahamLive(strategy)._collect_watchlist([])

        assert "P/B=2.50" in watch[0].why_en
        assert "$12.00" in watch[0].why_en
        assert "$12.00" in watch[0].why_he, "both languages carry the number"

    def test_a_held_name_is_not_also_awaited(self):
        """The screen's pool is the universe; it does not exclude the book."""
        strategy, _weights = a_run()
        held = [
            LiveTarget(
                ticker="NEAR1", weight=0.5, rank=1, why_en="", why_he="", score=None
            )
        ]
        watch = GrahamLive(strategy)._collect_watchlist(held)

        assert [w.ticker for w in watch] == ["NEAR0", "NEAR2"]
        assert [w.rank for w in watch] == [1, 2], "ranks close the gap"

    def test_the_adapter_respects_its_own_cap(self):
        strategy, _weights = a_run(buyable=1, near=8)
        watch = GrahamLive(strategy, watchlist_size=3)._collect_watchlist([])

        assert len(watch) == 3

    def test_the_second_cohort_inherits_all_of_it(self):
        """Attaching an agent is meant to be free. This is the proof."""
        strategy, _weights = a_run()
        watch = GrahamSecondCohortLive(strategy)._collect_watchlist([])

        assert len(watch) == 3
        assert all(w.entry_price_target == pytest.approx(WATCH_TRIGGER) for w in watch)

    def test_an_agent_that_never_ran_has_no_watch_list(self):
        strategy, _weights = a_run(buyable=2, near=0)
        assert GrahamLive(strategy)._collect_watchlist([]) == []


def test_the_weights_are_identical_with_the_watch_screen_disabled(monkeypatch):
    """The strongest form of "it only reads".

    The same universe, run twice: once normally and once with the near
    miss producer stubbed to return nothing. Any difference in the
    weights would mean the watch list had reached a trading decision.
    """
    import agents.graham.net_net as net_net

    _strategy, with_watch = a_run()

    monkeypatch.setattr(
        net_net, "defensive_watch_candidates", lambda *a, **k: []
    )
    strategy_without, without_watch = a_run()

    assert with_watch == without_watch
    assert strategy_without.selection_history[-1].watch_candidates == []


def test_a_strategy_that_has_not_run_yet_yields_an_empty_watch_list():
    """Not an IndexError in the middle of a finished trading run.

    The watch list is assembled after the trades are already placed and
    persisted, so anything that raises here turns a completed run into
    a failed one for the sake of a list nobody needs yet.
    """
    fresh = BenjaminGraham(portfolio_size=10, min_market_cap=1_000.0)

    assert fresh.selection_history == []
    assert GrahamLive(fresh)._collect_watchlist([]) == []
    assert GrahamSecondCohortLive(fresh)._collect_watchlist([]) == []
