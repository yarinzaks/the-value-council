"""Graham's own exit rules, as shadow verdicts (audit SF-X1, item 13b).

Playbook §7.1 names when Graham sells and §7.2 names when he does not.
None of it ran: the strategy ignored ``held`` and the engine sold a
name only for leaving the day's target list. These tests pin the rules
as :class:`~core.live.doctrine_exits.ExitVerdict` objects published on
``last_exit_decisions``:

* trigger 1 — fair value: a Defensive holding at its Graham Number, a
  Net-Net at 100% of NCAV;
* trigger 2 — a Net-Net held two years without reaching NCAV;
* trigger 3 — fundamentals deteriorate: earnings turn negative, the
  current ratio drops below 1.5, the dividend is cut, book value goes;
* retention — a held name with none of those is kept, even when it
  slipped out of the top of the ranking or no longer passes the
  stricter *entry* screen.

And in shadow — Graham's mode — none of this changes a weight.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from agents.graham import exits as graham_exits
from agents.graham.net_net import BenjaminGraham
from core.backtest.decision_logger import DecisionLogger
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.backtest.strategy_runner import HeldPosition
from core.live.agent_adapter import GrahamLive
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
    current_assets: float = 3_000_000_000,
    current_liabilities: float = 1_000_000_000,
    total_liabilities: float = 1_500_000_000,
    total_equity: float = 2_000_000_000,
    shares: float = 100_000_000,
    long_term_debt: float = 400_000_000,
    net_income: float = 200_000_000,
    eps: float | None = 2.0,
    dividends_paid: float | None = -50_000_000,
) -> PointInTimeFinancials:
    """A Defensive-grade balance sheet.

    Per share: EPS $2.00, book $20.00, so the Graham Number is
    sqrt(22.5 x 2 x 20) = $30.00 and NCAV is (3.0bn - 1.5bn) / 100m =
    $15.00. Current ratio 3.0; long-term debt under working capital.
    """
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
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        total_liabilities=total_liabilities,
        total_equity=total_equity,
        shares_outstanding=shares,
        total_debt=long_term_debt,
        long_term_debt=long_term_debt,
        net_income=net_income,
        eps_diluted=eps,
        dividends_paid=dividends_paid,
    )


GRAHAM_NUMBER = 30.0
NCAV = 15.0


def _held(
    ticker: str, *, entry: float, days: int, mark: float | None = None
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
    return graham_exits.decide(position, fin, price, as_of=AS_OF)


# ---------------------------------------------------------------------------
# The two tests the audit names (report section 6, item 13)
# ---------------------------------------------------------------------------
def test_held_name_that_still_qualifies_is_retained_when_it_slips_in_rank() -> None:
    """Three Defensive names pass; the strategy keeps room for two.

    HELD is the most expensive of the three (P/E x P/B 18.0 against
    10.0 and 12.0), so it is off the target list — and still below its
    Graham Number with every fundamental intact. Graham keeps it.
    """
    fins = {
        "CHEAP": _fin("CHEAP"),
        "MID": _fin("MID"),
        "HELD": _fin("HELD"),
    }
    # Book $20, EPS $2: price 20 -> 10.0 x 1.0 = 10; 22 -> 12.1; 27 -> 18.2.
    prices = {"CHEAP": 20.0, "MID": 22.0, "HELD": 27.0}
    strategy = BenjaminGraham(portfolio_size=2, min_market_cap=1_000_000)
    held = {"HELD": _held("HELD", entry=21.0, days=120, mark=27.0)}

    weights = strategy.select(
        AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
    )

    assert "HELD" not in weights  # it did slip in rank
    assert strategy.last_exit_decisions == [
        ExitVerdict(
            "HELD",
            sell=False,
            reason=(
                "Defensive: price $27.00 below the Graham Number $30.00 "
                "(10% margin); fundamentals intact"
            ),
        )
    ]


def test_exit_reason_in_journal_names_the_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real adapter, the real runner, shadow mode.

    RICH ran to $31 against a $30 Graham Number. The doctrine journal
    says SELL and names the Graham Number; the engine's own action is
    recorded beside it and — shadow — is whatever it would have been.
    """
    fins = {"CHEAP": _fin("CHEAP"), "RICH": _fin("RICH")}
    prices: dict[str, float | None] = {"CHEAP": 20.0, "RICH": 31.0}
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
    adapter = GrahamLive(BenjaminGraham(portfolio_size=30, min_market_cap=1_000_000))
    assert adapter.exit_mode == "shadow"

    book = LivePortfolio(agent=adapter.name, cash=2_000.0)
    book.last_rebalance_date = "2026-08-03"
    book.positions.append(
        Position(
            ticker="RICH",
            shares=10.0,
            entry_price=19.0,
            entry_date=(AS_OF - timedelta(days=200)).isoformat(),
            current_price=31.0,
            why_en="seeded",
            why_he="seeded",
        )
    )
    book.save(directory=runner.portfolio_dir)

    runner._run_one(
        adapter,
        AS_OF,
        list(fins),
        dict(prices),
        dict(fins),
    )

    rows: list[dict[str, Any]] = []
    for path in sorted((tmp_path / "doctrine_exits" / adapter.name).glob("*.json")):
        payload = json.loads(path.read_text())
        rows.extend(payload if isinstance(payload, list) else [payload])
    assert [r["ticker"] for r in rows] == ["RICH"]
    row = rows[0]
    assert row["decision"] == "SELL"
    assert row["exit_trigger"] == (
        "Defensive: price $31.00 reached the Graham Number $30.00"
    )
    assert "Graham Number" in row["criteria_met"][0]
    assert row["market_conditions"]["exit_mode"] == "shadow"


# ---------------------------------------------------------------------------
# Trigger 1 — fair value
# ---------------------------------------------------------------------------
class TestFairValue:
    def test_a_defensive_holding_below_its_graham_number_is_kept(self) -> None:
        v = _decide(_fin("X"), 24.0, _held("X", entry=20.0, days=60))
        assert v is not None and v.sell is False
        assert "Graham Number $30.00" in v.reason

    def test_a_defensive_holding_at_its_graham_number_is_sold(self) -> None:
        v = _decide(_fin("X"), 30.0, _held("X", entry=20.0, days=60))
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason="Defensive: price $30.00 reached the Graham Number $30.00",
        )

    def test_a_pe_above_the_entry_ceiling_is_not_a_sell(self) -> None:
        """Entry wants P/E <= 15; §7.2 does not list P/E as a sell.

        Price $26 on $2 EPS is P/E 13 — fine. Halve EPS to $1.00 with
        book unchanged: the Graham Number is sqrt(22.5 x 1 x 20) =
        $21.21, so $20 is P/E 20 (fails entry) and still below it.
        """
        fin = _fin("X", eps=1.0, net_income=100_000_000)
        v = _decide(fin, 20.0, _held("X", entry=18.0, days=60))
        assert v is not None and v.sell is False

    def test_a_net_net_holding_at_full_ncav_is_sold(self) -> None:
        # Entry $9 on NCAV $15 is P/NCAV 0.60: bought as a Net-Net.
        v = _decide(_fin("N"), 15.5, _held("N", entry=9.0, days=200))
        assert v == ExitVerdict(
            "N",
            sell=True,
            reason="Net-Net: price $15.50 reached NCAV $15.00/share (P/NCAV 1.03)",
        )

    def test_a_net_net_below_ncav_inside_two_years_is_kept(self) -> None:
        v = _decide(_fin("N"), 12.0, _held("N", entry=9.0, days=400))
        assert v is not None and v.sell is False
        assert v.reason == (
            "Net-Net: P/NCAV 0.80, below NCAV $15.00/share; "
            "held 400 of 730 days"
        )


