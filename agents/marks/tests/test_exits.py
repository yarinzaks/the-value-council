"""Marks's own exit rules, as shadow verdicts (audit SF-X1, item 13i).

Playbook §7: Marks scales out of a position as the price closes on
fair value — trim 25% as it approaches, another 25% when it reaches it,
out completely once it is past fair value with the cycle hot — and
sells when the thesis breaks. §7.3 lists what is not a trigger:
volatility, earnings noise, generic downturns when the cycle is mid or
early. None of it ran: ``HowardMarks.select`` ignored ``held`` and the
engine sold a Marks name only for leaving the day's target list (44
such exits since 2026-08-05). These tests pin the rules as
:class:`~core.live.doctrine_exits.ExitVerdict` objects published on
``last_exit_decisions``:

* trigger 4, first and at any price — book equity no longer positive,
  a full-year loss, or D/E above the entry gate's 1.0;
* triggers 1+2 / §7.2's last rung — price at or past fair value with
  the cycle Warm or Hot sells;
* retention — anything else is kept, including both 25% trim rungs (a
  trim is not a keep-or-sell verdict) and a name that slipped off
  today's list.

Fair value is the entry screen's own value line: the price at which the
held name's earnings yield falls to the floor the day's posture
requires before the screen will rank a name at all.

And in shadow — Marks's mode — none of this changes a weight.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from agents.marks import exits as marks_exits
from agents.marks.cycle_value import HowardMarks
from agents.marks.ranking import earnings_yield_floor_pct
from agents.marks.temperature import Posture
from core.backtest.decision_logger import DecisionLogger
from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import HeldPosition
from core.data.edgar_cache import EdgarCache
from core.live.agent_adapter import MarksLive
from core.live.doctrine_exits import ExitVerdict
from core.live.portfolio import LivePortfolio, Position
from core.live.runner import DailyRunner

from .conftest import make_pit

AS_OF = date(2024, 6, 30)

#: make_pit's EPS. Neutral's earnings-yield floor is 5%, a 20x line, so
#: fair value is $40.00; Warm's 5.5% is 18.18x, $36.36; Hot's 6.5% is
#: 15.38x, $30.77; Cool's 4.5% is 22.22x, $44.44.
EPS = 2.0


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


def _cache(tmp_path: Path) -> EdgarCache:
    return EdgarCache(cache_dir=tmp_path / "edgar")


def _held(ticker: str, *, entry: float, days: int = 120) -> HeldPosition:
    return HeldPosition(
        ticker=ticker,
        shares=10.0,
        entry_price=entry,
        entry_date=AS_OF - timedelta(days=days),
        current_price=entry,
    )


def _decide(
    fin: PointInTimeFinancials | None,
    price: float | None,
    posture: Posture | None = "Neutral",
    *,
    entry: float = 20.0,
) -> ExitVerdict | None:
    ticker = fin.ticker if fin is not None else "X"
    return marks_exits.decide(_held(ticker, entry=entry), fin, price, posture)


def _fair(posture: Posture) -> float:
    return EPS * 100.0 / earnings_yield_floor_pct(posture)


# ---------------------------------------------------------------------------
# The two tests the audit names (report section 6, item 13)
# ---------------------------------------------------------------------------
def test_held_name_that_still_qualifies_is_retained_when_it_slips_in_rank(
    tmp_path: Path,
) -> None:
    """Twenty-three names clear the screen; a Cool posture keeps room
    for twenty-two.

    The market here reads Cool (median P/E 10.5, no loss-makers, D/E
    0.27 across the board, 0.5% dividend yield: -2+2-2-2+2 = -2). HELD
    trades at $32, P/E 16 — the dearest of the 23, so it ranks last and
    is off the target list. Cool's value line is the 4.5% earnings-yield
    floor, 22.2x, $44.44 a share: at 72% of it HELD has not reached the
    80% rung where the first 25% trim begins, and its thesis holds: keep.
    """
    names = [f"N{i:02d}" for i in range(22)] + ["HELD"]
    fins = {t: make_pit(t) for t in names}
    prices = {t: 10.0 + i for i, t in enumerate(names)}  # HELD at $32
    strategy = HowardMarks(edgar_cache=_cache(tmp_path))
    held = {"HELD": _held("HELD", entry=25.0)}

    weights = strategy.select(
        AS_OF, names, _lookup(prices), _lookup(fins), held=held
    )

    assert strategy.last_temperature is not None
    assert strategy.last_temperature.posture == "Cool"
    assert len(weights) == 22 and "HELD" not in weights  # it did slip in rank
    assert strategy.last_exit_decisions == [
        ExitVerdict(
            "HELD",
            sell=False,
            reason=(
                "thesis intact; price $32.00 is 72.0% of fair value "
                "$44.44/share (P/E 16.0 against the Cool screen's 22.2x "
                "value line, its 4.5% earnings-yield floor), under the 80% "
                "rung where the first 25% trim begins"
            ),
        )
    ]


def test_exit_reason_in_journal_names_the_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real adapter, the real runner, shadow mode.

    The market reads Warm (median P/E 30, no loss-makers, D/E 0.27,
    0.3% yield: +2+2-2-2+2 = +2). RICH earns $1 a share and trades at
    $30: Warm's value line is the 5.5% floor, 18.2x, $18.18 a share, so
    RICH is at 165% of fair value with the cycle positioned for
    reversion — §7.2's last rung, the full exit. The doctrine journal
    says SELL and names the trigger; the engine's own action is recorded
    beside it and — shadow — is whatever it would have been.
    """
    rich = {t: make_pit(t, eps_diluted=1.0) for t in ("RICH", "R2", "R3")}
    fins: dict[str, PointInTimeFinancials] = {**rich, "CHEAP": make_pit("CHEAP")}
    prices: dict[str, float | None] = {
        "RICH": 30.0,
        "R2": 30.0,
        "R3": 30.0,
        "CHEAP": 20.0,
    }
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
    strategy = HowardMarks(edgar_cache=_cache(tmp_path))
    adapter = MarksLive(strategy)
    assert adapter.exit_mode == "shadow"

    book = LivePortfolio(agent=adapter.name, cash=2_000.0)
    book.last_rebalance_date = "2024-06-27"
    book.positions.append(
        Position(
            ticker="RICH",
            shares=10.0,
            entry_price=12.0,
            entry_date=(AS_OF - timedelta(days=400)).isoformat(),
            current_price=30.0,
            why_en="seeded",
            why_he="seeded",
        )
    )
    book.save(directory=runner.portfolio_dir)

    runner._run_one(adapter, AS_OF, list(fins), dict(prices), dict(fins))

    assert strategy.last_temperature is not None
    assert strategy.last_temperature.posture == "Warm"
    rows: list[dict[str, Any]] = []
    for path in sorted((tmp_path / "doctrine_exits" / adapter.name).glob("*.json")):
        payload = json.loads(path.read_text())
        rows.extend(payload if isinstance(payload, list) else [payload])
    assert [r["ticker"] for r in rows] == ["RICH"]
    row = rows[0]
    assert row["decision"] == "SELL"
    assert row["exit_trigger"] == (
        "past fair value with the cycle Warm: price $30.00 is 165.0% of "
        "fair value $18.18/share (P/E 30.0 against the Warm screen's 18.2x "
        "value line, its 5.5% earnings-yield floor); a cycle positioned "
        "for reversion is the playbook's full exit"
    )
    assert "fair value" in row["criteria_met"][0]
    assert row["market_conditions"]["exit_mode"] == "shadow"


# ---------------------------------------------------------------------------
# §7.2 — the scaling-out ladder
# ---------------------------------------------------------------------------
class TestScalingOut:
    def test_just_under_eighty_percent_of_fair_value_is_a_plain_keep(
        self,
    ) -> None:
        v = _decide(make_pit("X"), 31.9)
        assert v is not None and v.sell is False
        assert v.reason.startswith("thesis intact; price $31.90 is 79.8% of")
        assert "under the 80% rung" in v.reason

    def test_at_eighty_percent_the_first_trim_rung_is_named_and_kept(
        self,
    ) -> None:
        """§7.2: "as price approaches fair value, trim 25%". A verdict
        is keep or sell, so the trim is named, not done."""
        v = _decide(make_pit("X"), 0.8 * _fair("Neutral"))
        assert v == ExitVerdict(
            "X",
            sell=False,
            reason=(
                "first trim rung: price $32.00 is 80.0% of fair value "
                "$40.00/share (P/E 16.0 against the Neutral screen's 20.0x "
                "value line, its 5.0% earnings-yield floor), approaching "
                "it; the playbook's first 25% trim is not executed (a "
                "verdict is keep or sell), the position is kept"
            ),
        )

    def test_at_fair_value_with_the_cycle_neutral_the_second_rung_is_kept(
        self,
    ) -> None:
        """§7.2: reaching fair value is the second 25% trim, not the
        exit; the full exit needs the cycle hot as well."""
        v = _decide(make_pit("X"), _fair("Neutral"))
        assert v == ExitVerdict(
            "X",
            sell=False,
            reason=(
                "second trim rung: price $40.00 is 100.0% of fair value "
                "$40.00/share (P/E 20.0 against the Neutral screen's 20.0x "
                "value line, its 5.0% earnings-yield floor), at or past it "
                "with the cycle Neutral, not positioned for reversion; the "
                "playbook's second 25% trim is not executed (a verdict is "
                "keep or sell), the position is kept"
            ),
        )

    def test_far_past_fair_value_in_a_cool_cycle_is_still_kept(self) -> None:
        v = _decide(make_pit("X"), 2.0 * _fair("Cool"), "Cool")
        assert v is not None and v.sell is False
        assert v.reason.startswith("second trim rung: price $88.89 is 200.0%")

    def test_at_fair_value_with_the_cycle_hot_the_exit_is_complete(
        self,
    ) -> None:
        """§7.2: "as price exceeds fair value with cycle hot, fully
        exit" — at the line, as trigger 2 ("reaches") has it."""
        v = _decide(make_pit("X"), _fair("Hot"), "Hot")
        assert v is not None and v.sell is True
        assert v.reason.startswith(
            "past fair value with the cycle Hot: price $30.77 is 100.0% of "
            "fair value $30.77/share"
        )

    def test_at_fair_value_with_the_cycle_warm_the_exit_is_complete(
        self,
    ) -> None:
        v = _decide(make_pit("X"), _fair("Warm"), "Warm")
        assert v is not None and v.sell is True

    def test_just_under_fair_value_with_the_cycle_hot_is_kept(self) -> None:
        v = _decide(make_pit("X"), 30.76, "Hot")
        assert v is not None and v.sell is False
        assert v.reason.startswith("first trim rung:")

    def test_a_hot_cycle_alone_names_trigger_one_and_keeps(self) -> None:
        """§7.1 trigger 1: even good companies are trimmed when the
        cycle is hot — a trim, not an exit."""
        v = _decide(make_pit("X"), 10.0, "Hot")
        assert v == ExitVerdict(
            "X",
            sell=False,
            reason=(
                "thesis intact; price $10.00 is 32.5% of fair value "
                "$30.77/share (P/E 5.0 against the Hot screen's 15.4x value "
                "line, its 6.5% earnings-yield floor), under the 80% rung "
                "where the first 25% trim begins; the cycle is Hot, where "
                "trigger 1 trims even good companies — the trim is not "
                "executed (a verdict is keep or sell), the position is kept"
            ),
        )

    def test_a_price_decline_of_any_size_is_never_a_sell(self) -> None:
        """§7.3: volatility is opportunity, not risk. Down 80% from
        cost, thesis intact: keep."""
        v = _decide(make_pit("X"), 4.0, entry=20.0)
        assert v is not None and v.sell is False
        assert v.reason.startswith("thesis intact; price $4.00 is 10.0%")


# ---------------------------------------------------------------------------
# Trigger 4 — the thesis breaks, at any price
# ---------------------------------------------------------------------------
class TestThesisBreak:
    def test_book_equity_gone_sells_even_deep_below_fair_value(self) -> None:
        v = _decide(make_pit("X", total_equity=-50_000_000.0), 5.0)
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "thesis broken: book equity $-50,000,000 is no longer "
                "positive, which the entry gates require"
            ),
        )

    def test_a_full_year_loss_sells(self) -> None:
        """The entry gate requires positive full-year net income; a loss
        year is not quarterly noise (§7.3) — the earnings the screen
        priced are gone."""
        v = _decide(make_pit("X", net_income=-20_000_000.0), 5.0)
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "thesis broken: net income $-20,000,000 for the latest "
                "fiscal year is no longer positive, which the entry gates "
                "require"
            ),
        )

    def test_leverage_above_the_entry_ceiling_sells(self) -> None:
        """$1.6bn of debt on $1.5bn of equity is D/E 1.07, over the
        gate's 1.0."""
        v = _decide(make_pit("X", total_debt=1_600_000_000.0), 5.0)
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "thesis broken: balance sheet deteriorated, D/E 1.07 above "
                "the 1.0 the entry gates allow"
            ),
        )

    def test_leverage_at_the_ceiling_is_kept(self) -> None:
        v = _decide(make_pit("X", total_debt=1_500_000_000.0), 5.0)
        assert v is not None and v.sell is False

    def test_unreported_debt_is_not_a_breach(self) -> None:
        """Audit FUND-4: an absent debt tag is unknown, not a breach."""
        fin = dataclasses.replace(
            make_pit("X"), total_debt=None, long_term_debt=None
        )
        v = _decide(fin, 5.0)
        assert v is not None and v.sell is False

    def test_a_broken_thesis_sells_even_with_no_posture(self) -> None:
        v = _decide(make_pit("X", total_equity=0.0), 5.0, None)
        assert v is not None and v.sell is True


# ---------------------------------------------------------------------------
# What does not produce a verdict at all
# ---------------------------------------------------------------------------
class TestNoOpinion:
    def test_no_fundamentals_gives_no_verdict(self) -> None:
        assert _decide(None, 10.0) is None

    def test_no_price_gives_no_verdict(self) -> None:
        assert _decide(make_pit("X"), None) is None

    def test_no_measured_market_gives_no_verdict(self) -> None:
        """No posture, no value line: a keep here would hold the name on
        no evidence, a sell would sell it on none."""
        assert _decide(make_pit("X"), 50.0, None) is None

    def test_no_positive_eps_gives_no_verdict(self) -> None:
        v = _decide(make_pit("X", eps_diluted=0.0), 50.0)
        assert v is None


# ---------------------------------------------------------------------------
# The strategy wiring
# ---------------------------------------------------------------------------
class TestStrategyPublishesVerdicts:
    def _setup(
        self,
    ) -> tuple[dict[str, PointInTimeFinancials], dict[str, float]]:
        fins = {t: make_pit(t) for t in ("A", "B")}
        fins["C"] = make_pit("C", eps_diluted=1.0)
        # Median P/E 15 (10, 15, 40): Neutral-or-colder either way;
        # C at P/E 40 is past any posture's line.
        prices = {"A": 20.0, "B": 30.0, "C": 40.0}
        return fins, prices

    def test_nothing_held_publishes_nothing(self, tmp_path: Path) -> None:
        fins, prices = self._setup()
        strategy = HowardMarks(edgar_cache=_cache(tmp_path))
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []

    def test_weights_do_not_depend_on_what_is_held(self, tmp_path: Path) -> None:
        """Shadow: the verdicts are journaled, the target list is not
        touched. A held name the doctrine keeps is not added back, and
        one it sells is not removed."""
        fins, prices = self._setup()
        fins["D"] = make_pit("D", total_equity=-1.0)  # thesis broken
        prices["D"] = 20.0
        held = {
            "A": _held("A", entry=14.0),
            "D": _held("D", entry=14.0),
        }
        bare = HowardMarks(edgar_cache=_cache(tmp_path))
        holding = HowardMarks(edgar_cache=_cache(tmp_path))
        w_bare = bare.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        w_held = holding.select(
            AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
        )
        assert w_held == w_bare
        assert "A" in w_bare and "D" not in w_bare
        verdicts = {v.ticker: v.sell for v in holding.last_exit_decisions}
        assert verdicts == {"A": False, "D": True}

    def test_verdicts_are_published_when_nothing_qualifies(
        self, tmp_path: Path
    ) -> None:
        """An empty screen is when a held name's verdict matters most.
        C alone at P/E 40 reads Warm and clears no earnings-yield floor."""
        fins, _ = self._setup()
        strategy = HowardMarks(edgar_cache=_cache(tmp_path))
        weights = strategy.select(
            AS_OF,
            ["C"],
            _lookup({"C": 40.0}),
            _lookup(fins),
            held={"C": _held("C", entry=14.0)},
        )
        assert weights == {}
        assert strategy.last_temperature is not None
        assert strategy.last_temperature.posture == "Warm"
        assert [v.sell for v in strategy.last_exit_decisions] == [True]

    def test_an_unmeasured_market_judges_only_thesis_breaks(
        self, tmp_path: Path
    ) -> None:
        """An empty universe defaults the posture to Neutral without
        measuring anything; that is not a cycle reading to sell on."""
        fins, prices = self._setup()
        fins["D"] = make_pit("D", total_equity=-1.0)
        prices["D"] = 20.0
        strategy = HowardMarks(edgar_cache=_cache(tmp_path))
        strategy.select(
            AS_OF,
            [],
            _lookup(prices),
            _lookup(fins),
            held={"C": _held("C", entry=14.0), "D": _held("D", entry=14.0)},
        )
        assert [(v.ticker, v.sell) for v in strategy.last_exit_decisions] == [
            ("D", True)
        ]

    def test_a_held_name_outside_the_universe_is_judged_from_its_lookup(
        self, tmp_path: Path
    ) -> None:
        fins, prices = self._setup()
        strategy = HowardMarks(edgar_cache=_cache(tmp_path))
        strategy.select(
            AS_OF,
            ["B"],
            _lookup(prices),
            _lookup(fins),
            held={"A": _held("A", entry=14.0)},
        )
        assert [(v.ticker, v.sell) for v in strategy.last_exit_decisions] == [
            ("A", False)
        ]

    def test_the_previous_days_verdicts_do_not_survive_a_new_select(
        self, tmp_path: Path
    ) -> None:
        fins, prices = self._setup()
        strategy = HowardMarks(edgar_cache=_cache(tmp_path))
        strategy.select(
            AS_OF,
            list(fins),
            _lookup(prices),
            _lookup(fins),
            held={"C": _held("C", entry=14.0)},
        )
        assert strategy.last_exit_decisions
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []
