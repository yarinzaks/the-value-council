"""Klarman's own exit rules, as shadow verdicts (audit SF-X1, item 13e).

Playbook §7: Klarman eases out of a position as the price closes on
intrinsic value — scaling out from 80% of value, out completely by
110% — and sells at any price when the thesis breaks. §7.3 lists what
is not a trigger: market downturns, earnings noise, sector rotation.
None of it ran: ``SethKlarman.select`` ignored ``held`` and the engine
sold a Klarman name only for leaving the day's target list (37 such
exits since 2026-08-05). These tests pin the rules as
:class:`~core.live.doctrine_exits.ExitVerdict` objects published on
``last_exit_decisions``:

* thesis break, first and at any price — book equity no longer
  positive, D/E above the entry screen's ceiling, or free cash flow the
  conservative DCF can no longer project (negative in the latest year,
  negative on average, or shrinking faster than 20%/yr);
* trigger 1 — price at or past 110% of the conservative DCF value;
* retention — anything else is kept, including the 80%-110% scale-out
  zone (its 25-50% trim is not a keep-or-sell verdict) and a name that
  slipped off today's list.

And in shadow — Klarman's mode — none of this changes a weight.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from agents.klarman import exits as klarman_exits
from agents.klarman.margin_of_safety import SethKlarman
from core.backtest.decision_logger import DecisionLogger
from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import HeldPosition
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact
from core.live.agent_adapter import KlarmanLive
from core.live.doctrine_exits import ExitVerdict
from core.live.portfolio import LivePortfolio, Position
from core.live.runner import DailyRunner

from .conftest import make_fact, make_pit

AS_OF = date(2024, 6, 30)

#: Flat $300M free cash flow FY2019..FY2023 (OCF $400M less capex
#: $100M): 0% growth, 8% discount, 10x terminal over ten years is
#: $3.4026bn, or $34.03 a share on make_pit's 100M shares.
IV_PER_SHARE = 34.026048839364854

FLAT = (300_000_000.0,) * 5


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


def _facts(
    ticker: str, fcfs: Sequence[float] = FLAT, *, first_year: int = 2019
) -> list[XbrlFact]:
    """A filer whose annual FCF series is exactly ``fcfs``, oldest first;
    capex flat at $100M, so the series is OCF's."""
    capex = 100_000_000.0
    out: list[XbrlFact] = []
    for i, fcf in enumerate(fcfs):
        fy = first_year + i
        end = date(fy, 12, 31)
        filed = date(fy + 1, 2, 15)
        acc = f"{ticker}-{fy}"
        out.append(
            make_fact(
                concept="NetCashProvidedByUsedInOperatingActivities",
                value=fcf + capex,
                period_end=end,
                filed=filed,
                accession=acc,
            )
        )
        out.append(
            make_fact(
                concept="PaymentsToAcquirePropertyPlantAndEquipment",
                value=capex,
                period_end=end,
                filed=filed,
                accession=acc,
            )
        )
    return out


def _cache(tmp_path: Path, **per_ticker: list[XbrlFact]) -> EdgarCache:
    cache = EdgarCache(cache_dir=tmp_path / "edgar")
    for ticker, facts in per_ticker.items():
        cache.save_facts(ticker, facts)
    return cache


def _held(ticker: str, *, entry: float, days: int = 120) -> HeldPosition:
    return HeldPosition(
        ticker=ticker,
        shares=10.0,
        entry_price=entry,
        entry_date=AS_OF - timedelta(days=days),
        current_price=entry,
    )


def _decide(
    cache: EdgarCache,
    fin: PointInTimeFinancials | None,
    price: float | None,
    *,
    entry: float = 20.0,
) -> ExitVerdict | None:
    ticker = fin.ticker if fin is not None else "X"
    market_cap = (
        price * fin.shares_outstanding
        if fin is not None and price is not None and fin.shares_outstanding
        else None
    )
    return klarman_exits.decide(
        _held(ticker, entry=entry),
        fin,
        price,
        market_cap,
        cache=cache,
        as_of=AS_OF,
    )


@pytest.fixture(autouse=True)
def _not_a_financial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every synthetic ticker is a beverage company, so its FCF is
    valuable: a test ticker can be a real one the SIC table calls a
    bank."""
    monkeypatch.setattr("core.screener.business_type.sic_for", lambda _t: 2080)


# ---------------------------------------------------------------------------
# The two tests the audit names (report section 6, item 13)
# ---------------------------------------------------------------------------
def test_held_name_that_still_qualifies_is_retained_when_it_slips_in_rank(
    tmp_path: Path,
) -> None:
    """Three names clear the 30% margin-of-safety floor; the strategy
    keeps room for two.

    HELD trades at $22 against $34.03 of value — a 35% margin, the
    thinnest of the three — so it ranks third and is off the target
    list. At 65% of value it has not reached the 80% line where Klarman
    starts to scale out, and its thesis holds: keep.
    """
    cache = _cache(tmp_path, A=_facts("A"), B=_facts("B"), HELD=_facts("HELD"))
    fins = {t: make_pit(t) for t in ("A", "B", "HELD")}
    prices = {"A": 15.0, "B": 16.0, "HELD": 22.0}
    strategy = SethKlarman(edgar_cache=cache, max_portfolio_size=2)
    held = {"HELD": _held("HELD", entry=18.0)}

    weights = strategy.select(
        AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
    )

    assert set(weights) == {"A", "B"}  # it did slip in rank
    assert strategy.last_exit_decisions == [
        ExitVerdict(
            "HELD",
            sell=False,
            reason=(
                "thesis intact; price $22.00 is 64.7% of intrinsic value "
                "$34.03/share (conservative DCF, margin of safety 35.3%), "
                "under the 80% line where scaling out begins"
            ),
        )
    ]


def test_exit_reason_in_journal_names_the_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real adapter, the real runner, shadow mode.

    RICH ran to $40 against $34.03 of value — 117.6%, past the 110%
    complete-exit line. The doctrine journal says SELL and names the
    trigger; the engine's own action is recorded beside it and —
    shadow — is whatever it would have been.
    """
    cache = _cache(tmp_path, CHEAP=_facts("CHEAP"), RICH=_facts("RICH"))
    fins = {"CHEAP": make_pit("CHEAP"), "RICH": make_pit("RICH")}
    prices: dict[str, float | None] = {"CHEAP": 15.0, "RICH": 40.0}
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
    adapter = KlarmanLive(SethKlarman(edgar_cache=cache))
    assert adapter.exit_mode == "shadow"

    book = LivePortfolio(agent=adapter.name, cash=2_000.0)
    book.last_rebalance_date = "2024-06-27"
    book.positions.append(
        Position(
            ticker="RICH",
            shares=10.0,
            entry_price=21.0,
            entry_date=(AS_OF - timedelta(days=400)).isoformat(),
            current_price=40.0,
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
        "price approached intrinsic value: $40.00 is 117.6% of "
        "$34.03/share (conservative DCF), at or past the 110% line where "
        "the exit is complete"
    )
    assert "intrinsic value" in row["criteria_met"][0]
    assert row["market_conditions"]["exit_mode"] == "shadow"


# ---------------------------------------------------------------------------
# Trigger 1 — price approaches intrinsic value
# ---------------------------------------------------------------------------
class TestScalingOut:
    def test_just_under_eighty_percent_of_value_is_a_plain_keep(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X"), 27.0)
        assert v is not None and v.sell is False
        assert v.reason.startswith("thesis intact; price $27.00 is 79.4%")

    def test_at_eighty_percent_scaling_out_begins_and_the_rest_is_kept(
        self, tmp_path: Path
    ) -> None:
        """§7.2: "begin scaling out at 80% of intrinsic value". A
        verdict is keep or sell, so the 25-50% trim is named, not done."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X"), 0.8 * IV_PER_SHARE)
        assert v == ExitVerdict(
            "X",
            sell=False,
            reason=(
                "scale-out zone: price $27.22 is 80.0% of intrinsic value "
                "$34.03/share (conservative DCF), between the 80% line "
                "where scaling out begins and the 110% line where it is "
                "complete; the playbook's 25-50% trim is not executed "
                "(a verdict is keep or sell), the position is kept"
            ),
        )

    def test_full_value_is_still_inside_the_zone(self, tmp_path: Path) -> None:
        """Unlike Graham, reaching fair value is not the exit."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X"), IV_PER_SHARE)
        assert v is not None and v.sell is False
        assert v.reason.startswith("scale-out zone: price $34.03 is 100.0%")

    def test_at_one_hundred_ten_percent_the_exit_is_complete(
        self, tmp_path: Path
    ) -> None:
        """§7.2: "complete exit by 110% of intrinsic value" — at the
        line, not only past it."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X"), 1.1 * IV_PER_SHARE)
        assert v is not None and v.sell is True
        assert v.reason.startswith(
            "price approached intrinsic value: $37.43 is 110.0% of $34.03/share"
        )

    def test_just_under_one_hundred_ten_percent_is_kept(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X"), 37.42)
        assert v is not None and v.sell is False

    def test_a_price_decline_of_any_size_is_never_a_sell(
        self, tmp_path: Path
    ) -> None:
        """§7.3: a market downturn widens the margin — "more buying
        opportunities". Down 75% from cost, thesis intact: keep."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X"), 5.0, entry=20.0)
        assert v is not None and v.sell is False
        assert "margin of safety 85.3%" in v.reason


# ---------------------------------------------------------------------------
# Trigger 2 — the thesis is broken, at any price
# ---------------------------------------------------------------------------
class TestThesisBreak:
    def test_book_equity_gone_sells_even_deep_below_value(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        fin = make_pit("X", total_equity=-50_000_000.0)
        v = _decide(cache, fin, 10.0)
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason="thesis broken: book equity $-50,000,000 is no longer positive",
        )

    def test_leverage_above_the_entry_ceiling_sells(self, tmp_path: Path) -> None:
        """§10.6: leverage destroys margin of safety. $1.2bn of debt on
        $1.5bn of equity is D/E 0.80, over the screen's 0.7."""
        cache = _cache(tmp_path, X=_facts("X"))
        fin = make_pit("X", total_debt=1_200_000_000.0)
        v = _decide(cache, fin, 10.0)
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "thesis broken: balance sheet deteriorated, D/E 0.80 above "
                "the 0.7 the entry screen allows"
            ),
        )

    def test_leverage_at_the_ceiling_is_kept(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        fin = make_pit("X", total_debt=1_050_000_000.0)  # exactly 0.70
        v = _decide(cache, fin, 10.0)
        assert v is not None and v.sell is False

    def test_unreported_debt_is_not_a_breach(self, tmp_path: Path) -> None:
        """Audit FUND-4: an absent debt tag is unknown, not a breach."""
        cache = _cache(tmp_path, X=_facts("X"))
        fin = dataclasses.replace(
            make_pit("X"), total_debt=None, long_term_debt=None
        )
        v = _decide(cache, fin, 10.0)
        assert v is not None and v.sell is False

    def test_negative_free_cash_flow_in_the_latest_year_sells(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(
            tmp_path,
            X=_facts("X", (300e6, 300e6, 300e6, 300e6, -40_000_000.0)),
        )
        v = _decide(cache, make_pit("X"), 10.0)
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "thesis broken: free cash flow negative in FY2023 "
                "($-40,000,000); the conservative DCF can no longer "
                "value the business"
            ),
        )

    def test_free_cash_flow_shrinking_faster_than_twenty_percent_sells(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(
            tmp_path, X=_facts("X", tuple(300e6 * 0.7**i for i in range(5)))
        )
        v = _decide(cache, make_pit("X"), 5.0)
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "thesis broken: free cash flow shrinking 30.0%/yr, "
                "steeper than the 20%/yr the conservative DCF will project"
            ),
        )

    def test_a_mild_decline_is_valued_not_sold(self, tmp_path: Path) -> None:
        cache = _cache(
            tmp_path, X=_facts("X", tuple(300e6 * 0.95**i for i in range(5)))
        )
        v = _decide(cache, make_pit("X"), 10.0)
        assert v is not None and v.sell is False

    def test_a_loss_year_is_not_a_sell(self, tmp_path: Path) -> None:
        """§7.3: earnings noise is not a trigger; the thesis is cash flow."""
        cache = _cache(tmp_path, X=_facts("X"))
        fin = make_pit("X", net_income=-20_000_000.0)
        v = _decide(cache, fin, 10.0)
        assert v is not None and v.sell is False

    def test_a_broken_thesis_sells_even_without_a_market_cap(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        fin = make_pit("X", total_equity=0.0)
        v = klarman_exits.decide(
            _held("X", entry=20.0), fin, 10.0, None, cache=cache, as_of=AS_OF
        )
        assert v is not None and v.sell is True


# ---------------------------------------------------------------------------
# What does not produce a verdict at all
# ---------------------------------------------------------------------------
class TestNoOpinion:
    def test_no_fundamentals_gives_no_verdict(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, None, 10.0) is None

    def test_no_price_gives_no_verdict(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, make_pit("X"), None) is None

    def test_too_short_a_cash_flow_record_gives_no_verdict(
        self, tmp_path: Path
    ) -> None:
        """Two 10-Ks: the DCF needs three. A keep here would hold the
        name on no evidence; a sell would sell it on none."""
        cache = _cache(tmp_path, X=_facts("X", (300e6, -40e6), first_year=2022))
        assert _decide(cache, make_pit("X"), 10.0) is None

    def test_a_financial_gives_no_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OCF - capex tracks deposits at a bank: no DCF, no verdict."""
        monkeypatch.setattr("core.screener.business_type.sic_for", lambda _t: 6020)
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, make_pit("X"), 40.0) is None

    def test_no_market_cap_gives_no_verdict(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        v = klarman_exits.decide(
            _held("X", entry=20.0),
            make_pit("X"),
            40.0,
            None,
            cache=cache,
            as_of=AS_OF,
        )
        assert v is None


# ---------------------------------------------------------------------------
# The strategy wiring
# ---------------------------------------------------------------------------
class TestStrategyPublishesVerdicts:
    def _setup(
        self, tmp_path: Path
    ) -> tuple[EdgarCache, dict[str, PointInTimeFinancials], dict[str, float]]:
        cache = _cache(tmp_path, A=_facts("A"), B=_facts("B"), C=_facts("C"))
        fins = {t: make_pit(t) for t in ("A", "B", "C")}
        prices = {"A": 15.0, "B": 16.0, "C": 40.0}
        return cache, fins, prices

    def test_nothing_held_publishes_nothing(self, tmp_path: Path) -> None:
        cache, fins, prices = self._setup(tmp_path)
        strategy = SethKlarman(edgar_cache=cache)
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []

    def test_weights_do_not_depend_on_what_is_held(self, tmp_path: Path) -> None:
        """Shadow: the verdicts are journaled, the target list is not
        touched. A held name the doctrine keeps is not added back, and
        one it sells is not removed."""
        cache, fins, prices = self._setup(tmp_path)
        held = {"A": _held("A", entry=14.0), "C": _held("C", entry=14.0)}
        bare = SethKlarman(edgar_cache=cache)
        holding = SethKlarman(edgar_cache=cache)
        w_bare = bare.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        w_held = holding.select(
            AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
        )
        assert w_held == w_bare
        verdicts = {v.ticker: v.sell for v in holding.last_exit_decisions}
        assert verdicts == {"A": False, "C": True}

    def test_verdicts_are_published_when_nothing_qualifies(
        self, tmp_path: Path
    ) -> None:
        """An empty screen is when a held name's verdict matters most."""
        cache, fins, _ = self._setup(tmp_path)
        strategy = SethKlarman(edgar_cache=cache)
        weights = strategy.select(
            AS_OF,
            ["C"],
            _lookup({"C": 40.0}),
            _lookup(fins),
            held={"C": _held("C", entry=14.0)},
        )
        assert weights == {}
        assert [v.sell for v in strategy.last_exit_decisions] == [True]

    def test_a_held_name_outside_the_universe_is_judged_from_its_lookup(
        self, tmp_path: Path
    ) -> None:
        cache, fins, prices = self._setup(tmp_path)
        strategy = SethKlarman(edgar_cache=cache)
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
        cache, fins, prices = self._setup(tmp_path)
        strategy = SethKlarman(edgar_cache=cache)
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