# ---------------------------------------------------------------------------
# Trigger 2 — two years without reaching fair value
# ---------------------------------------------------------------------------
class TestTwoYearRule:
    def test_a_net_net_held_two_years_below_ncav_is_sold(self) -> None:
        v = _decide(_fin("N"), 12.0, _held("N", entry=9.0, days=730))
        assert v == ExitVerdict(
            "N",
            sell=True,
            reason=(
                "Net-Net: held 730 days without reaching NCAV "
                "$15.00/share (P/NCAV 0.80) — the two-year rule"
            ),
        )

    def test_one_day_short_of_two_years_is_kept(self) -> None:
        v = _decide(_fin("N"), 12.0, _held("N", entry=9.0, days=729))
        assert v is not None and v.sell is False

    def test_the_two_year_rule_is_for_net_nets_only(self) -> None:
        """§7.1 trigger 2 is written about Net-Nets; a Defensive name
        below its Graham Number after three years is kept."""
        v = _decide(_fin("X"), 24.0, _held("X", entry=20.0, days=3 * 365))
        assert v is not None and v.sell is False


# ---------------------------------------------------------------------------
# Trigger 3 — fundamentals deteriorate, regardless of price
# ---------------------------------------------------------------------------
class TestDeterioration:
    def test_negative_earnings_sell_even_far_below_fair_value(self) -> None:
        fin = _fin("X", net_income=-10_000_000, eps=-0.10)
        v = _decide(fin, 12.0, _held("X", entry=20.0, days=60))
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason="Defensive: earnings turned negative (trailing net income $-10,000,000)",
        )

    def test_current_ratio_below_one_and_a_half_sells(self) -> None:
        fin = _fin("X", current_liabilities=2_200_000_000)  # CR 1.36
        v = _decide(fin, 20.0, _held("X", entry=20.0, days=60))
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason="Defensive: current ratio 1.36 below 1.5",
        )

    def test_a_current_ratio_between_entry_and_exit_floors_is_kept(self) -> None:
        """Entry wants 2.0; the playbook's exit floor is 1.5."""
        fin = _fin("X", current_liabilities=1_700_000_000)  # CR 1.76
        v = _decide(fin, 20.0, _held("X", entry=20.0, days=60))
        assert v is not None and v.sell is False

    def test_a_cut_dividend_sells_a_defensive_holding(self) -> None:
        fin = _fin("X", dividends_paid=0.0)
        v = _decide(fin, 20.0, _held("X", entry=20.0, days=60))
        assert v == ExitVerdict(
            "X", sell=True, reason="Defensive: dividend cut (none paid)"
        )

    def test_an_unreported_dividend_is_not_read_as_a_cut(self) -> None:
        """Absence of a tag is not evidence of a cut (audit FUND-4)."""
        fin = _fin("X", dividends_paid=None)
        v = _decide(fin, 20.0, _held("X", entry=20.0, days=60))
        assert v is not None and v.sell is False

    def test_book_value_gone_sells_a_defensive_holding(self) -> None:
        fin = _fin("X", total_equity=-5_000_000)
        v = _decide(fin, 20.0, _held("X", entry=20.0, days=60))
        assert v == ExitVerdict(
            "X", sell=True, reason="Defensive: book value no longer positive"
        )

    def test_negative_earnings_sell_a_net_net_too(self) -> None:
        fin = _fin("N", net_income=-1.0, eps=-0.01)
        v = _decide(fin, 10.0, _held("N", entry=9.0, days=60))
        assert v is not None and v.sell is True
        assert v.reason.startswith("Net-Net: earnings turned negative")


# ---------------------------------------------------------------------------
# What does not produce a verdict at all
# ---------------------------------------------------------------------------
class TestNoOpinion:
    def test_no_fundamentals_gives_no_verdict(self) -> None:
        assert _decide(None, 20.0, _held("X", entry=20.0, days=60)) is None

    def test_no_price_gives_no_verdict(self) -> None:
        assert _decide(_fin("X"), None, _held("X", entry=20.0, days=60)) is None

    def test_an_uncomputable_graham_number_gives_no_verdict(self) -> None:
        """Positive earnings but no EPS tag: the fair value is unknown,
        and a keep would hold the name on no evidence."""
        fin = _fin("X", eps=None)
        assert _decide(fin, 20.0, _held("X", entry=20.0, days=60)) is None

    def test_a_price_decline_alone_is_never_a_sell(self) -> None:
        """§7.2: never sell because the stock dropped."""
        v = _decide(_fin("X"), 8.0, _held("X", entry=20.0, days=60))
        assert v is not None and v.sell is False


# ---------------------------------------------------------------------------
# The strategy wiring
# ---------------------------------------------------------------------------
class TestStrategyPublishesVerdicts:
    def _universe(self) -> tuple[dict[str, PointInTimeFinancials], dict[str, float]]:
        fins = {"A": _fin("A"), "B": _fin("B"), "C": _fin("C")}
        prices = {"A": 20.0, "B": 22.0, "C": 31.0}
        return fins, prices

    def test_nothing_held_publishes_nothing(self) -> None:
        fins, prices = self._universe()
        strategy = BenjaminGraham(portfolio_size=5, min_market_cap=1_000_000)
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []

    def test_weights_do_not_depend_on_what_is_held(self) -> None:
        """Shadow: the verdicts are journaled, the target list is not
        touched. A held name the doctrine keeps is not added back, and
        one it sells is not removed."""
        fins, prices = self._universe()
        held = {
            "A": _held("A", entry=15.0, days=60, mark=20.0),
            "C": _held("C", entry=18.0, days=60, mark=31.0),
        }
        bare = BenjaminGraham(portfolio_size=5, min_market_cap=1_000_000)
        holding = BenjaminGraham(portfolio_size=5, min_market_cap=1_000_000)
        w_bare = bare.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        w_held = holding.select(
            AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
        )
        assert w_held == w_bare
        verdicts = {v.ticker: v.sell for v in holding.last_exit_decisions}
        assert verdicts == {"A": False, "C": True}

    def test_verdicts_are_published_when_nothing_qualifies(self) -> None:
        """An empty screen is when a held name's verdict matters most."""
        fins = {"C": _fin("C")}
        strategy = BenjaminGraham(portfolio_size=5, min_market_cap=1_000_000)
        weights = strategy.select(
            AS_OF,
            ["C"],
            _lookup({"C": 31.0}),
            _lookup(fins),
            held={"C": _held("C", entry=18.0, days=60, mark=31.0)},
        )
        assert weights == {}
        assert [v.sell for v in strategy.last_exit_decisions] == [True]

    def test_a_held_name_outside_the_universe_is_judged_from_its_lookup(self) -> None:
        fins, prices = self._universe()
        fins["OUT"] = _fin("OUT")
        prices["OUT"] = 24.0
        strategy = BenjaminGraham(portfolio_size=5, min_market_cap=1_000_000)
        strategy.select(
            AS_OF,
            ["A", "B", "C"],
            _lookup(prices),
            _lookup(fins),
            held={"OUT": _held("OUT", entry=20.0, days=60)},
        )
        assert [(v.ticker, v.sell) for v in strategy.last_exit_decisions] == [
            ("OUT", False)
        ]

    def test_the_previous_days_verdicts_do_not_survive_a_new_select(self) -> None:
        fins, prices = self._universe()
        strategy = BenjaminGraham(portfolio_size=5, min_market_cap=1_000_000)
        strategy.select(
            AS_OF,
            list(fins),
            _lookup(prices),
            _lookup(fins),
            held={"C": _held("C", entry=18.0, days=60)},
        )
        assert strategy.last_exit_decisions
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []
