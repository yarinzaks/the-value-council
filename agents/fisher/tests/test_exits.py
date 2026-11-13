"""Fisher's own exit rules, as shadow verdicts (audit SF-X1, item 13f).

Playbook §7: the default is never sell. The legitimate triggers are a
mistake in the original purchase, a company that "would no longer pass
12+ of 15 points if newly evaluated today", and a clearly superior
opportunity; §7.2 rules out appreciation, market levels, recessions,
a bad quarter, downgrades and sector rotation. None of it ran:
``PhilipFisher.select`` ignored ``held`` and the engine sold a Fisher
name only for leaving the day's target list (17 sales since
2026-08-05, median hold 64.5 days). These tests pin the rules as
:class:`~core.live.doctrine_exits.ExitVerdict` objects published on
``last_exit_decisions``:

* trigger 2 — the entry screen's balance-sheet gates now fail (book
  equity, D/E above 0.6), or fewer than 4 of the 5 quant-checkable
  points can still pass on measured evidence;
* retention — anything else that can be judged is kept, including a
  Tier A name slipping to Tier B, a P/E far above the entry ceiling, a
  loss year, and a name that slipped off today's list.

And in shadow — Fisher's mode — none of this changes a weight.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from agents.fisher import exits as fisher_exits
from agents.fisher.quality_growth import PhilipFisher
from core.backtest.decision_logger import DecisionLogger
from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import HeldPosition
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact
from core.live.agent_adapter import FisherLive
from core.live.doctrine_exits import ExitVerdict
from core.live.portfolio import LivePortfolio, Position
from core.live.runner import DailyRunner

from .conftest import make_fact, make_pit

AS_OF = date(2024, 6, 30)

INTACT = (
    "15-point assessment intact: 5 of the 5 quant-checkable points pass, "
    "at least the 4 the entry screen requires; Fisher's default is never sell"
)


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
    ticker: str,
    *,
    growth: float = 0.12,
    rd_share: float | None = 0.08,
    margin_start: float = 0.18,
    margin_step: float = 0.005,
    share_growth: float = 0.0,
) -> list[XbrlFact]:
    """Eight 10-Ks, FY2016..FY2023, each filed the next 15 February.

    The defaults are Fisher-grade on every point: revenue +12%/yr, R&D
    8% of revenue, operating margin 18% widening 50 bps a year (FY2018
    19.0% to FY2023 21.5%, +250 bps), share count flat at 200M.
    ``rd_share=None`` files no R&D at all.
    """
    out: list[XbrlFact] = []
    for i, fy in enumerate(range(2016, 2024)):
        revenue = 2_000_000_000.0 * (1.0 + growth) ** i
        end = date(fy, 12, 31)
        filed = date(fy + 1, 2, 15)
        acc = f"{ticker}-{fy}"
        rows = [
            ("Revenues", revenue),
            ("OperatingIncomeLoss", revenue * (margin_start + margin_step * i)),
            ("CommonStockSharesOutstanding", 200_000_000.0 * (1.0 + share_growth) ** i),
        ]
        if rd_share is not None:
            rows.append(("ResearchAndDevelopmentExpense", revenue * rd_share))
        for concept, value in rows:
            out.append(
                make_fact(
                    concept=concept,
                    value=value,
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


def _held(ticker: str, *, entry: float = 100.0, days: int = 120) -> HeldPosition:
    return HeldPosition(
        ticker=ticker,
        shares=10.0,
        entry_price=entry,
        entry_date=AS_OF - timedelta(days=days),
        current_price=entry,
    )


def _decide(
    cache: EdgarCache, fin: PointInTimeFinancials | None
) -> ExitVerdict | None:
    ticker = fin.ticker if fin is not None else "X"
    return fisher_exits.decide(_held(ticker), fin, cache=cache, as_of=AS_OF)


def _thin_margin(ticker: str) -> PointInTimeFinancials:
    """Operating margin 10% on the latest filing — point 5 fails."""
    return make_pit(ticker, operating_income=500_000_000.0)


# ---------------------------------------------------------------------------
# The two tests the audit names (report section 6, item 13)
# ---------------------------------------------------------------------------
def test_held_name_that_still_qualifies_is_retained_when_it_slips_in_rank(
    tmp_path: Path,
) -> None:
    """Three Tier A names clear every gate; the strategy keeps room for
    two.

    Within a tier the screen ranks on P/E, and HELD's 30 is the richest
    of the three, so it is off the target list. Its five points all
    still pass: Fisher keeps it.
    """
    cache = _cache(tmp_path, A=_facts("A"), B=_facts("B"), HELD=_facts("HELD"))
    fins = {t: make_pit(t) for t in ("A", "B", "HELD")}
    prices = {"A": 60.0, "B": 80.0, "HELD": 120.0}
    strategy = PhilipFisher(edgar_cache=cache, max_portfolio_size=2)
    held = {"HELD": _held("HELD", entry=90.0)}

    weights = strategy.select(
        AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
    )

    assert set(weights) == {"A", "B"}  # it did slip in rank
    assert strategy.last_exit_decisions == [
        ExitVerdict("HELD", sell=False, reason=INTACT)
    ]


def test_exit_reason_in_journal_names_the_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real adapter, the real runner, shadow mode.

    DECAY's revenue now shrinks 5% a year, its operating margin is 10%
    and 500 bps narrower than five years ago: three of the five points
    fail. The doctrine
    journal says SELL and names the deterioration; the engine's own
    action is recorded beside it and — shadow — is whatever it would
    have been.
    """
    cache = _cache(
        tmp_path,
        GOOD=_facts("GOOD"),
        DECAY=_facts("DECAY", growth=-0.05, margin_start=0.18, margin_step=-0.01),
    )
    fins = {"GOOD": make_pit("GOOD"), "DECAY": _thin_margin("DECAY")}
    prices: dict[str, float | None] = {"GOOD": 60.0, "DECAY": 50.0}
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
    adapter = FisherLive(PhilipFisher(edgar_cache=cache))
    assert adapter.exit_mode == "shadow"

    book = LivePortfolio(agent=adapter.name, cash=2_000.0)
    book.last_rebalance_date = "2024-06-27"
    book.positions.append(
        Position(
            ticker="DECAY",
            shares=10.0,
            entry_price=70.0,
            entry_date=(AS_OF - timedelta(days=400)).isoformat(),
            current_price=50.0,
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
    assert [r["ticker"] for r in rows] == ["DECAY"]
    row = rows[0]
    assert row["decision"] == "SELL"
    assert row["exit_trigger"] == (
        "15-point assessment deteriorated: 2 of the 5 quant-checkable points "
        "pass, under the 4 the entry screen requires; failing: "
        "point 1 market potential: 5-yr revenue CAGR -5.0% (floor 8%); "
        "point 5 profit margins: operating margin 10.0% (floor 12%); "
        "point 6 margin maintenance: 5-yr margin trend -500 bps (floor -100 bps)"
    )
    assert "15-point assessment" in row["criteria_met"][0]
    assert row["market_conditions"]["exit_mode"] == "shadow"


# ---------------------------------------------------------------------------
# Trigger 2 — the company no longer matches the original assessment
# ---------------------------------------------------------------------------
class TestDeterioration:
    def test_two_measured_failures_sell(self, tmp_path: Path) -> None:
        """3 of 5: the screen would not buy it today."""
        cache = _cache(tmp_path, X=_facts("X", growth=0.05))
        v = _decide(cache, _thin_margin("X"))
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "15-point assessment deteriorated: 3 of the 5 quant-checkable "
                "points pass, under the 4 the entry screen requires; failing: "
                "point 1 market potential: 5-yr revenue CAGR 5.0% (floor 8%); "
                "point 5 profit margins: operating margin 10.0% (floor 12%)"
            ),
        )

    def test_tier_a_slipping_to_tier_b_is_kept(self, tmp_path: Path) -> None:
        """4 of 5 is still a name the screen buys; the failure is named."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _thin_margin("X"))
        assert v == ExitVerdict(
            "X",
            sell=False,
            reason=(
                "15-point assessment intact: 4 of the 5 quant-checkable points "
                "pass, at least the 4 the entry screen requires; Fisher's "
                "default is never sell; also point 5 profit margins: "
                "operating margin 10.0% (floor 12%)"
            ),
        )

    def test_dilution_past_the_ceiling_counts_as_a_failure(
        self, tmp_path: Path
    ) -> None:
        """Point 13: shares +3%/yr is +15.9% over five years."""
        cache = _cache(tmp_path, X=_facts("X", share_growth=0.03))
        v = _decide(cache, _thin_margin("X"))
        assert v is not None and v.sell is True
        assert "point 13 equity dilution: 5-yr share count +15.9% (ceiling +5%)" in (
            v.reason
        )

    def test_book_equity_gone_sells(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X", total_equity=-50_000_000.0))
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "no longer matches the original assessment: book equity "
                "$-50,000,000 is no longer positive"
            ),
        )

    def test_leverage_above_the_entry_ceiling_sells(self, tmp_path: Path) -> None:
        """$2.1bn of debt on $3bn of equity is D/E 0.70, over 0.6."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X", total_debt=2_100_000_000.0))
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "no longer matches the original assessment: D/E 0.70 above "
                "the 0.6 the entry screen allows"
            ),
        )

    def test_leverage_at_the_ceiling_is_kept(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X", total_debt=1_800_000_000.0))  # 0.60
        assert v == ExitVerdict("X", sell=False, reason=INTACT)

    def test_unreported_debt_is_not_a_breach(self, tmp_path: Path) -> None:
        """Audit FUND-4: an absent debt tag is unknown, not a breach."""
        cache = _cache(tmp_path, X=_facts("X"))
        fin = dataclasses.replace(
            make_pit("X"), total_debt=None, long_term_debt=None
        )
        v = _decide(cache, fin)
        assert v is not None and v.sell is False


# ---------------------------------------------------------------------------
# §7.2 / §10 — what is never a sell
# ---------------------------------------------------------------------------
class TestNotATrigger:
    def test_a_loss_year_is_not_a_sell(self, tmp_path: Path) -> None:
        """The screen's positive-income gate prices an entry; §7.2 says
        a disappointing quarter is noise."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X", net_income=-20_000_000.0))
        assert v == ExitVerdict("X", sell=False, reason=INTACT)

    def test_a_pe_far_above_the_entry_ceiling_is_kept(self, tmp_path: Path) -> None:
        """§10.3: "Sell a high-quality position because the price has
        appreciated" is an anti-pattern. P/E 60 is off the screen (Tier
        A buys up to 35) and still a keep."""
        cache = _cache(tmp_path, X=_facts("X"))
        fins = {"X": make_pit("X")}
        strategy = PhilipFisher(edgar_cache=cache)
        weights = strategy.select(
            AS_OF,
            ["X"],
            _lookup({"X": 240.0}),
            _lookup(fins),
            held={"X": _held("X", entry=60.0)},
        )
        assert weights == {}
        assert strategy.last_exit_decisions == [
            ExitVerdict("X", sell=False, reason=INTACT)
        ]

    def test_a_missing_price_does_not_block_the_verdict(
        self, tmp_path: Path
    ) -> None:
        """Nothing in Fisher's exit doctrine reads a price."""
        cache = _cache(tmp_path, X=_facts("X"))
        strategy = PhilipFisher(edgar_cache=cache)
        strategy.select(
            AS_OF,
            [],
            _lookup({}),
            _lookup({"X": make_pit("X")}),
            held={"X": _held("X")},
        )
        assert strategy.last_exit_decisions == [
            ExitVerdict("X", sell=False, reason=INTACT)
        ]


