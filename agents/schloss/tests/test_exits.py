"""Schloss's own exit rules, as shadow verdicts (audit SF-X1, item 13c).

Playbook §7 names when Schloss sells and when he does not. None of it
ran: the strategy ignored ``held`` and the engine sold a name only for
leaving the day's target list. These tests pin the rules as
:class:`~core.live.doctrine_exits.ExitVerdict` objects published on
``last_exit_decisions``:

* §7.2 — the balance sheet breaks: debt above 100% of equity, tangible
  book gone — sell at any price;
* §7.1 — price above 1.2x tangible book: sell, whatever the gain;
* §7.1 + §7.3 — up 40% or more: reevaluate against book; sell unless
  still at the entry discount (0.8x or under);
* retention — anything else is kept, including a name that fell in
  price, posted a loss, or slipped out of the cheapest N.

And in shadow — Schloss's mode — none of this changes a weight.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from agents.schloss import exits as schloss_exits
from agents.schloss.deep_value import WalterSchloss
from core.backtest.decision_logger import DecisionLogger
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.backtest.strategy_runner import HeldPosition
from core.live.agent_adapter import SchlossLive
from core.live.doctrine_exits import ExitVerdict
from core.live.portfolio import LivePortfolio, Position
from core.live.runner import DailyRunner

AS_OF = date(2026, 8, 5)


class _StubLookup:
    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = dict(values)

    def get(self, ticker: str) -> Any:
        return self._values.get(ticker.upper())


def _lookup(values: Mapping[str, Any]) -> Any:
    """A dict standing in for the price or fundamentals lookup.

    Typed ``Any`` because ``select`` names the concrete loader-backed
    classes; this has their one method, ``get``.
    """
    return _StubLookup(values)


def _fin(
    ticker: str,
    *,
    total_equity: float = 1_000_000_000,
    shares: float | None = 100_000_000,
    total_debt: float | None = 200_000_000,
    goodwill: float | None = None,
    net_income: float = 50_000_000,
) -> PointInTimeFinancials:
    """A Schloss-grade balance sheet: tangible book $10.00/share, D/E 0.20."""
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=AS_OF,
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-Q",
            filing_date=AS_OF - timedelta(days=30),
            period_of_report=AS_OF - timedelta(days=60),
            accession_number=f"a-{ticker}",
        ),
        total_assets=2_000_000_000,
        total_liabilities=1_000_000_000,
        current_liabilities=300_000_000,
        total_equity=total_equity,
        shares_outstanding=shares,
        total_debt=total_debt,
        long_term_debt=total_debt,
        goodwill=goodwill,
        net_income=net_income,
    )


TANGIBLE_BOOK = 10.0


def _held(
    ticker: str, *, entry: float, days: int = 120, mark: float | None = None
) -> HeldPosition:
    return HeldPosition(
        ticker=ticker,
        shares=10.0,
        entry_price=entry,
        entry_date=AS_OF - timedelta(days=days),
        current_price=mark if mark is not None else entry,
    )


def _decide(
    fin: PointInTimeFinancials | None,
    price: float | None,
    position: HeldPosition,
) -> ExitVerdict | None:
    return schloss_exits.decide(position, fin, price)


def _strategy(portfolio_size: int) -> WalterSchloss:
    return WalterSchloss(
        portfolio_size=portfolio_size, min_market_cap=1_000_000, min_years_public=0
    )


# ---------------------------------------------------------------------------
# The two tests the audit names (report section 6, item 13)
# ---------------------------------------------------------------------------
def test_held_name_that_still_qualifies_is_retained_when_it_slips_in_rank() -> None:
    """Three names pass the screen; the strategy keeps room for two.

    HELD is the dearest of the three (P/B 0.70 against 0.50 and 0.60),
    so it is off the target list — and it is up 17% from cost, short of
    the 40% target, at 0.70x book with a sound balance sheet. Schloss
    keeps it.
    """
    fins = {"CHEAP": _fin("CHEAP"), "MID": _fin("MID"), "HELD": _fin("HELD")}
    prices = {"CHEAP": 5.0, "MID": 6.0, "HELD": 7.0}
    strategy = _strategy(2)
    held = {"HELD": _held("HELD", entry=6.0, mark=7.0)}

    weights = strategy.select(
        AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
    )

    assert "HELD" not in weights  # it did slip in rank
    assert strategy.last_exit_decisions == [
        ExitVerdict(
            "HELD",
            sell=False,
            reason=(
                "up 17% from cost $6.00; 0.70x tangible book $10.00/share; "
                "balance sheet intact"
            ),
        )
    ]


def test_exit_reason_in_journal_names_the_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real adapter, the real runner, shadow mode.

    RICH ran to $13 on a $10 tangible book (1.30x). The doctrine journal
    says SELL and names the 1.2x line; the engine's own action is
    recorded beside it and — shadow — is whatever it would have been.
    """
    fins = {"CHEAP": _fin("CHEAP"), "RICH": _fin("RICH")}
    prices: dict[str, float | None] = {"CHEAP": 5.0, "RICH": 13.0}
    monkeypatch.setattr("core.live.runner.save_snapshot", lambda snap: None)

    class _Prices:
        def get_price_on(  # type: ignore[no-untyped-def]
            self, ticker, as_of, *, force_refresh=False
        ):
            return prices.get(ticker)

        def cached_range(self, ticker):  # type: ignore[no-untyped-def]
            return None

    runner = DailyRunner(
        market="US",
        adapters=[],
        portfolio_dir=tmp_path / "portfolios",
        published_dir=tmp_path / "published",
        ledger_dir=tmp_path / "trades",
        price_loader=_Prices(),  # type: ignore[arg-type]
        universe=object(),  # type: ignore[arg-type]
        pit_loader=object(),  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
        decision_logger=DecisionLogger(root=tmp_path / "decisions"),
        exit_verdict_logger=DecisionLogger(root=tmp_path / "doctrine_exits"),
    )
    adapter = SchlossLive(_strategy(30))
    assert adapter.exit_mode == "shadow"

    book = LivePortfolio(agent=adapter.name, cash=2_000.0)
    book.last_rebalance_date = "2026-08-03"
    book.positions.append(
        Position(
            ticker="RICH",
            shares=10.0,
            entry_price=6.0,
            entry_date=(AS_OF - timedelta(days=200)).isoformat(),
            current_price=13.0,
            why_en="seeded",
            why_he="seeded",
        )
    )
    book.save(directory=runner.portfolio_dir)

    runner._run_one(adapter, AS_OF, list(fins), dict(prices), dict(fins))

    rows: list[dict[str, Any]] = []
    for path in sorted((tmp_path / "doctrine_exits" / adapter.name).glob("*.json")):
        payload = json.loads(path.read_text())
        rows.extend(payload if isinstance(payload, list) else [payload])
    assert [r["ticker"] for r in rows] == ["RICH"]
    row = rows[0]
    assert row["decision"] == "SELL"
    assert row["exit_trigger"] == (
        "price $13.00 is 1.30x tangible book $10.00/share, "
        "above the 1.2x full-exit line"
    )
    assert "1.2x full-exit line" in row["criteria_met"][0]
    assert row["market_conditions"]["exit_mode"] == "shadow"


# ---------------------------------------------------------------------------
# §7.1 — the gain target, reevaluated against book (§7.3)
# ---------------------------------------------------------------------------
class TestGainTarget:
    def test_up_forty_percent_and_near_book_is_sold(self) -> None:
        # Cost $6.50, price $9.10: +40% exactly, at 0.91x book.
        v = _decide(_fin("X"), 9.10, _held("X", entry=6.50))
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "up 40% from cost $6.50, past the 40% target, and at "
                "0.91x tangible book $10.00/share no longer 0.8x or under"
            ),
        )

    def test_just_short_of_forty_percent_is_kept(self) -> None:
        # +39.85%: under the bottom of the 40-60% range.
        v = _decide(_fin("X"), 9.09, _held("X", entry=6.50))
        assert v is not None and v.sell is False

    def test_up_fifty_percent_but_still_deep_below_book_is_kept(self) -> None:
        """§7.3: "If still significantly below book, hold." Cost $5,
        price $7.50 (+50%) is still 0.75x book — the entry discount."""
        v = _decide(_fin("X"), 7.50, _held("X", entry=5.00))
        assert v == ExitVerdict(
            "X",
            sell=False,
            reason=(
                "up 50% from cost $5.00, past the 40% target, but still "
                "0.75x tangible book $10.00/share — reevaluated, still well "
                "below book"
            ),
        )

    def test_at_exactly_the_entry_discount_the_gain_is_held(self) -> None:
        v = _decide(_fin("X"), 8.00, _held("X", entry=5.00))
        assert v is not None and v.sell is False


# ---------------------------------------------------------------------------
# §7.1 — fully exit above 1.2x book
# ---------------------------------------------------------------------------
class TestFullExitAboveBook:
    def test_above_one_point_two_times_book_sells_even_without_the_gain(self) -> None:
        """Bought at $11 (a name re-rated by a write-down of book): at
        $12.50 it is up 14%, under the target — and 1.25x book."""
        v = _decide(_fin("X"), 12.50, _held("X", entry=11.00))
        assert v is not None and v.sell is True
        assert "1.25x tangible book" in v.reason

    def test_exactly_one_point_two_times_book_is_not_above_it(self) -> None:
        # "exceeds 1.2x": $12.00 on $10 is 1.20x, not above. +9%: keep.
        v = _decide(_fin("X"), 12.00, _held("X", entry=11.00))
        assert v is not None and v.sell is False

    def test_book_is_tangible_book(self) -> None:
        """$300m goodwill on $1bn equity: tangible book $7.00/share.
        $9 is 0.90x stated book but 1.29x tangible — sell."""
        fin = _fin("X", goodwill=300_000_000)
        v = _decide(fin, 9.00, _held("X", entry=8.50))
        assert v is not None and v.sell is True
        assert "1.29x tangible book $7.00/share" in v.reason


# ---------------------------------------------------------------------------
# §7.2 — fundamentals deteriorate, at any price
# ---------------------------------------------------------------------------
class TestDeterioration:
    def test_debt_above_equity_sells_even_at_a_loss(self) -> None:
        fin = _fin("X", total_debt=1_100_000_000)  # D/E 1.10
        v = _decide(fin, 3.00, _held("X", entry=6.00))
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason="fundamentals: debt rose above 100% of equity (D/E 1.10)",
        )

    def test_debt_at_exactly_equity_is_not_above_it(self) -> None:
        fin = _fin("X", total_debt=1_000_000_000)  # D/E 1.00
        v = _decide(fin, 6.00, _held("X", entry=6.00))
        assert v is not None and v.sell is False

    def test_tangible_book_gone_sells(self) -> None:
        fin = _fin("X", goodwill=1_200_000_000)  # tangible equity -$200m
        v = _decide(fin, 4.00, _held("X", entry=6.00))
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason="fundamentals: tangible book value no longer positive ($-200,000,000)",
        )

    def test_unreported_debt_is_not_read_as_a_breach(self) -> None:
        """Absence of a tag is not evidence (audit FUND-4)."""
        fin = _fin("X", total_debt=None)
        v = _decide(fin, 6.00, _held("X", entry=6.00))
        assert v is not None and v.sell is False


# ---------------------------------------------------------------------------
# §7.2 / §7.4 — what is never a sell
# ---------------------------------------------------------------------------
class TestNeverASell:
    def test_a_price_decline_alone_is_never_a_sell(self) -> None:
        """§7.2: "NOT for normal price decline alone — he would buy more"."""
        v = _decide(_fin("X"), 3.00, _held("X", entry=6.00))
        assert v == ExitVerdict(
            "X",
            sell=False,
            reason=(
                "down 50% from cost $6.00; 0.30x tangible book $10.00/share; "
                "balance sheet intact"
            ),
        )

    def test_a_loss_year_is_not_a_sell(self) -> None:
        """Schloss tolerated occasional losses; §7.2 does not list one."""
        fin = _fin("X", net_income=-20_000_000)
        v = _decide(fin, 6.00, _held("X", entry=6.00))
        assert v is not None and v.sell is False


# ---------------------------------------------------------------------------
# What does not produce a verdict at all
# ---------------------------------------------------------------------------
class TestNoOpinion:
    def test_no_fundamentals_gives_no_verdict(self) -> None:
        assert _decide(None, 6.0, _held("X", entry=6.0)) is None

    def test_no_price_gives_no_verdict(self) -> None:
        assert _decide(_fin("X"), None, _held("X", entry=6.0)) is None

    def test_no_share_count_gives_no_verdict(self) -> None:
        """Book per share, and so every §7.1 test, is unknown."""
        assert _decide(_fin("X", shares=None), 6.0, _held("X", entry=6.0)) is None

    def test_a_broken_balance_sheet_still_sells_without_a_share_count(self) -> None:
        """§7.2 needs no per-share figure."""
        fin = _fin("X", shares=None, total_debt=1_500_000_000)
        v = _decide(fin, 6.0, _held("X", entry=6.0))
        assert v is not None and v.sell is True

    def test_an_unusable_cost_basis_gives_no_verdict(self) -> None:
        assert _decide(_fin("X"), 6.0, _held("X", entry=0.0)) is None


# ---------------------------------------------------------------------------
# The strategy wiring
# ---------------------------------------------------------------------------
class TestStrategyPublishesVerdicts:
    def _universe(self) -> tuple[dict[str, PointInTimeFinancials], dict[str, float]]:
        fins = {"A": _fin("A"), "B": _fin("B"), "C": _fin("C")}
        prices = {"A": 5.0, "B": 6.0, "C": 7.0}
        return fins, prices

    def test_nothing_held_publishes_nothing(self) -> None:
        fins, prices = self._universe()
        strategy = _strategy(5)
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []

    def test_weights_do_not_depend_on_what_is_held(self) -> None:
        """Shadow: the verdicts are journaled, the target list is not
        touched. A held name the doctrine sells is not removed."""
        fins, prices = self._universe()
        prices["D"] = 9.0  # 0.90x book: off the screen (P/B >= 0.75)
        fins["D"] = _fin("D")
        held = {
            "A": _held("A", entry=4.5, mark=5.0),  # +11%: keep
            "B": _held("B", entry=4.0, mark=6.0),  # +50% at 0.60x: keep
            "D": _held("D", entry=6.0, mark=9.0),  # +50% at 0.90x: sell
        }
        bare = _strategy(5)
        holding = _strategy(5)
        w_bare = bare.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        w_held = holding.select(
            AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
        )
        assert w_held == w_bare
        assert set(w_held) == {"A", "B", "C"}  # B is kept on, not added
        verdicts = {v.ticker: v.sell for v in holding.last_exit_decisions}
        assert verdicts == {"A": False, "B": False, "D": True}

    def test_verdicts_are_published_when_nothing_qualifies(self) -> None:
        """An empty screen is when a held name's verdict matters most.
        $13 is 1.30x book: off the screen, and a sell."""
        strategy = _strategy(5)
        weights = strategy.select(
            AS_OF,
            ["C"],
            _lookup({"C": 13.0}),
            _lookup({"C": _fin("C")}),
            held={"C": _held("C", entry=6.0, mark=13.0)},
        )
        assert weights == {}
        assert [v.sell for v in strategy.last_exit_decisions] == [True]

    def test_a_held_name_outside_the_universe_is_judged_from_its_lookup(self) -> None:
        fins, prices = self._universe()
        fins["OUT"] = _fin("OUT")
        prices["OUT"] = 6.0
        strategy = _strategy(5)
        strategy.select(
            AS_OF,
            ["A", "B", "C"],
            _lookup(prices),
            _lookup(fins),
            held={"OUT": _held("OUT", entry=6.0)},
        )
        assert [(v.ticker, v.sell) for v in strategy.last_exit_decisions] == [
            ("OUT", False)
        ]

    def test_the_previous_days_verdicts_do_not_survive_a_new_select(self) -> None:
        fins, prices = self._universe()
        strategy = _strategy(5)
        strategy.select(
            AS_OF,
            list(fins),
            _lookup(prices),
            _lookup(fins),
            held={"C": _held("C", entry=6.0)},
        )
        assert strategy.last_exit_decisions
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []
