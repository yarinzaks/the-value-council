"""Tests for LivePortfolio — buy/sell accounting, NAV, persistence."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from core.live.portfolio import (
    DEFAULT_COMMISSION_USD,
    DEFAULT_INITIAL_CASH,
    UNPRICED_AFTER_SESSIONS,
    LivePortfolio,
    LivePortfolioError,
    Position,
    WatchEntry,
    now_iso,
    unpriced_since_for,
)


# ---------------------------------------------------------------------------
# Construction & seeding
# ---------------------------------------------------------------------------
class TestSeed:
    def test_default_seed_state(self) -> None:
        p = LivePortfolio(agent="test")
        assert p.cash == DEFAULT_INITIAL_CASH
        assert p.initial_cash == DEFAULT_INITIAL_CASH
        assert p.invested == 0
        assert p.total_nav == DEFAULT_INITIAL_CASH
        assert p.positions == []
        assert p.watchlist == []

    def test_load_or_seed_creates_when_missing(self, tmp_path: Path) -> None:
        p = LivePortfolio.load_or_seed("alpha", directory=tmp_path)
        assert p.agent == "alpha"
        assert p.cash == DEFAULT_INITIAL_CASH


# ---------------------------------------------------------------------------
# Buy
# ---------------------------------------------------------------------------
class TestBuy:
    def test_buy_basic_accounting(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        trade = p.buy(
            "AAPL",
            target_dollars=5_000,
            price=200.0,
            entry_date="2026-04-29",
            why_en="value", why_he="ערך",
        )
        # (5000 - 2.50 commission) / 200 = 24.99 shares, floored to 24.
        # The $197.50 that cannot buy a 25th share
        # stays in cash, because the broker this book is copied into
        # sells whole shares.
        assert trade.shares == 24.0
        assert trade.gross_value == pytest.approx(24 * 200.0)
        assert trade.cost_paid == pytest.approx(DEFAULT_COMMISSION_USD)
        assert p.cash == pytest.approx(10_000.0 - 4_800.0 - 2.50, abs=0.01)
        assert p.cumulative_costs == pytest.approx(trade.cost_paid)
        assert len(p.positions) == 1

    def test_buy_records_why_strings(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy(
            "AAPL", target_dollars=1_000, price=100, entry_date="2026-04-29",
            why_en="EY 25%, ROC 30%", why_he="EY 25%, ROC 30%",
        )
        assert p.positions[0].why_en == "EY 25%, ROC 30%"
        assert p.positions[0].why_he == "EY 25%, ROC 30%"

    def test_buy_rejects_negative_target(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        with pytest.raises(LivePortfolioError):
            p.buy("AAPL", target_dollars=-100, price=100, entry_date="x", why_en="", why_he="")

    def test_buy_rejects_zero_price(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        with pytest.raises(LivePortfolioError):
            p.buy("X", target_dollars=100, price=0, entry_date="x", why_en="", why_he="")

    def test_buy_rejects_when_target_exceeds_cash(self) -> None:
        p = LivePortfolio(agent="test", cash=100)
        with pytest.raises(LivePortfolioError):
            p.buy("X", target_dollars=200, price=1, entry_date="x", why_en="", why_he="")

    def test_a_target_below_one_share_is_refused(self) -> None:
        # A $50 target on a $100 stock is not half a share, it is no
        # trade. The runner is expected to have removed the name before
        # it got here; refusing rather than rounding up means a caller
        # that did not cannot overspend its slot by 2x.
        p = LivePortfolio(agent="test", cash=10_000)
        with pytest.raises(LivePortfolioError, match="whole share"):
            p.buy(
                "X", target_dollars=50, price=100, entry_date="x",
                why_en="", why_he="",
            )

    def test_a_target_of_exactly_one_share_still_fails_on_cost(self) -> None:
        # $100 buys one $100 share and $2.50 of commission it cannot
        # pay, so the floor takes it to zero. The slot has to carry the
        # fee.
        p = LivePortfolio(agent="test", cash=10_000)
        with pytest.raises(LivePortfolioError, match="whole share"):
            p.buy(
                "X", target_dollars=100, price=100, entry_date="x",
                why_en="", why_he="",
            )
        trade = p.buy(
            "X", target_dollars=102.5, price=100, entry_date="x",
            why_en="", why_he="",
        )
        assert trade.shares == 1.0

    def test_a_target_that_rounds_to_zero_shares_is_rejected(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        with pytest.raises(LivePortfolioError):
            p.buy(
                "X",
                target_dollars=1e-9,
                price=100,
                entry_date="x",
                why_en="",
                why_he="",
            )

    def test_buy_merges_into_existing_position(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("AAPL", target_dollars=2_000, price=100, entry_date="2026-04-29", why_en="", why_he="")
        p.buy("AAPL", target_dollars=2_000, price=120, entry_date="2026-04-30", why_en="", why_he="")
        assert len(p.positions) == 1
        # Two whole-share lots: (2000-2.50)/100 = 19.98 floors to 19 at
        # 100, then (2000-2.50)/120 = 16.65 floors to 16 at 120.
        lot1, lot2 = 19.0, 16.0
        avg = (lot1 * 100 + lot2 * 120) / (lot1 + lot2)
        assert p.positions[0].entry_price == pytest.approx(avg, rel=1e-6)
        assert p.positions[0].shares == lot1 + lot2


# ---------------------------------------------------------------------------
# Sell
# ---------------------------------------------------------------------------
class TestSell:
    def test_sell_full_liquidation(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("AAPL", target_dollars=2_000, price=100, entry_date="2026-04-29", why_en="", why_he="")
        cash_before_sell = p.cash
        # Sell at 110 — gain of 10/share on 19 whole shares.
        shares = 19.0
        trade = p.sell("AAPL", price=110.0)
        assert trade.shares == shares
        assert trade.realized_pnl_usd == pytest.approx(shares * (110 - 100))
        # Cash should be cash_before_sell + proceeds - the commission
        gross = shares * 110.0
        assert p.cash == pytest.approx(
            cash_before_sell + gross - DEFAULT_COMMISSION_USD
        )
        assert len(p.positions) == 0

    def test_a_sale_says_what_it_closed(self) -> None:
        """The entry date and cost travel with the sale.

        A sale that carries only its own price and profit has no
        beginning: the ledger could say what a name made and never how
        long it was held, so no page could draw it from entry to exit.
        """
        p = LivePortfolio(agent="test", cash=10_000)
        bought = p.buy(
            "AAPL", target_dollars=2_000, price=100, entry_date="2026-04-29",
            why_en="", why_he="",
        )
        assert bought.entry_date is None and bought.entry_price is None
        trade = p.sell("AAPL", price=110.0)
        assert trade.entry_date == "2026-04-29"
        assert trade.entry_price == pytest.approx(100.0)

    def test_a_trim_keeps_the_original_entry(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("AAPL", target_dollars=2_000, price=100, entry_date="2026-04-29",
              why_en="", why_he="")
        trade = p.sell("AAPL", price=110.0, shares=5)
        assert trade.entry_date == "2026-04-29"
        assert trade.entry_price == pytest.approx(100.0)
        assert p.positions[0].entry_date == "2026-04-29"

    def test_sell_unknown_ticker_raises(self) -> None:
        p = LivePortfolio(agent="test")
        with pytest.raises(LivePortfolioError):
            p.sell("AAPL", price=100)

    def test_sell_zero_price_raises(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("AAPL", target_dollars=2_000, price=100, entry_date="d", why_en="", why_he="")
        with pytest.raises(LivePortfolioError):
            p.sell("AAPL", price=0)


# ---------------------------------------------------------------------------
# Commission — the broker's, not a model of one
# ---------------------------------------------------------------------------
class TestFlatCommission:
    """The owner's broker charges $2.50 an order, whatever its size.

    The book charged 0.1% of the gross instead: 48c on a typical $480
    order where the broker takes $2.50, so every book understated its
    costs about six-fold (audit ENG-2).
    """

    BROKER = 2.50

    def test_a_buy_pays_one_flat_commission(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        trade = p.buy(
            "AAPL", target_dollars=5_000, price=200.0, entry_date="d",
            why_en="", why_he="",
        )
        # (5000 - 2.50) / 200 = 24.99 shares, floored to 24.
        assert trade.shares == 24.0
        assert trade.cost_paid == pytest.approx(self.BROKER)
        assert p.cash == pytest.approx(10_000.0 - 24 * 200.0 - self.BROKER)
        assert p.cumulative_costs == pytest.approx(self.BROKER)

    def test_the_commission_does_not_scale_with_the_order(self) -> None:
        p = LivePortfolio(agent="test", cash=100_000)
        small = p.buy(
            "A", target_dollars=110, price=100.0, entry_date="d",
            why_en="", why_he="",
        )
        large = p.buy(
            "B", target_dollars=50_000, price=100.0, entry_date="d",
            why_en="", why_he="",
        )
        assert small.cost_paid == pytest.approx(self.BROKER)
        assert large.cost_paid == pytest.approx(self.BROKER)

    def test_a_sale_pays_one_flat_commission(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("AAPL", target_dollars=2_000, price=100, entry_date="d",
              why_en="", why_he="")
        before = p.cash
        trade = p.sell("AAPL", price=110.0)
        assert trade.cost_paid == pytest.approx(self.BROKER)
        assert p.cash == pytest.approx(before + trade.gross_value - self.BROKER)
        assert p.cumulative_costs == pytest.approx(2 * self.BROKER)

    def test_a_trim_pays_one_flat_commission(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("AAPL", target_dollars=2_000, price=100, entry_date="d",
              why_en="", why_he="")
        trade = p.sell("AAPL", price=100.0, shares=5)
        assert trade.cost_paid == pytest.approx(self.BROKER)

    def test_the_target_must_carry_the_commission(self) -> None:
        # One $100 share costs $102.50 at this broker. A $102.49 target
        # cannot place it; the floor takes it to zero shares.
        p = LivePortfolio(agent="test", cash=10_000)
        with pytest.raises(LivePortfolioError, match="whole share"):
            p.buy("X", target_dollars=102.49, price=100, entry_date="d",
                  why_en="", why_he="")
        trade = p.buy("X", target_dollars=102.50, price=100, entry_date="d",
                      why_en="", why_he="")
        assert trade.shares == 1.0

    def test_a_target_smaller_than_the_commission_buys_nothing(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        with pytest.raises(LivePortfolioError, match="whole share"):
            p.buy("PENNY", target_dollars=2.0, price=0.01, entry_date="d",
                  why_en="", why_he="")
        assert p.cash == pytest.approx(10_000.0)
        assert p.cumulative_costs == 0.0

    def test_a_buy_never_overdraws_cash(self) -> None:
        # Spending the whole of the cash on a name that divides it
        # exactly: the commission has to come out of the shares.
        p = LivePortfolio(agent="test", cash=1_000)
        trade = p.buy("X", target_dollars=1_000, price=100, entry_date="d",
                      why_en="", why_he="")
        assert trade.shares == 9.0
        assert p.cash == pytest.approx(1_000 - 900 - self.BROKER)
        assert p.cash >= 0

    def test_a_negative_commission_is_refused(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        with pytest.raises(LivePortfolioError, match="commission"):
            p.buy("X", target_dollars=500, price=100, entry_date="d",
                  why_en="", why_he="", commission=-1.0)
        p.buy("X", target_dollars=500, price=100, entry_date="d",
              why_en="", why_he="")
        with pytest.raises(LivePortfolioError, match="commission"):
            p.sell("X", price=100, commission=-1.0)

    def test_a_zero_commission_is_honoured(self) -> None:
        # A merger cash-out is not an order and pays nothing; the
        # settlement script relies on being able to say so.
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("X", target_dollars=500, price=100, entry_date="d",
              why_en="", why_he="")
        costs = p.cumulative_costs
        trade = p.sell("X", price=100, commission=0.0)
        assert trade.cost_paid == 0.0
        assert p.cumulative_costs == pytest.approx(costs)

    def test_a_proportional_cost_is_still_available(self) -> None:
        # Kept for a caller that wants to model slippage on top of the
        # commission. Off by default in the live book.
        p = LivePortfolio(agent="test", cash=10_000)
        trade = p.buy("X", target_dollars=1_000, price=100, entry_date="d",
                      why_en="", why_he="", commission=0.0, cost_bps=0.01)
        # 1000 / 1.01 / 100 = 9.9 → 9 shares, cost 1% of $900.
        assert trade.shares == 9.0
        assert trade.cost_paid == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# NAV / mark-to-market
# ---------------------------------------------------------------------------
class TestMarkToMarket:
    def test_total_nav_with_positions(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("AAPL", target_dollars=2_000, price=100, entry_date="d", why_en="", why_he="")
        # (2000-2.50)/100 = 19.98 shares, floored to 19 at 100 = $1,900
        # (commission $2.50), leaving $97.50 of the slot in cash. Mark
        # to 120.
        shares = 19.0
        p.mark_to_market({"AAPL": 120.0})
        assert p.invested == pytest.approx(shares * 120.0, rel=1e-6)
        assert p.total_nav == pytest.approx(p.cash + shares * 120.0)

    def test_pnl_pct_computed(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("AAPL", target_dollars=2_000, price=100, entry_date="d", why_en="", why_he="")
        p.mark_to_market({"AAPL": 110.0})
        assert p.positions[0].pnl_pct == pytest.approx(10.0)

    def test_weights_sum_to_invested_pct(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("AAPL", target_dollars=2_000, price=100, entry_date="d", why_en="", why_he="")
        p.buy("MSFT", target_dollars=2_000, price=200, entry_date="d", why_en="", why_he="")
        p.mark_to_market({"AAPL": 100, "MSFT": 200})
        total_weight = sum(pos.weight_pct for pos in p.positions)
        # invested ~ (1900 + 1800) of NAV ~10K → ~37%
        assert total_weight < 100
        assert total_weight > 0

    def test_missing_price_keeps_last_mark(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("X", target_dollars=2_000, price=100, entry_date="d", why_en="", why_he="")
        p.mark_to_market({"X": 110})
        # Now price source returns None — should keep 110
        p.mark_to_market({"X": None})
        assert p.positions[0].current_price == 110


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------
class TestWatchlist:
    def test_set_replaces(self) -> None:
        p = LivePortfolio(agent="test")
        p.set_watchlist([
            WatchEntry(ticker="A", identified_date="d", current_rank=31),
            WatchEntry(ticker="B", identified_date="d", current_rank=32),
        ])
        assert {w.ticker for w in p.watchlist} == {"A", "B"}
        p.set_watchlist([WatchEntry(ticker="C", identified_date="d", current_rank=40)])
        assert [w.ticker for w in p.watchlist] == ["C"]

    def test_set_filters_held_tickers(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("HELD", target_dollars=200, price=10, entry_date="d", why_en="", why_he="")
        p.set_watchlist([
            WatchEntry(ticker="HELD", identified_date="d", current_rank=1),
            WatchEntry(ticker="WATCH", identified_date="d", current_rank=2),
        ])
        assert [w.ticker for w in p.watchlist] == ["WATCH"]


# ---------------------------------------------------------------------------
# Persistence (round-trip)
# ---------------------------------------------------------------------------
class TestPersistence:
    def test_round_trip(self, tmp_path: Path) -> None:
        p = LivePortfolio(agent="rtt", cash=10_000)
        p.buy(
            "AAPL", target_dollars=2_000, price=100, entry_date="2026-04-29",
            why_en="EY 25%", why_he="EY 25%",
        )
        p.set_watchlist([
            WatchEntry(
                ticker="MSFT", identified_date="2026-04-29",
                current_rank=42, entry_trigger="rank top 30",
                why_en="watch", why_he="במעקב",
            ),
        ])
        p.last_updated = now_iso()
        path = p.save(directory=tmp_path)
        assert path.exists()

        loaded = LivePortfolio.load_or_seed("rtt", directory=tmp_path)
        assert loaded.cash == pytest.approx(p.cash)
        assert len(loaded.positions) == 1
        assert loaded.positions[0].ticker == "AAPL"
        assert loaded.positions[0].why_en == "EY 25%"
        assert loaded.watchlist[0].ticker == "MSFT"
        assert loaded.watchlist[0].current_rank == 42

    def test_atomic_write_no_partial_file(self, tmp_path: Path) -> None:
        p = LivePortfolio(agent="atomic", cash=10_000)
        p.save(directory=tmp_path)
        files = list(tmp_path.iterdir())
        # Should have exactly one file: <agent>.json (no leftover .tmp)
        assert any(f.name == "atomic.json" for f in files)
        assert not any(f.name.endswith(".tmp") for f in files)

    def test_to_dict_schema_keys(self) -> None:
        p = LivePortfolio(agent="schema")
        d = p.to_dict()
        for key in (
            "agent", "cash", "invested", "total_nav", "positions",
            "watchlist", "last_updated", "initial_cash",
        ):
            assert key in d, f"missing {key}"

    def test_the_dividend_floor_is_written_under_its_real_name(self) -> None:
        """It is a settlement floor, and the key has to say so.

        It was called ``inception_date`` and it is not one: it is stamped
        the first time settlement runs over a book with positions, so
        every one of the eleven agents seeded on 2026-05-05 carries
        2026-08-10 — the day the dividend code shipped. Read as an
        inception, it understates a book's life by three months, which
        is exactly the misreading it invited.
        """
        p = LivePortfolio(agent="floor")
        p.dividend_floor_date = "2026-08-10"
        d = p.to_dict()
        assert d["dividend_floor_date"] == "2026-08-10"
        assert "inception_date" not in d

    def test_a_book_written_under_the_old_key_still_loads(self, tmp_path: Path) -> None:
        """The twelve live books on disk predate the rename.

        Dropping the old key on read would silently reset the floor to
        empty, and the next settlement would stamp today — paying
        nothing for the gap and losing the record of where the gap was.
        """
        (tmp_path / "legacy.json").write_text(
            json.dumps(
                {
                    "agent": "legacy",
                    "cash": 10_000.0,
                    "initial_cash": 10_000.0,
                    "inception_date": "2026-08-10",
                    "positions": [],
                    "watchlist": [],
                }
            )
        )
        loaded = LivePortfolio.load_or_seed("legacy", directory=tmp_path)
        assert loaded.dividend_floor_date == "2026-08-10"

    def test_the_new_key_wins_when_a_book_carries_both(self, tmp_path: Path) -> None:
        """A half-migrated file must not resurrect the stale value."""
        (tmp_path / "both.json").write_text(
            json.dumps(
                {
                    "agent": "both",
                    "cash": 10_000.0,
                    "initial_cash": 10_000.0,
                    "inception_date": "2026-08-10",
                    "dividend_floor_date": "2026-05-05",
                    "positions": [],
                    "watchlist": [],
                }
            )
        )
        loaded = LivePortfolio.load_or_seed("both", directory=tmp_path)
        assert loaded.dividend_floor_date == "2026-05-05"

    def test_corrupt_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("not json {")
        with pytest.raises(LivePortfolioError):
            LivePortfolio.load_or_seed("broken", directory=tmp_path)


class TestCumulativeReturn:
    def test_no_positions_zero(self) -> None:
        p = LivePortfolio(agent="x", cash=10_000, initial_cash=10_000)
        assert p.cumulative_return_pct == 0.0

    def test_positive_after_gain(self) -> None:
        p = LivePortfolio(agent="x", cash=10_000, initial_cash=10_000)
        p.buy("X", target_dollars=2_000, price=100, entry_date="d", why_en="", why_he="")
        p.mark_to_market({"X": 200})
        # initial 10k → now ~ (cash 8097.50) + (19*200) = 11897 — ~+19% return
        assert p.cumulative_return_pct > 15.0


class TestRenameTicker:
    """A rename is a relabel, not a trade.

    ASGN's issuer began filing as EFOR. Four books carried the position
    frozen at a mark seven weeks old, understating it by 69% — EFOR was
    at $32.22 while ASGN's last bar said $19.08.
    """

    @staticmethod
    def _book(*lines: tuple[str, float, float, str]) -> LivePortfolio:
        p = LivePortfolio(agent="stub")
        for ticker, shares, entry, entry_date in lines:
            p.positions.append(
                Position(
                    ticker=ticker,
                    shares=shares,
                    entry_price=entry,
                    entry_date=entry_date,
                    current_price=entry,
                )
            )
        return p

    def test_it_relabels_without_touching_the_basis(self) -> None:
        p = self._book(("ASGN", 26.0, 21.02, "2026-05-06"))

        assert p.rename_ticker("ASGN", "EFOR") is True

        assert [x.ticker for x in p.positions] == ["EFOR"]
        assert p.positions[0].shares == pytest.approx(26.0)
        assert p.positions[0].entry_price == pytest.approx(21.02)
        assert p.positions[0].entry_date == "2026-05-06"

    def test_an_absent_ticker_changes_nothing(self) -> None:
        p = self._book(("AAPL", 10.0, 100.0, "2026-05-06"))

        assert p.rename_ticker("ASGN", "EFOR") is False
        assert [x.ticker for x in p.positions] == ["AAPL"]

    def test_it_merges_into_an_existing_line(self) -> None:
        # Klarman held both labels at once, and renaming without merging
        # left two positions in one company. _index_of returns the first,
        # so the second is invisible to every buy, sell and dividend
        # while still counting toward NAV.
        p = self._book(
            ("EFOR", 22.0, 20.11, "2026-05-08"),
            ("ASGN", 22.0, 20.87, "2026-05-08"),
        )

        p.rename_ticker("ASGN", "EFOR")

        assert len(p.positions) == 1
        merged = p.positions[0]
        assert merged.ticker == "EFOR"
        assert merged.shares == pytest.approx(44.0)
        # Share-weighted: (22*20.11 + 22*20.87) / 44
        assert merged.entry_price == pytest.approx(20.49)

    def test_the_merged_line_keeps_the_earlier_entry_date(self) -> None:
        p = self._book(
            ("EFOR", 10.0, 30.0, "2026-07-01"),
            ("ASGN", 10.0, 20.0, "2026-05-06"),
        )

        p.rename_ticker("ASGN", "EFOR")

        assert p.positions[0].entry_date == "2026-05-06"

    def test_paid_dividends_follow_the_shares(self) -> None:
        p = self._book(("ASGN", 10.0, 20.0, "2026-05-06"))
        p.paid_dividends["ASGN"] = ["2026-06-15"]

        p.rename_ticker("ASGN", "EFOR")

        assert p.paid_dividends == {"EFOR": ["2026-06-15"]}

    def test_merged_dividend_records_are_deduplicated(self) -> None:
        p = self._book(
            ("EFOR", 10.0, 20.0, "2026-05-06"),
            ("ASGN", 10.0, 20.0, "2026-05-06"),
        )
        p.paid_dividends["EFOR"] = ["2026-06-15"]
        p.paid_dividends["ASGN"] = ["2026-06-15", "2026-07-20"]

        p.rename_ticker("ASGN", "EFOR")

        assert p.paid_dividends == {"EFOR": ["2026-06-15", "2026-07-20"]}

    def test_the_watchlist_is_relabelled_too(self) -> None:
        p = self._book(("ASGN", 10.0, 20.0, "2026-05-06"))
        p.watchlist.append(WatchEntry(ticker="ASGN", identified_date="2026-05-01"))

        p.rename_ticker("ASGN", "EFOR")

        assert [w.ticker for w in p.watchlist] == ["EFOR"]

    def test_it_survives_a_round_trip_through_json(self, tmp_path: Path) -> None:
        p = self._book(
            ("EFOR", 22.0, 20.11, "2026-05-08"),
            ("ASGN", 22.0, 20.87, "2026-05-08"),
        )
        p.rename_ticker("ASGN", "EFOR")
        p.save(directory=tmp_path)

        reloaded = LivePortfolio.load_or_seed("stub", directory=tmp_path)

        assert len(reloaded.positions) == 1
        assert reloaded.positions[0].shares == pytest.approx(44.0)


class TestConsolidate:
    """One line per ticker is an invariant every other method assumes.

    A second line is invisible to buys, sells and dividend settlement —
    _index_of returns the first — while still counting toward NAV.
    """

    @staticmethod
    def _book(*lines: tuple[str, float, float, str]) -> LivePortfolio:
        p = LivePortfolio(agent="stub")
        for ticker, shares, entry, entry_date in lines:
            p.positions.append(
                Position(
                    ticker=ticker,
                    shares=shares,
                    entry_price=entry,
                    entry_date=entry_date,
                    current_price=entry,
                )
            )
        return p

    def test_a_single_line_is_left_alone(self) -> None:
        p = self._book(("EFOR", 22.0, 20.11, "2026-05-08"))

        assert p.consolidate("EFOR") is False
        assert len(p.positions) == 1

    def test_two_lines_become_one(self) -> None:
        p = self._book(
            ("EFOR", 22.0, 20.11, "2026-05-08"),
            ("EFOR", 22.0, 20.87, "2026-05-08"),
        )

        assert p.consolidate("EFOR") is True
        assert len(p.positions) == 1
        assert p.positions[0].shares == pytest.approx(44.0)
        assert p.positions[0].entry_price == pytest.approx(20.49)

    def test_three_lines_become_one(self) -> None:
        p = self._book(
            ("X", 10.0, 10.0, "2026-05-01"),
            ("X", 10.0, 20.0, "2026-05-02"),
            ("X", 20.0, 30.0, "2026-05-03"),
        )

        p.consolidate("X")

        assert len(p.positions) == 1
        assert p.positions[0].shares == pytest.approx(40.0)
        # (10*10 + 10*20 + 20*30) / 40
        assert p.positions[0].entry_price == pytest.approx(22.5)
        assert p.positions[0].entry_date == "2026-05-01"

    def test_other_tickers_are_untouched(self) -> None:
        p = self._book(
            ("AAPL", 5.0, 100.0, "2026-05-01"),
            ("X", 10.0, 10.0, "2026-05-01"),
            ("X", 10.0, 20.0, "2026-05-02"),
            ("MSFT", 5.0, 200.0, "2026-05-01"),
        )

        p.consolidate("X")

        assert [x.ticker for x in p.positions] == ["AAPL", "X", "MSFT"]
        assert p.positions[0].shares == pytest.approx(5.0)
        assert p.positions[2].shares == pytest.approx(5.0)

    def test_the_merged_line_keeps_its_place(self) -> None:
        # Order is what the dashboard sorts from; consolidating must not
        # shuffle the book.
        p = self._book(
            ("AAPL", 5.0, 100.0, "2026-05-01"),
            ("X", 10.0, 10.0, "2026-05-01"),
            ("MSFT", 5.0, 200.0, "2026-05-01"),
            ("X", 10.0, 20.0, "2026-05-02"),
        )

        p.consolidate("X")

        assert [x.ticker for x in p.positions] == ["AAPL", "X", "MSFT"]

    def test_nav_is_unchanged_by_consolidating(self) -> None:
        p = self._book(
            ("X", 10.0, 10.0, "2026-05-01"),
            ("X", 10.0, 20.0, "2026-05-02"),
        )
        p.cash = 100.0
        before = p.total_nav

        p.consolidate("X")

        assert p.total_nav == pytest.approx(before)


# ---------------------------------------------------------------------------
# Unpriced holdings (audit CE-1a / PD-09 / SF-X4)
# ---------------------------------------------------------------------------
class TestUnpricedSince:
    """When a held name has gone long enough without a bar to be flagged.

    LEG sat in Klarman's book at its 26 August close for 78 days, marked
    every run, with nothing anywhere saying the number referred to a
    company that no longer listed. The rule is one place so the runner
    that sets the flag and the verifier that checks it cannot disagree.
    """

    AS_OF = date(2026, 8, 5)  # a Wednesday

    def test_the_threshold_is_five_sessions(self) -> None:
        assert UNPRICED_AFTER_SESSIONS == 5

    def test_six_sessions_without_a_bar_is_unpriced_from_the_first_missing_one(
        self,
    ) -> None:
        # Last bar Tue 28 July; 29 July is the first session with none.
        assert (
            unpriced_since_for(
                date(2026, 7, 28), as_of=self.AS_OF, entry_date="2026-07-01"
            )
            == "2026-07-29"
        )

    def test_five_sessions_is_not_yet_unpriced(self) -> None:
        # A long weekend plus a vendor hiccup must not raise the alarm.
        assert (
            unpriced_since_for(
                date(2026, 7, 29), as_of=self.AS_OF, entry_date="2026-07-01"
            )
            == ""
        )

    def test_no_bar_ever_is_unpriced_since_entry(self) -> None:
        assert (
            unpriced_since_for(None, as_of=self.AS_OF, entry_date="2026-07-01")
            == "2026-07-01"
        )

    def test_no_bar_and_no_entry_date_falls_back_to_the_run_date(self) -> None:
        assert (
            unpriced_since_for(None, as_of=self.AS_OF, entry_date="")
            == "2026-08-05"
        )


class TestUnpricedPersistence:
    def test_the_flag_round_trips(self, tmp_path: Path) -> None:
        p = LivePortfolio(agent="seth_klarman")
        p.positions.append(
            Position(
                ticker="LEG",
                shares=17.0,
                entry_price=9.66,
                entry_date="2026-08-16",
                current_price=9.2,
                unpriced_since="2026-08-27",
            )
        )
        p.save(directory=tmp_path)

        raw = json.loads((tmp_path / "seth_klarman.json").read_text())
        assert raw["positions"][0]["unpriced_since"] == "2026-08-27"
        loaded = LivePortfolio.load_or_seed("seth_klarman", directory=tmp_path)
        assert loaded.positions[0].unpriced_since == "2026-08-27"

    def test_a_book_written_before_the_flag_reads_as_priced(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "old.json").write_text(
            json.dumps(
                {
                    "agent": "old",
                    "positions": [
                        {
                            "ticker": "KO",
                            "shares": 1,
                            "entry_price": 60.0,
                            "entry_date": "2026-05-05",
                        }
                    ],
                }
            )
        )
        loaded = LivePortfolio.load_or_seed("old", directory=tmp_path)
        assert loaded.positions[0].unpriced_since == ""


# ---------------------------------------------------------------------------
# Stock splits (audit item 22 / CE-5)
# ---------------------------------------------------------------------------
class TestApplySplit:
    """The price cache is split-adjusted, the book's share count was
    not: the first split inside a holding window would have halved (or
    doubled) that line's value with nothing having happened to it."""

    @staticmethod
    def _book(shares: float = 10.0, entry: float = 100.0, mark: float = 120.0) -> LivePortfolio:
        p = LivePortfolio(agent="t", cash=500.0)
        p.positions.append(
            Position(
                ticker="NVDA",
                shares=shares,
                entry_price=entry,
                entry_date="2026-07-01",
                current_price=mark,
            )
        )
        return p

    def test_two_for_one_doubles_whole_shares_and_keeps_nav(self) -> None:
        p = self._book()
        nav_before = p.total_nav  # 500 + 10 x 120

        rec = p.apply_split("NVDA", ratio=2.0, ex_date="2026-08-05", price=60.0)
        p.mark_to_market({"NVDA": 60.0})

        assert rec is not None
        pos = p.positions[0]
        assert pos.shares == 20.0
        assert pos.entry_price == pytest.approx(50.0)
        assert rec.cash_in_lieu == 0.0
        assert p.total_nav == pytest.approx(nav_before)
        # The cost basis is the same money, restated per share.
        assert pos.cost_basis == pytest.approx(1_000.0)

    def test_three_for_two_floors_and_pays_cash_in_lieu(self) -> None:
        # 3 shares x 1.5 = 4.5: the broker holds whole shares, so 4
        # stay and the half share is paid out at the post-split price.
        p = self._book(shares=3.0, entry=90.0, mark=150.0)
        nav_before = p.total_nav

        rec = p.apply_split("NVDA", ratio=1.5, ex_date="2026-08-05", price=100.0)
        p.mark_to_market({"NVDA": 100.0})

        assert rec is not None
        assert p.positions[0].shares == 4.0
        assert p.positions[0].entry_price == pytest.approx(60.0)
        assert rec.cash_in_lieu == pytest.approx(50.0)
        assert p.cash == pytest.approx(550.0)
        assert rec.realized_pnl_usd == pytest.approx((100.0 - 60.0) * 0.5)
        assert p.total_nav == pytest.approx(nav_before)
        # Cash in lieu is not an order: no commission is charged.
        assert p.cumulative_costs == 0.0

    def test_it_is_applied_once_per_ex_date(self) -> None:
        p = self._book()
        p.apply_split("NVDA", ratio=2.0, ex_date="2026-08-05", price=60.0)

        again = p.apply_split("NVDA", ratio=2.0, ex_date="2026-08-05", price=60.0)

        assert again is None
        assert p.positions[0].shares == 20.0

    def test_without_a_fresh_price_the_mark_is_restated_too(self) -> None:
        # If no post-split quote arrives today, mark_to_market keeps the
        # last mark. That mark is pre-split, so it must be restated with
        # the shares or the line's value doubles.
        p = self._book()
        nav_before = p.total_nav

        p.apply_split("NVDA", ratio=2.0, ex_date="2026-08-05", price=None)
        p.mark_to_market({})

        assert p.positions[0].current_price == pytest.approx(60.0)
        assert p.total_nav == pytest.approx(nav_before)

    def test_a_reverse_split_below_one_share_closes_the_line_for_cash(self) -> None:
        p = self._book(shares=5.0, entry=2.0, mark=3.0)
        p.paid_dividends["NVDA"] = ["2026-07-15"]
        nav_before = p.total_nav

        rec = p.apply_split("NVDA", ratio=0.1, ex_date="2026-08-05", price=30.0)

        assert rec is not None and rec.shares_after == 0.0
        assert not p.has("NVDA")
        assert "NVDA" not in p.paid_dividends
        assert p.cash == pytest.approx(500.0 + 0.5 * 30.0)
        assert p.total_nav == pytest.approx(nav_before)

    def test_a_vendor_rounded_reverse_ratio_does_not_lose_a_share(self) -> None:
        # yfinance reports 1-for-3 as 0.333333; 3 shares must become 1,
        # not 0 shares and 0.999999 of a share in cash.
        p = self._book(shares=3.0, entry=1.0, mark=1.0)

        p.apply_split("NVDA", ratio=0.333333, ex_date="2026-08-05", price=3.0)

        assert p.positions[0].shares == 1.0

    def test_an_absent_ticker_is_a_no_op(self) -> None:
        p = self._book()

        assert p.apply_split("AAPL", ratio=4.0, ex_date="2026-08-05", price=1.0) is None
        assert p.cash == 500.0

    @pytest.mark.parametrize("ratio", [0.0, -2.0, float("nan"), float("inf")])
    def test_a_nonsense_ratio_is_refused(self, ratio: float) -> None:
        p = self._book()

        with pytest.raises(LivePortfolioError):
            p.apply_split("NVDA", ratio=ratio, ex_date="2026-08-05", price=60.0)
        assert p.positions[0].shares == 10.0

    def test_the_record_survives_a_round_trip_and_a_full_sale_clears_it(
        self, tmp_path: Path
    ) -> None:
        p = self._book()
        p.split_floor_date = "2026-08-04"
        p.apply_split("NVDA", ratio=2.0, ex_date="2026-08-05", price=60.0)
        p.save(directory=tmp_path)

        loaded = LivePortfolio.load_or_seed("t", directory=tmp_path)
        assert loaded.applied_splits == {"NVDA": ["2026-08-05"]}
        assert loaded.split_floor_date == "2026-08-04"

        loaded.sell("NVDA", price=60.0)
        assert loaded.applied_splits == {}


# ---------------------------------------------------------------------------
# Pruning settled-event records the book can no longer use (audit CE-3b)
# ---------------------------------------------------------------------------
class TestPruneEventHistory:
    """`paid_dividends` and `applied_splits` are meant to stay the size of
    the book. A line closed without `sell()` left its keys behind — 13
    of them across four committed books — and this is what removes
    them."""

    @staticmethod
    def _book(*lines: tuple[str, str]) -> LivePortfolio:
        p = LivePortfolio(agent="t", cash=1_000.0)
        p.positions = [
            Position(
                ticker=t, shares=5.0, entry_price=10.0, entry_date=d,
                current_price=10.0,
            )
            for t, d in lines
        ]
        return p

    def test_a_key_for_a_name_not_held_is_removed(self) -> None:
        p = self._book(("KEEP", "2026-05-06"))
        p.paid_dividends = {"KEEP": ["2026-06-15"], "SEB": ["2026-08-14"]}
        p.applied_splits = {"GONE": ["2026-07-01"]}

        removed = p.prune_event_history()

        assert p.paid_dividends == {"KEEP": ["2026-06-15"]}
        assert p.applied_splits == {}
        assert removed == {
            "paid_dividends": {"SEB": ["2026-08-14"]},
            "applied_splits": {"GONE": ["2026-07-01"]},
        }

    def test_an_ex_date_before_the_current_entry_is_removed(self) -> None:
        """REPX paid 2026-10-28 on a lot the flooring closed, and the
        name was bought back 2026-11-04. Settlement never looks before
        the entry date, so the old date is dead weight."""
        p = self._book(("REPX", "2026-11-04"))
        p.paid_dividends = {"REPX": ["2026-10-28"]}

        removed = p.prune_event_history()

        assert p.paid_dividends == {}
        assert removed["paid_dividends"] == {"REPX": ["2026-10-28"]}

    def test_an_ex_date_on_or_after_the_entry_is_kept(self) -> None:
        """Removing one of these would let settlement pay it a second
        time: it is the record that makes re-scanning safe."""
        p = self._book(("MRK", "2026-06-15T13:30:00+00:00"))
        p.paid_dividends = {"MRK": ["2026-06-15", "2026-09-15"]}
        p.applied_splits = {"MRK": ["2026-07-01"]}

        removed = p.prune_event_history()

        assert p.paid_dividends == {"MRK": ["2026-06-15", "2026-09-15"]}
        assert p.applied_splits == {"MRK": ["2026-07-01"]}
        assert removed == {"paid_dividends": {}, "applied_splits": {}}

    def test_a_lower_case_ticker_on_the_line_still_counts_as_held(self) -> None:
        # The maps are keyed upper-case; a line is not always.
        p = self._book(("brk.b", "2026-05-06"))
        p.paid_dividends = {"BRK.B": ["2026-06-15"]}

        p.prune_event_history()

        assert p.paid_dividends == {"BRK.B": ["2026-06-15"]}

    def test_a_line_with_no_entry_date_keeps_every_date(self) -> None:
        # No date to compare with is no evidence a payment is stale.
        p = self._book(("OLD", ""))
        p.paid_dividends = {"OLD": ["2026-06-15"]}

        p.prune_event_history()

        assert p.paid_dividends == {"OLD": ["2026-06-15"]}

    def test_cash_and_nav_do_not_move(self) -> None:
        p = self._book(("KEEP", "2026-11-04"))
        p.paid_dividends = {"KEEP": ["2026-10-01"], "SEB": ["2026-08-14"]}
        p.mark_to_market({})
        before = (p.cash, p.total_nav, p.cumulative_dividends)

        p.prune_event_history()

        assert (p.cash, p.total_nav, p.cumulative_dividends) == before

    def test_an_empty_book_is_not_an_error(self) -> None:
        p = LivePortfolio(agent="t")

        assert p.prune_event_history() == {
            "paid_dividends": {}, "applied_splits": {},
        }