# ---------------------------------------------------------------------------
# What does not produce a verdict at all
# ---------------------------------------------------------------------------
class TestNoOpinion:
    def test_no_fundamentals_gives_no_verdict(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, None) is None

    def test_no_filing_history_gives_no_verdict(self, tmp_path: Path) -> None:
        """Only point 5 is measurable without 10-Ks. A keep here would
        hold the name on no evidence; a sell would sell it on none."""
        cache = _cache(tmp_path)
        assert _decide(cache, make_pit("X")) is None

    def test_an_unmeasurable_point_can_leave_the_count_undecided(
        self, tmp_path: Path
    ) -> None:
        """No R&D filed, one measured failure: 3 pass, 1 unknown. It
        might be 4 of 5 or 3 of 5 — no verdict."""
        cache = _cache(tmp_path, X=_facts("X", rd_share=None))
        assert _decide(cache, _thin_margin("X")) is None

    def test_but_an_unmeasurable_point_cannot_rescue_two_failures(
        self, tmp_path: Path
    ) -> None:
        """No R&D filed, two measured failures: at most 3 of 5 even if
        R&D would pass — sell."""
        cache = _cache(tmp_path, X=_facts("X", rd_share=None, growth=0.05))
        v = _decide(cache, _thin_margin("X"))
        assert v is not None and v.sell is True
        assert v.reason.startswith("15-point assessment deteriorated: 2 of the 5")
        assert "point 3" not in v.reason

    def test_a_gate_break_sells_even_with_no_filing_history(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(tmp_path)
        v = _decide(cache, make_pit("X", total_equity=0.0))
        assert v is not None and v.sell is True


# ---------------------------------------------------------------------------
# The strategy wiring
# ---------------------------------------------------------------------------
class TestStrategyPublishesVerdicts:
    def _setup(
        self, tmp_path: Path
    ) -> tuple[EdgarCache, dict[str, PointInTimeFinancials], dict[str, float]]:
        cache = _cache(
            tmp_path,
            A=_facts("A"),
            B=_facts("B"),
            C=_facts("C", growth=0.05),
        )
        fins = {"A": make_pit("A"), "B": make_pit("B"), "C": _thin_margin("C")}
        prices = {"A": 60.0, "B": 80.0, "C": 40.0}
        return cache, fins, prices

    def test_nothing_held_publishes_nothing(self, tmp_path: Path) -> None:
        cache, fins, prices = self._setup(tmp_path)
        strategy = PhilipFisher(edgar_cache=cache)
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []

    def test_weights_do_not_depend_on_what_is_held(self, tmp_path: Path) -> None:
        """Shadow: the verdicts are journaled, the target list is not
        touched. A held name the doctrine keeps is not added back, and
        one it sells is not removed."""
        cache, fins, prices = self._setup(tmp_path)
        held = {"A": _held("A"), "C": _held("C")}
        bare = PhilipFisher(edgar_cache=cache)
        holding = PhilipFisher(edgar_cache=cache)
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
        strategy = PhilipFisher(edgar_cache=cache)
        weights = strategy.select(
            AS_OF,
            ["C"],
            _lookup({"C": 40.0}),
            _lookup(fins),
            held={"C": _held("C")},
        )
        assert weights == {}
        assert [v.sell for v in strategy.last_exit_decisions] == [True]

    def test_a_held_name_outside_the_universe_is_judged_from_its_lookup(
        self, tmp_path: Path
    ) -> None:
        cache, fins, prices = self._setup(tmp_path)
        strategy = PhilipFisher(edgar_cache=cache)
        strategy.select(
            AS_OF,
            ["B"],
            _lookup(prices),
            _lookup(fins),
            held={"A": _held("A")},
        )
        assert [(v.ticker, v.sell) for v in strategy.last_exit_decisions] == [
            ("A", False)
        ]

    def test_the_previous_days_verdicts_do_not_survive_a_new_select(
        self, tmp_path: Path
    ) -> None:
        cache, fins, prices = self._setup(tmp_path)
        strategy = PhilipFisher(edgar_cache=cache)
        strategy.select(
            AS_OF,
            list(fins),
            _lookup(prices),
            _lookup(fins),
            held={"C": _held("C")},
        )
        assert strategy.last_exit_decisions
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []
