"""Tests for LivePortfolio — buy/sell accounting, NAV, persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.live.portfolio import (
    DEFAULT_COST_BPS,
    DEFAULT_INITIAL_CASH,
    LivePortfolio,
    LivePortfolioError,
    Position,
    WatchEntry,
    now_iso,
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
        # 5000 / (1 + 0.001) = 4995.005 max notional, / 200 = 24.975025
        # shares. Whole-share rounding used to buy 24 and strand $195.
        assert trade.shares == pytest.approx(24.975025)
        assert trade.gross_value == pytest.approx(24.975025 * 200.0)
        assert trade.cost_paid == pytest.approx(
            24.975025 * 200.0 * DEFAULT_COST_BPS
        )
        # The whole $5,000 target is deployed, to within rounding.
        assert p.cash == pytest.approx(5_000.0, abs=0.01)
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

    def test_a_target_below_one_share_still_buys(self) -> None:
        # This used to raise: a $50 target on a $100 stock bought nothing
        # and the money sat idle. Fractional shares make it a half share.
        p = LivePortfolio(agent="test", cash=10_000)
        trade = p.buy(
            "X", target_dollars=50, price=100, entry_date="x", why_en="", why_he=""
        )
        assert trade.shares == pytest.approx(0.4995, abs=1e-4)

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
        # Two fractional lots: 2000/1.001/100 = 19.980020 shares at 100,
        # then 2000/1.001/120 = 16.650017 at 120.
        lot1, lot2 = 2_000 / 1.001 / 100, 2_000 / 1.001 / 120
        avg = (lot1 * 100 + lot2 * 120) / (lot1 + lot2)
        assert p.positions[0].entry_price == pytest.approx(avg, rel=1e-6)
        assert p.positions[0].shares == pytest.approx(lot1 + lot2, rel=1e-6)


# ---------------------------------------------------------------------------
# Sell
# ---------------------------------------------------------------------------
class TestSell:
    def test_sell_full_liquidation(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("AAPL", target_dollars=2_000, price=100, entry_date="2026-04-29", why_en="", why_he="")
        cash_before_sell = p.cash
        # Sell at 110 — gain of 10/share on 19.980020 shares.
        shares = 2_000 / 1.001 / 100
        trade = p.sell("AAPL", price=110.0)
        assert trade.shares == pytest.approx(shares, rel=1e-6)
        assert trade.realized_pnl_usd == pytest.approx(shares * (110 - 100))
        # Cash should be cash_before_sell + proceeds - cost
        gross = shares * 110.0
        assert p.cash == pytest.approx(cash_before_sell + gross - gross * DEFAULT_COST_BPS)
        assert len(p.positions) == 0

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
# NAV / mark-to-market
# ---------------------------------------------------------------------------
class TestMarkToMarket:
    def test_total_nav_with_positions(self) -> None:
        p = LivePortfolio(agent="test", cash=10_000)
        p.buy("AAPL", target_dollars=2_000, price=100, entry_date="d", why_en="", why_he="")
        # 19.980020 shares at 100 = 1998.00 (cost 2.00); the full $2,000
        # target is deployed. Mark up to 120.
        shares = 2_000 / 1.001 / 100
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
        # initial 10k → now ~ (cash 8098.10) + (19*200) = 11898 — ~+19% return
        assert p.cumulative_return_pct > 15.0
