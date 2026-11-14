"""Tests for DailyRunner's trade-execution seam.

``_run_one`` is the method that turns a scan into trades. It had no test
coverage at all, which is how a held position could be sold at a price
that no longer existed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest

from agents.council.cadence import RunType
from agents.council.published_list import (
    PublishedList,
    executable_list,
    publish,
)
from core.backtest.decision_logger import DecisionLogger
from core.live.agent_adapter import AgentAdapter, LiveTarget, LiveWatch, ScanResult
from core.live.portfolio import LivePortfolio, Position, WatchEntry
from core.live.runner import (
    DEFAULT_MIN_HOLDING_DAYS,
    AgentRunResult,
    DailyRunner,
    _filter_adapters,
    pos_age_days,
    reachable_targets,
)
from core.live.trade_ledger import read_ledger
from scripts.run_daily_paper_trading import parse_args

AS_OF = date(2026, 8, 5)


class _StubAdapter:
    """Minimal stand-in for AgentAdapter: returns a fixed scan."""

    def __init__(self, name: str, targets: list[LiveTarget]) -> None:
        self.name = name
        self._targets = targets
        self.last_held: object = None

    def run_scan(  # type: ignore[no-untyped-def]
        self, as_of, universe, prices, fundamentals, *, held=None
    ) -> ScanResult:
        # Record what the runner handed us so tests can assert the
        # strategy actually sees its own book.
        self.last_held = held
        return ScanResult(
            targets=self._targets, watchlist=[], universe_size=len(universe)
        )


class _StubPriceLoader:
    """Returns whatever the test dictates, including None.

    Records ``force_refresh`` so tests can assert the close-of-day mark
    asks for a settled close rather than re-reading the morning quote.
    """

    def __init__(
        self,
        prices: dict[str, float | None],
        *,
        last_bars: dict[str, date] | None = None,
    ) -> None:
        self._prices = prices
        self._last_bars = last_bars or {}
        self.calls: list[tuple[str, date, bool]] = []

    def get_price_on(
        self, ticker: str, as_of: date, *, force_refresh: bool = False
    ) -> float | None:
        self.calls.append((ticker, as_of, force_refresh))
        return self._prices.get(ticker)

    def cached_range(self, ticker: str) -> tuple[date, date] | None:
        """The newest cached bar, as the real loader reports it.

        None — no history at all — unless the test names a last bar.
        """
        last = self._last_bars.get(ticker)
        return None if last is None else (date(2020, 1, 2), last)

    def splits_between(
        self, ticker: str, start: date, end: date
    ) -> list[tuple[date, float]]:
        """No splits. A failed lookup makes the name unpriced for the run."""
        return []


def _target(ticker: str, *, weight: float = 0.5, rank: int = 1) -> LiveTarget:
    return LiveTarget(
        ticker=ticker,
        weight=weight,
        rank=rank,
        why_en="stub",
        why_he="stub",
        score=None,
    )


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DailyRunner:
    """A DailyRunner with every external dependency stubbed out.

    Snapshot writing is neutralised so tests never touch the real data
    root; the runner swallows snapshot errors anyway, so leaving it live
    would hide failures rather than surface them.
    """
    monkeypatch.setattr("core.live.runner.save_snapshot", lambda snap: None)
    return DailyRunner(
        market="US",
        adapters=[],
        portfolio_dir=tmp_path / "portfolios",
        # Without this the cooling-off lists are written into the real
        # data root, so a test run would leave artefacts the live
        # rebalance could later trade from.
        published_dir=tmp_path / "published",
        ledger_dir=tmp_path / "trades",
        price_loader=_StubPriceLoader({}),  # type: ignore[arg-type]
        universe=object(),  # type: ignore[arg-type]
        pit_loader=object(),  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
        decision_logger=DecisionLogger(root=tmp_path / "decisions"),
    )


def _not_a_rebalance_book() -> LivePortfolio:
    """A book whose quarterly rebalance is already behind it.

    AS_OF is 2026-08-05 and August is a rebalance month, so without
    this every test about opening a position would instead be
    testing the cooling-off rule — which correctly refuses to trade
    when nothing was published on an earlier run.
    """
    p = LivePortfolio(agent="stub_agent", cash=10_000)
    p.last_rebalance_date = "2026-08-03"
    return p


def _seed_holding(
    runner: DailyRunner,
    agent: str,
    ticker: str,
    *,
    entry_price: float,
    current_price: float,
    shares: float = 10.0,
) -> LivePortfolio:
    p = LivePortfolio(agent=agent)
    p.positions.append(
        Position(
            ticker=ticker,
            shares=shares,
            entry_price=entry_price,
            entry_date="2026-07-01",
            current_price=current_price,
            why_en="seeded",
            why_he="seeded",
        )
    )
    p.cash = 1_000.0
    p.save(directory=runner.portfolio_dir)
    return p


# ---------------------------------------------------------------------------
# The defect this file exists for
# ---------------------------------------------------------------------------
class TestStaleMarkSell:
    def test_no_fresh_price_holds_instead_of_selling(
        self, runner: DailyRunner
    ) -> None:
        # DEAD dropped off the target list, and no price source can quote
        # it — the delisting case. It must not be sold at its last mark.
        _seed_holding(
            runner, "stub_agent", "DEAD", entry_price=50.0, current_price=50.0
        )
        runner.price_loader = _StubPriceLoader({"DEAD": None})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("KEEP")])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["KEEP", "DEAD"],
            {"KEEP": 20.0, "DEAD": None},
            {"KEEP": None, "DEAD": None},
        )

        sells = [t for t in result.trades if t.side == "SELL"]
        assert sells == []
        assert result.portfolio.has("DEAD")

    def test_fresh_price_still_sells(self, runner: DailyRunner) -> None:
        # The guard must not block ordinary exits.
        _seed_holding(
            runner, "stub_agent", "GONE", entry_price=50.0, current_price=50.0
        )
        runner.price_loader = _StubPriceLoader({"GONE": 55.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("KEEP")])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["KEEP", "GONE"],
            {"KEEP": 20.0, "GONE": 55.0},
            {"KEEP": None, "GONE": None},
        )

        sells = [t for t in result.trades if t.side == "SELL"]
        assert len(sells) == 1
        assert sells[0].ticker == "GONE"
        assert sells[0].price == pytest.approx(55.0)
        assert not result.portfolio.has("GONE")

    def test_zero_price_holds(self, runner: DailyRunner) -> None:
        # A quote of 0.0 is a data error, not a valuation.
        _seed_holding(
            runner, "stub_agent", "ZERO", entry_price=50.0, current_price=50.0
        )
        runner.price_loader = _StubPriceLoader({"ZERO": 0.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("KEEP")])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["KEEP", "ZERO"],
            {"KEEP": 20.0, "ZERO": 0.0},
            {"KEEP": None, "ZERO": None},
        )

        assert [t for t in result.trades if t.side == "SELL"] == []
        assert result.portfolio.has("ZERO")

    def test_nav_still_reflects_the_unsold_position(
        self, runner: DailyRunner
    ) -> None:
        # Holding rather than selling must not make the position vanish
        # from NAV: mark_to_market keeps the last mark on purpose.
        _seed_holding(
            runner, "stub_agent", "DEAD", entry_price=50.0, current_price=50.0
        )
        runner.price_loader = _StubPriceLoader({"DEAD": None})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["DEAD"],
            {"DEAD": None},
            {"DEAD": None},
        )

        # 10 shares at the retained $50 mark plus $1,000 cash.
        assert result.portfolio.total_nav == pytest.approx(1_500.0)


# ---------------------------------------------------------------------------
# Surrounding behaviour the fix must not disturb
# ---------------------------------------------------------------------------
class TestNoTradeSignal:
    def test_empty_target_list_never_liquidates(self, runner: DailyRunner) -> None:
        _seed_holding(
            runner, "stub_agent", "HOLD", entry_price=50.0, current_price=60.0
        )
        runner.price_loader = _StubPriceLoader({"HOLD": 60.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["HOLD"],
            {"HOLD": 60.0},
            {"HOLD": None},
        )

        assert result.trades == []
        assert result.portfolio.has("HOLD")

    def test_run_stamps_last_open_run(self, runner: DailyRunner) -> None:
        _seed_holding(
            runner, "stub_agent", "HOLD", entry_price=50.0, current_price=60.0
        )
        runner.price_loader = _StubPriceLoader({"HOLD": 60.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["HOLD"],
            {"HOLD": 60.0},
            {"HOLD": None},
        )

        # scripts/verify_run_state.py gates the daily job on this field.
        assert result.portfolio.last_open_run != ""


# ---------------------------------------------------------------------------
# Close-of-day mark
# ---------------------------------------------------------------------------
class TestMarkToMarket:
    def test_close_mark_demands_a_settled_close(self, runner: DailyRunner) -> None:
        # Without force_refresh, get_price_on's fast path returns the
        # intraday quote the morning run cached under the same date, and
        # the NAV series becomes intraday prices labelled as closes.
        _seed_holding(
            runner, "stub_agent", "HOLD", entry_price=50.0, current_price=60.0
        )
        loader = _StubPriceLoader({"HOLD": 63.0})
        runner.price_loader = loader  # type: ignore[assignment]
        runner.adapters = [_StubAdapter("stub_agent", [])]  # type: ignore[list-item]

        runner.run_mark_to_market(as_of=AS_OF)

        assert loader.calls == [("HOLD", AS_OF, True)]

    def test_close_mark_applies_the_fresh_price(self, runner: DailyRunner) -> None:
        _seed_holding(
            runner, "stub_agent", "HOLD", entry_price=50.0, current_price=60.0
        )
        runner.price_loader = _StubPriceLoader({"HOLD": 63.0})  # type: ignore[assignment]
        runner.adapters = [_StubAdapter("stub_agent", [])]  # type: ignore[list-item]

        results = runner.run_mark_to_market(as_of=AS_OF)

        pos = results[0].portfolio.positions[0]
        assert pos.current_price == pytest.approx(63.0)
        assert results[0].portfolio.last_close_run != ""


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
class TestIdempotency:
    """The workflow can fire twice for one date — a retry, a manual
    dispatch after a scheduled run, the watchdog's make-up trigger. The
    second pass used to re-execute every rotation and write a second set
    of decision records for the same day."""

    def _scan(self, runner: DailyRunner, *, force: bool = False):
        adapter = _StubAdapter("stub_agent", [_target("KEEP", weight=0.5)])
        return runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["KEEP"],
            {"KEEP": 20.0},
            {"KEEP": None},
            force=force,
        )

    def test_second_run_on_the_same_date_is_a_no_op(
        self, runner: DailyRunner
    ) -> None:
        runner.price_loader = _StubPriceLoader({"KEEP": 20.0})  # type: ignore[assignment]

        first = self._scan(runner)
        second = self._scan(runner)

        assert first.trades  # the first run actually bought
        assert second.trades == []
        assert second.skipped is True

    def test_the_no_op_preserves_state(self, runner: DailyRunner) -> None:
        runner.price_loader = _StubPriceLoader({"KEEP": 20.0})  # type: ignore[assignment]

        first = self._scan(runner)
        nav_after_first = first.portfolio.total_nav
        second = self._scan(runner)

        assert second.portfolio.has("KEEP")
        assert second.portfolio.total_nav == pytest.approx(nav_after_first)

    def test_force_re_runs(self, runner: DailyRunner) -> None:
        runner.price_loader = _StubPriceLoader({"KEEP": 20.0})  # type: ignore[assignment]

        self._scan(runner)
        forced = self._scan(runner, force=True)

        assert forced.skipped is False

    def test_a_different_date_is_not_skipped(self, runner: DailyRunner) -> None:
        runner.price_loader = _StubPriceLoader({"KEEP": 20.0})  # type: ignore[assignment]
        self._scan(runner)

        adapter = _StubAdapter("stub_agent", [_target("KEEP", weight=0.5)])
        tomorrow = runner._run_one(
            adapter,  # type: ignore[arg-type]
            date(2026, 8, 6),
            ["KEEP"],
            {"KEEP": 21.0},
            {"KEEP": None},
        )

        assert tomorrow.skipped is False


# ---------------------------------------------------------------------------
# Decision-log labelling
# ---------------------------------------------------------------------------
class TestExecutionLabels:
    """The strategy logs BUY as intent; the runner used to log BUY again
    for the fill, so every executed purchase appeared twice for the same
    ticker on the same day."""

    def _records(self, runner: DailyRunner) -> list[dict]:
        import json

        root = runner.decision_logger.root / "stub_agent"
        out: list[dict] = []
        for path in sorted(root.glob("*.json")):
            payload = json.loads(path.read_text())
            out.extend(payload if isinstance(payload, list) else [payload])
        return out

    def test_a_fill_is_labelled_as_a_fill(self, runner: DailyRunner) -> None:
        runner.price_loader = _StubPriceLoader({"NEW": 20.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("NEW", weight=0.5)])

        runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["NEW"],
            {"NEW": 20.0},
            {"NEW": None},
        )

        decisions = {r["decision"] for r in self._records(runner)}
        assert "FILL" in decisions
        assert "BUY" not in decisions  # the stub adapter logs no intent

    def test_an_exit_is_labelled_as_an_exit(self, runner: DailyRunner) -> None:
        _seed_holding(
            runner, "stub_agent", "GONE", entry_price=50.0, current_price=55.0
        )
        runner.price_loader = _StubPriceLoader({"GONE": 55.0, "NEW": 20.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("NEW", weight=0.5)])

        runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["NEW", "GONE"],
            {"NEW": 20.0, "GONE": 55.0},
            {"NEW": None, "GONE": None},
        )

        records = self._records(runner)
        exits = [r for r in records if r["decision"] == "EXIT"]
        assert len(exits) == 1
        assert exits[0]["ticker"] == "GONE"
        assert "left today's target list" in exits[0]["criteria_met"]

    def _exit_rationale(self, runner: DailyRunner, shares: float) -> str:
        _seed_holding(
            runner, "stub_agent", "GONE", entry_price=50.0, current_price=55.0,
            shares=shares,
        )
        runner.price_loader = _StubPriceLoader({"GONE": 55.0, "NEW": 20.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("NEW", weight=0.5)])
        runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["NEW", "GONE"],
            {"NEW": 20.0, "GONE": 55.0},
            {"NEW": None, "GONE": None},
        )
        exits = [r for r in self._records(runner) if r["decision"] == "EXIT"]
        assert len(exits) == 1
        return str(exits[0]["rationale"])

    def test_an_exit_says_how_many_shares_it_sold(
        self, runner: DailyRunner
    ) -> None:
        """Audit RK-05: ADD and TRIM rows name their shares, EXIT did not,
        so the journal could not say how large a sale was."""
        assert self._exit_rationale(runner, 10.0) == (
            "Closed live position: entry $50.00, exit $55.00 (+10.00%), "
            "sold 10 shares"
        )

    def test_a_legacy_fractional_line_reports_what_left_the_book(
        self, runner: DailyRunner
    ) -> None:
        # A full exit sells the line entire, fraction and all.
        assert self._exit_rationale(runner, 10.5).endswith(", sold 10.5 shares")


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
class TestWholeShareSizing:
    """The book holds whole shares, because the broker it is copied
    into sells whole shares. The remainder of each position stays in
    cash and the names a $10,000 book cannot afford at all give their
    weight to the names it can."""

    def test_equal_weight_book_holds_every_affordable_name(
        self, runner: DailyRunner
    ) -> None:
        n = 27
        weight = 1.0 / n
        # Deliberately awkward prices — the whole-share remainder is
        # worst when the price does not divide the slot. Every one is
        # under the $370 slot, so none is out of reach.
        prices = {f"T{i:02d}": 37.0 + i * 11.3 for i in range(n)}
        assert max(prices.values()) < 10_000.0 * weight
        targets = [
            _target(t, weight=weight, rank=i + 1)
            for i, t in enumerate(sorted(prices))
        ]
        runner.price_loader = _StubPriceLoader(dict(prices))  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", targets)

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            sorted(prices),
            prices,
            dict.fromkeys(prices),
        )

        p = result.portfolio
        assert len(p.positions) == n
        for pos in p.positions:
            assert pos.shares == float(int(pos.shares)), f"{pos.ticker} fractional"
        # Flooring 27 positions strands 20.1% of this book in cash —
        # measured, with these prices. The residue pass spends it down
        # to 2.5%. It does not reach zero and should not: the band caps
        # the top-up, so a $331 share in a $370 slot cannot take a
        # second one and its $40 stays put. 4% is the ceiling this
        # asserts; 20% is the number it exists to prevent.
        cash_pct = p.cash / p.total_nav * 100.0
        assert cash_pct < 4.0, f"{cash_pct:.2f}% left idle"
        # Nothing was pushed past the band in the process.
        for pos in p.positions:
            assert pos.weight_pct <= (100.0 / n) * 1.25 + 0.01

    def test_a_name_no_share_of_which_fits_is_left_alone(
        self, runner: DailyRunner
    ) -> None:
        # $10,000 / 30 = $333 per slot against a $2,000 share. It is the
        # only name on the list, so there is nowhere for the money to go
        # and the book stays in cash — which is the honest answer, not a
        # third of a share.
        runner.price_loader = _StubPriceLoader({"PRICEY": 2_000.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("PRICEY", weight=1 / 30)])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["PRICEY"],
            {"PRICEY": 2_000.0},
            {"PRICEY": None},
        )

        assert not result.portfolio.has("PRICEY")
        assert result.portfolio.cash == pytest.approx(10_000.0)

    def test_an_unreachable_name_hands_its_weight_to_the_rest(
        self, runner: DailyRunner
    ) -> None:
        """The regression this class exists for.

        Four equal 25% slots on $10,000 is $2,500 each. One name costs
        $4,000 a share and cannot be held. Sizing the other three at
        their original $2,500 would leave $2,500 — a quarter of the book
        — idle. They are resized to a third of the book each instead,
        which is what an investor told "you cannot buy this one" does.
        """
        prices = {"CHEAP1": 100.0, "CHEAP2": 100.0, "CHEAP3": 100.0, "DEAR": 4_000.0}
        targets = [
            _target(t, weight=0.25, rank=i + 1)
            for i, t in enumerate(["DEAR", "CHEAP1", "CHEAP2", "CHEAP3"])
        ]
        runner.price_loader = _StubPriceLoader(dict(prices))  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", targets)

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            sorted(prices),
            prices,
            dict.fromkeys(prices),
        )

        p = result.portfolio
        assert not p.has("DEAR")
        assert {pos.ticker for pos in p.positions} == {"CHEAP1", "CHEAP2", "CHEAP3"}
        # $3,333 each at $100 a share is 33 shares, not the 25 that the
        # original quarter-weight slot would have bought.
        for pos in p.positions:
            assert pos.shares == 33.0
        assert p.cash / p.total_nav * 100.0 < 2.0

    def test_dropping_one_name_can_bring_another_back_in_reach(self) -> None:
        """Names are dropped one at a time, not as a set.

        Two $600 shares against two 50% slots of a $1,000 book: both are
        out of reach at $500 a slot. Removing one takes the survivor's
        slot to $1,000, which covers it. Dropping the unaffordable set
        in one pass would have left the book empty.
        """
        targets = [
            _target("A", weight=0.5, rank=1),
            _target("B", weight=0.5, rank=2),
        ]
        kept = reachable_targets(
            targets, {"A": 600.0, "B": 600.0}, nav=1_000.0
        )
        assert len(kept) == 1
        assert kept[0].weight == pytest.approx(1.0)

    def test_a_held_name_is_never_dropped_for_price(self) -> None:
        """Affordability is an entry question, not a holding question.

        A name bought at $50 that has run to $600 is above its slot, but
        the cash is already in it. Dropping it would hand the exit
        decision to the sizing pass, which has no view on whether to
        sell.
        """
        targets = [
            _target("RAN", weight=0.5, rank=1),
            _target("FLAT", weight=0.5, rank=2),
        ]
        kept = reachable_targets(
            targets,
            {"RAN": 600.0, "FLAT": 100.0},
            nav=1_000.0,
            held=frozenset({"RAN"}),
        )
        assert {t.ticker for t in kept} == {"RAN", "FLAT"}
        assert sum(t.weight for t in kept) == pytest.approx(1.0)

    def test_an_unpriced_name_is_passed_through_untouched(self) -> None:
        """No price is not the same as too expensive.

        The buy loop has its own "cannot buy, no price" branch and logs
        a warning there. Silently dropping the name here would turn a
        data problem into a sizing decision and hide it.
        """
        targets = [_target("A", weight=0.5), _target("NOPRICE", weight=0.5)]
        kept = reachable_targets(targets, {"A": 10.0, "NOPRICE": None}, nav=1_000.0)
        assert [t.ticker for t in kept] == ["A"]


class TestCashPosture:
    """A doctrine that deploys less than the whole book means the rest
    as cash.

    Audit SF-X2: Klarman's list sums to 92% and his book ran at 99.96%
    invested ($4.83 of cash); Marks at Neutral sums to 80% and his ran
    at 0.08%. The whole-share residue pass pooled every dollar of cash,
    and the rebalance ADD spent whatever was left.
    """

    @staticmethod
    def _awkward_prices(n: int) -> dict[str, float | None]:
        # The same deliberately awkward ladder the whole-share tests
        # use: no price divides a slot, so the residue pass has work.
        return {f"T{i:02d}": 37.0 + i * 11.3 for i in range(n)}

    def _open(
        self, runner: DailyRunner, n: int, deployed: float
    ) -> AgentRunResult:
        prices = self._awkward_prices(n)
        targets = [
            _target(t, weight=deployed / n, rank=i + 1)
            for i, t in enumerate(sorted(prices))
        ]
        runner.price_loader = _StubPriceLoader(dict(prices))  # type: ignore[assignment]
        return runner._run_one(
            _StubAdapter("stub_agent", targets),  # type: ignore[arg-type]
            AS_OF,
            sorted(prices),
            prices,
            dict.fromkeys(prices),
        )

    def test_klarman_92_percent_deployment_leaves_8_percent_cash(
        self, runner: DailyRunner
    ) -> None:
        from agents.klarman.margin_of_safety import _deployment_for

        decision = _deployment_for(20)
        assert decision.deployed_fraction == pytest.approx(0.92)
        result = self._open(
            runner, decision.portfolio_size, decision.deployed_fraction
        )

        p = result.portfolio
        assert len(p.positions) == decision.portfolio_size
        cash_pct = p.cash / p.total_nav * 100.0
        # The doctrine's 8% is a floor the sizing may not dig into. The
        # whole-share residue may leave a little more, never less.
        assert cash_pct >= 8.0, f"{cash_pct:.2f}% cash against an 8% posture"
        assert cash_pct < 12.0, f"{cash_pct:.2f}% cash — sizing left too much idle"

    def test_marks_neutral_posture_holds_20_percent(
        self, runner: DailyRunner
    ) -> None:
        from agents.marks.temperature import profile_for

        neutral = profile_for("Neutral")
        assert neutral.deployed_fraction == pytest.approx(0.80)
        result = self._open(
            runner, neutral.portfolio_size, neutral.deployed_fraction
        )

        p = result.portfolio
        cash_pct = p.cash / p.total_nav * 100.0
        assert cash_pct >= 20.0, f"{cash_pct:.2f}% cash against a 20% posture"
        assert cash_pct < 24.0, f"{cash_pct:.2f}% cash — sizing left too much idle"

    def test_a_rebalance_add_does_not_spend_the_posture_cash(
        self, runner: DailyRunner
    ) -> None:
        """The second way the 8% went: an ADD that spent every dollar.

        Two names at 46% each of a $10,000 book want $4,600 apiece. A
        sits at $5,500 (+19.6%, inside the band) and B at $3,200 (-30.4%,
        outside it), so the book is 87% invested. B's shortfall is
        $1,400, but the doctrine's 92% leaves room for only $500 more.
        """
        p = LivePortfolio(agent="stub_agent", cash=1_300.0)
        p.last_rebalance_date = "2026-08-03"
        for ticker, shares, price in (("A", 55.0, 100.0), ("B", 32.0, 100.0)):
            p.positions.append(
                Position(
                    ticker=ticker,
                    shares=shares,
                    entry_price=price,
                    entry_date="2026-07-01",
                    current_price=price,
                )
            )
        p.save(directory=runner.portfolio_dir)
        prices: dict[str, float | None] = {"A": 100.0, "B": 100.0}
        runner.price_loader = _StubPriceLoader(dict(prices))  # type: ignore[assignment]

        result = runner._run_one(
            _StubAdapter(  # type: ignore[arg-type]
                "stub_agent",
                [_target("A", weight=0.46), _target("B", weight=0.46, rank=2)],
            ),
            AS_OF,
            ["A", "B"],
            prices,
            dict.fromkeys(prices),
        )

        adds = [t for t in result.trades if t.side == "BUY"]
        # Still topped up — the posture limits the ADD, it does not
        # forbid it — but by four shares, not twelve.
        assert len(adds) == 1 and adds[0].ticker == "B"
        assert adds[0].shares == pytest.approx(4.0)
        book = result.portfolio
        assert book.cash / book.total_nav * 100.0 >= 8.0

    def test_a_held_name_outside_the_list_counts_against_the_posture(
        self, runner: DailyRunner
    ) -> None:
        """Capital the floor keeps in a name is capital already invested.

        OLD left the list four days after it was bought and the 30-day
        floor holds it: $2,000 of a $10,000 book. The new list is one
        name at 50%. The doctrine's half in cash is honoured only if
        NEW gets $3,000, not $5,000.
        """
        p = LivePortfolio(agent="stub_agent", cash=8_000.0)
        p.last_rebalance_date = "2026-08-03"
        p.positions.append(
            Position(
                ticker="OLD",
                shares=20.0,
                entry_price=100.0,
                entry_date="2026-08-01",
                current_price=100.0,
            )
        )
        p.save(directory=runner.portfolio_dir)
        prices: dict[str, float | None] = {"OLD": 100.0, "NEW": 100.0}
        runner.price_loader = _StubPriceLoader(dict(prices))  # type: ignore[assignment]

        result = runner._run_one(
            _StubAdapter("stub_agent", [_target("NEW", weight=0.5)]),  # type: ignore[arg-type]
            AS_OF,
            ["OLD", "NEW"],
            prices,
            dict.fromkeys(prices),
        )

        book = result.portfolio
        assert book.has("OLD")
        new = book.positions[book._index_of("NEW")]  # type: ignore[index]
        assert new.shares == pytest.approx(29.0)
        assert book.cash / book.total_nav * 100.0 >= 50.0

    def test_a_fully_deployed_doctrine_may_spend_all_its_cash(self) -> None:
        """Weights that sum to the whole book leave the cash uncapped."""
        from core.live.runner import deployable_cash

        targets = [_target(t, weight=1 / 3) for t in ("A", "B", "C")]
        assert deployable_cash(
            targets, nav=10_000.0, invested=4_000.0, cash=6_000.0
        ) == pytest.approx(6_000.0)

    def test_a_book_already_past_its_posture_deploys_nothing(self) -> None:
        """Over the posture is not a reason to sell; it is a reason not
        to buy. The cap floors at zero rather than going negative."""
        from core.live.runner import deployable_cash

        targets = [_target("A", weight=0.5)]
        assert deployable_cash(
            targets, nav=10_000.0, invested=7_000.0, cash=3_000.0
        ) == pytest.approx(0.0)

    def test_an_empty_list_deploys_nothing(self) -> None:
        from core.live.runner import deployable_cash

        assert deployable_cash(
            [], nav=10_000.0, invested=0.0, cash=10_000.0
        ) == pytest.approx(0.0)

    @staticmethod
    def _held(
        runner: DailyRunner, cash: float, lines: list[tuple[str, float, float]]
    ) -> None:
        """Save a book holding ``(ticker, shares, mark)`` lines, all past
        the holding floor."""
        p = LivePortfolio(agent="stub_agent", cash=cash)
        p.last_rebalance_date = "2026-08-03"
        for ticker, shares, mark in lines:
            p.positions.append(
                Position(
                    ticker=ticker,
                    shares=shares,
                    entry_price=mark,
                    entry_date="2026-07-01",
                    current_price=mark,
                )
            )
        p.save(directory=runner.portfolio_dir)

    def test_a_held_name_without_a_quote_keeps_its_place_in_the_posture(
        self, runner: DailyRunner
    ) -> None:
        """Review of SF-X2: a held list name with no quote today left Σw
        but stayed in ``invested`` at its last mark, so its slot was
        counted twice and an equal-weight book could buy nothing.

        Twenty names at 5%. T00..T18 are held at their $500 slot, T00
        with no quote today; T19 is new at $50 and the book has $500.
        The list sums to the whole book, so T19 gets its nine shares.
        """
        self._held(
            runner, 500.0, [(f"T{i:02d}", 10.0, 50.0) for i in range(19)]
        )
        prices: dict[str, float | None] = {f"T{i:02d}": 50.0 for i in range(20)}
        prices["T00"] = None
        runner.price_loader = _StubPriceLoader(dict(prices))  # type: ignore[assignment]

        result = runner._run_one(
            _StubAdapter(  # type: ignore[arg-type]
                "stub_agent",
                [_target(f"T{i:02d}", weight=0.05, rank=i + 1) for i in range(20)],
            ),
            AS_OF,
            sorted(prices),
            prices,
            dict.fromkeys(prices),
        )

        book = result.portfolio
        assert book.has("T19")
        new = book.positions[book._index_of("T19")]  # type: ignore[index]
        assert new.shares == pytest.approx(9.0)

    def test_an_unquoted_holding_does_not_starve_a_partial_posture(
        self, runner: DailyRunner
    ) -> None:
        """The same gap on a 92% list. A ($4,600, 46%) has no quote; B
        is new at $100. Room is $9,200 − $4,600 = $4,600, so B gets 45
        shares ($4,500 + fees) and cash stays at or above 8%."""
        self._held(runner, 5_400.0, [("A", 46.0, 100.0)])
        prices: dict[str, float | None] = {"A": None, "B": 100.0}
        runner.price_loader = _StubPriceLoader(dict(prices))  # type: ignore[assignment]

        result = runner._run_one(
            _StubAdapter(  # type: ignore[arg-type]
                "stub_agent",
                [_target("A", weight=0.46), _target("B", weight=0.46, rank=2)],
            ),
            AS_OF,
            ["A", "B"],
            prices,
            dict.fromkeys(prices),
        )

        book = result.portfolio
        assert book.has("B")
        b = book.positions[book._index_of("B")]  # type: ignore[index]
        assert b.shares == pytest.approx(45.0)
        assert book.cash / book.total_nav * 100.0 >= 8.0

    def test_an_unquoted_holding_does_not_block_a_rebalance_add(
        self, runner: DailyRunner
    ) -> None:
        """The zeroed room also blocked the day's ADDs. A ($4,600) has no
        quote; B sits at $3,200 against a $4,600 slot (−30%). Room is
        $9,200 − $7,800 = $1,400: thirteen shares after the fee."""
        self._held(runner, 2_200.0, [("A", 46.0, 100.0), ("B", 32.0, 100.0)])
        prices: dict[str, float | None] = {"A": None, "B": 100.0}
        runner.price_loader = _StubPriceLoader(dict(prices))  # type: ignore[assignment]

        result = runner._run_one(
            _StubAdapter(  # type: ignore[arg-type]
                "stub_agent",
                [_target("A", weight=0.46), _target("B", weight=0.46, rank=2)],
            ),
            AS_OF,
            ["A", "B"],
            prices,
            dict.fromkeys(prices),
        )

        adds = [t for t in result.trades if t.side == "BUY"]
        assert len(adds) == 1 and adds[0].ticker == "B"
        assert adds[0].shares == pytest.approx(13.0)
        book = result.portfolio
        assert book.cash / book.total_nav * 100.0 >= 8.0

    def test_only_an_unquoted_holding_returns_to_the_posture(self) -> None:
        """An unquoted name not held keeps its slot in cash, as before;
        an unquoted held one is added back, at the per-name cap."""
        from core.live.runner import posture_targets

        listed = [
            _target("HELD", weight=0.3),
            _target("NEW", weight=0.3, rank=2),
            _target("OK", weight=0.3, rank=3),
        ]
        prices: dict[str, float | None] = {"HELD": None, "NEW": None, "OK": 10.0}
        reachable = reachable_targets(
            listed, prices, nav=10_000.0, held=frozenset({"HELD"}), max_weight=0.25
        )
        assert [t.ticker for t in reachable] == ["OK"]

        posture = posture_targets(
            listed, reachable, prices, frozenset({"HELD"}), max_weight=0.25
        )
        assert {t.ticker: t.weight for t in posture} == pytest.approx(
            {"OK": 0.25, "HELD": 0.25}
        )

    def test_a_non_finite_input_deploys_nothing(self) -> None:
        """NaN fails every comparison, so ``min(cash, nan)`` was the
        whole cash: a broken mark would have lifted the cap entirely."""
        from core.live.runner import deployable_cash

        targets = [_target("A", weight=0.5)]
        for nav, invested in (
            (float("nan"), 0.0),
            (10_000.0, float("nan")),
            (float("inf"), 0.0),
        ):
            assert deployable_cash(
                targets, nav=nav, invested=invested, cash=3_000.0
            ) == pytest.approx(0.0)
        assert deployable_cash(
            [_target("A", weight=float("inf"))],
            nav=10_000.0,
            invested=0.0,
            cash=3_000.0,
        ) == pytest.approx(0.0)


class TestRebalanceBand:
    """Positions were sized once at entry and never touched again, so a
    name that doubled became twice its intended weight."""

    def _drifted(
        self,
        runner: DailyRunner,
        *,
        entry: float,
        now: float,
        entry_date: str = "2026-07-01",
    ) -> DailyRunner:
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="DRIFT",
                shares=50.0,
                entry_price=entry,
                entry_date=entry_date,
                current_price=now,
                why_en="",
                why_he="",
            )
        )
        p.cash = 5_000.0
        p.save(directory=runner.portfolio_dir)
        return runner

    def _run(self, runner: DailyRunner, price: float):
        adapter = _StubAdapter("stub_agent", [_target("DRIFT", weight=0.5)])
        runner.price_loader = _StubPriceLoader({"DRIFT": price})  # type: ignore[assignment]
        return runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["DRIFT"],
            {"DRIFT": price},
            {"DRIFT": None},
        )

    def test_a_position_inside_the_band_is_left_alone(
        self, runner: DailyRunner
    ) -> None:
        # 50 shares at 110 = $5,500 against $5,000 cash: NAV 10,500,
        # target 5,250, drift +4.8% — well inside the 25% band.
        self._drifted(runner, entry=100.0, now=110.0)

        result = self._run(runner, 110.0)

        assert result.trades == []

    def test_a_position_outside_the_band_is_trimmed(
        self, runner: DailyRunner
    ) -> None:
        # 50 shares at 300 = $15,000 against $5,000 cash: NAV 20,000,
        # target 10,000, drift +50%.
        self._drifted(runner, entry=100.0, now=300.0)

        result = self._run(runner, 300.0)

        sells = [t for t in result.trades if t.side == "SELL"]
        assert len(sells) == 1
        assert sells[0].ticker == "DRIFT"
        # The line is trimmed, not closed.
        assert result.portfolio.has("DRIFT")
        pos = result.portfolio.positions[0]
        # A whole-share trim lands within one share of target, not on
        # it: the ideal trim is 16.67 shares, so 16 go and the position
        # sits at 34 × $300 = 51.0% of a $20,000 NAV. One share is 1.5%
        # of this book, which is the tolerance — tightening it below
        # that would be asserting a fraction can be sold.
        one_share_pct = 300.0 / result.portfolio.total_nav * 100.0
        assert pos.weight_pct == pytest.approx(50.0, abs=one_share_pct)
        assert pos.shares == float(int(pos.shares))

    def test_a_trim_keeps_the_original_cost_basis(
        self, runner: DailyRunner
    ) -> None:
        # Trimming must not restate what the remaining shares cost.
        self._drifted(runner, entry=100.0, now=300.0)

        result = self._run(runner, 300.0)

        assert result.portfolio.positions[0].entry_price == pytest.approx(100.0)

    def test_an_underweight_position_is_topped_up(
        self, runner: DailyRunner
    ) -> None:
        # 50 shares at 20 = $1,000 against $5,000 cash: NAV 6,000,
        # target 3,000, drift -67%.
        self._drifted(runner, entry=100.0, now=20.0)

        result = self._run(runner, 20.0)

        buys = [t for t in result.trades if t.side == "BUY"]
        assert len(buys) == 1
        assert result.portfolio.positions[0].weight_pct == pytest.approx(
            50.0, abs=1.0
        )


class TestTheHoldingFloorCoversTheTrim:
    """Audit ENG-5: the 30-day floor guarded the exit path only.

    ``_rebalance`` built trims from drift alone, so market_core cut BAC
    at 7 days (4 -> 3 shares) and seth_klarman cut TTD at 11 days
    (54 -> 42) — the same young-position selling the floor exists to
    stop, arriving through the other door.
    """

    _band = TestRebalanceBand()

    def _drifted(
        self, runner: DailyRunner, *, entry: float, now: float, entry_date: str
    ) -> DailyRunner:
        return self._band._drifted(
            runner, entry=entry, now=now, entry_date=entry_date
        )

    def _run(self, runner: DailyRunner, price: float) -> AgentRunResult:
        result: AgentRunResult = self._band._run(runner, price)
        return result

    def test_a_7_day_old_overweight_position_is_not_trimmed(
        self, runner: DailyRunner
    ) -> None:
        # +50% drift, which a seasoned line trims by 16 shares.
        self._drifted(runner, entry=100.0, now=300.0, entry_date="2026-07-29")

        result = self._run(runner, 300.0)

        assert [t for t in result.trades if t.side == "SELL"] == []
        assert result.portfolio.positions[0].shares == 50.0

    def test_a_29_day_old_position_is_still_inside_the_floor(
        self, runner: DailyRunner
    ) -> None:
        self._drifted(runner, entry=100.0, now=300.0, entry_date="2026-07-07")

        result = self._run(runner, 300.0)

        assert [t for t in result.trades if t.side == "SELL"] == []

    def test_a_position_exactly_30_days_old_is_trimmed(
        self, runner: DailyRunner
    ) -> None:
        # The exit path holds below the floor and sells at it; the trim
        # path agrees, so one boundary governs every sale.
        self._drifted(runner, entry=100.0, now=300.0, entry_date="2026-07-06")

        result = self._run(runner, 300.0)

        sells = [t for t in result.trades if t.side == "SELL"]
        assert len(sells) == 1 and sells[0].ticker == "DRIFT"

    def test_an_unreadable_entry_date_does_not_block_the_trim(
        self, runner: DailyRunner
    ) -> None:
        # pos_age_days returns None for "cannot tell", and the exit path
        # treats that as no floor. The trim path must not differ.
        self._drifted(runner, entry=100.0, now=300.0, entry_date="not-a-date")

        result = self._run(runner, 300.0)

        assert len([t for t in result.trades if t.side == "SELL"]) == 1

    def test_a_young_underweight_position_is_still_topped_up(
        self, runner: DailyRunner
    ) -> None:
        # The floor stops selling. Buying more of a 7-day-old name sells
        # nothing, so the add path is unchanged.
        self._drifted(runner, entry=100.0, now=20.0, entry_date="2026-07-29")

        result = self._run(runner, 20.0)

        buys = [t for t in result.trades if t.side == "BUY"]
        assert len(buys) == 1 and buys[0].ticker == "DRIFT"

    def test_a_runner_with_a_shorter_floor_trims_sooner(
        self, runner: DailyRunner
    ) -> None:
        # The trim reads the runner's floor, not a second constant.
        runner.min_holding_days = 5
        self._drifted(runner, entry=100.0, now=300.0, entry_date="2026-07-29")

        result = self._run(runner, 300.0)

        assert len([t for t in result.trades if t.side == "SELL"]) == 1


class TestTheBrokerCommission:
    """Every order the runner places pays the broker's flat $2.50.

    Audit ENG-2: the runner charged 0.1% of the gross, so 2,260 share
    changes cost the books $877 where the broker would have taken
    $5,650.
    """

    BROKER = 2.50

    def _single(
        self, runner: DailyRunner, *, shares: float, price: float, cash: float
    ) -> AgentRunResult:
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="DRIFT",
                shares=shares,
                entry_price=price,
                entry_date="2026-07-01",
                current_price=price,
            )
        )
        p.cash = cash
        p.save(directory=runner.portfolio_dir)
        adapter = _StubAdapter("stub_agent", [_target("DRIFT", weight=0.5)])
        runner.price_loader = _StubPriceLoader({"DRIFT": price})  # type: ignore[assignment]
        return runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["DRIFT"],
            {"DRIFT": price},
            {"DRIFT": None},
        )

    def test_every_opening_buy_pays_the_flat_commission(
        self, runner: DailyRunner
    ) -> None:
        n = 27
        prices: dict[str, float | None] = {
            f"T{i:02d}": 37.0 + i * 11.3 for i in range(n)
        }
        targets = [
            _target(t, weight=1.0 / n, rank=i + 1)
            for i, t in enumerate(sorted(prices))
        ]
        runner.price_loader = _StubPriceLoader(dict(prices))  # type: ignore[assignment]
        result = runner._run_one(
            _StubAdapter("stub_agent", targets),  # type: ignore[arg-type]
            AS_OF,
            sorted(prices),
            prices,
            dict.fromkeys(prices),
        )
        buys = [t for t in result.trades if t.side == "BUY"]
        assert buys
        for trade in buys:
            assert trade.cost_paid == pytest.approx(self.BROKER), trade.ticker
        p = result.portfolio
        assert p.cumulative_costs == pytest.approx(self.BROKER * len(result.trades))
        assert p.cash >= 0

    def test_the_runner_passes_its_own_commission_to_every_order(
        self, runner: DailyRunner
    ) -> None:
        # Not the portfolio's default: a runner told $1 charges $1, on
        # the trim as on the buy, so the setting is the one in force.
        runner.commission = 1.0
        result = self._single(runner, shares=50.0, price=300.0, cash=5_000.0)
        assert result.trades
        for trade in result.trades:
            assert trade.cost_paid == pytest.approx(1.0)

    def test_the_plan_never_spends_more_than_the_cash(self) -> None:
        """Commissions included, the planned outlay fits the book.

        One name at $100 against $1,000 of cash and a 100% slot: ten
        shares is $1,000 of stock and $2.50 the book does not have.
        """
        from core.live.runner import plan_whole_share_buys

        plan = plan_whole_share_buys(
            [_target("X", weight=1.0)],
            {"X": 100.0},
            nav=1_000.0,
            cash=1_000.0,
            cost_bps=0.0,
            commission=self.BROKER,
            band=0.25,
        )
        assert plan == {"X": 9}

    def test_the_plan_charges_one_commission_per_name(self) -> None:
        """Each name pays one commission, not one per share.

        Three names, $500 of cash, $100 shares: the slots are $166.67,
        so the first pass places one share of each for $307.50 — three
        orders. The $192.50 left tops one of them up by a share; that
        is the same order, so it costs $100, not $102.50. The book ends
        with four shares and three commissions, inside its cash.
        """
        from core.live.runner import plan_whole_share_buys

        targets = [_target(t, weight=1 / 3) for t in ("A", "B", "C")]
        prices: dict[str, float | None] = {"A": 100.0, "B": 100.0, "C": 100.0}
        plan = plan_whole_share_buys(
            targets, prices, nav=500.0, cash=500.0,
            cost_bps=0.0, commission=self.BROKER, band=0.25,
        )
        outlay = sum(n * 100.0 + self.BROKER for n in plan.values())
        assert outlay <= 500.0
        assert sum(plan.values()) == 4

    def test_a_trim_too_small_to_carry_the_commission_is_skipped(
        self, runner: DailyRunner
    ) -> None:
        # 50 shares at $8 = $400 against $200 cash: NAV 600, target 300,
        # drift +33% — outside the band. The trim is 12 shares, $96, on
        # which $2.50 is 2.6%: more than the 1% the book will pay to
        # rebalance. Left alone.
        result = self._single(runner, shares=50.0, price=8.0, cash=200.0)
        assert result.trades == []
        assert result.portfolio.positions[0].shares == 50.0
        assert result.portfolio.cumulative_costs == 0.0

    def test_an_add_too_small_to_carry_the_commission_is_skipped(
        self, runner: DailyRunner
    ) -> None:
        # 50 shares at $4 = $200 against $400 cash: NAV 600, target 300,
        # drift -33%. The add is ~$100, on which $2.50 is 2.5%.
        result = self._single(runner, shares=50.0, price=4.0, cash=400.0)
        assert result.trades == []
        assert result.portfolio.cash == pytest.approx(400.0)

    def test_a_rebalance_large_enough_still_trades(
        self, runner: DailyRunner
    ) -> None:
        # 50 shares at $300 = $15,000 against $5,000 cash: the trim is
        # 16 shares, $4,800, on which $2.50 is 0.05%.
        result = self._single(runner, shares=50.0, price=300.0, cash=5_000.0)
        sells = [t for t in result.trades if t.side == "SELL"]
        assert len(sells) == 1
        assert sells[0].cost_paid == pytest.approx(self.BROKER)

    def test_the_threshold_is_inclusive_at_one_percent(self) -> None:
        from core.live.runner import commission_dominates

        # $2.50 is exactly 1% of $250: that trade is worth placing.
        assert commission_dominates(250.0, commission=2.50) is False
        assert commission_dominates(249.99, commission=2.50) is True
        # A free order is never dominated, however small.
        assert commission_dominates(0.01, commission=0.0) is False


# ---------------------------------------------------------------------------
# Dividends
# ---------------------------------------------------------------------------
class _StubPriceLoaderWithDividends(_StubPriceLoader):
    def __init__(
        self,
        prices: dict[str, float | None],
        dividends: dict[str, list[tuple[date, float]]] | None = None,
    ) -> None:
        super().__init__(prices)
        self._dividends = dividends or {}
        self.dividend_queries: list[tuple[str, date, date]] = []

    def dividends_between(self, ticker, start, end):  # type: ignore[no-untyped-def]
        from core.backtest.data_loader import _to_date

        s, e = _to_date(start), _to_date(end)
        self.dividend_queries.append((ticker, s, e))
        return [(d, a) for d, a in self._dividends.get(ticker, []) if s < d <= e]


class TestDividends:
    """Marking at the close puts the ex-date price drop into NAV while
    the payment never arrives, so the recorded return was price return.
    The penalty is proportional to yield — doctrine-correlated, and so
    it silently favoured the low-yield agents."""

    def _held(self, runner: DailyRunner, *, entry_date: str = "2026-07-01") -> None:
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="DIV",
                shares=100.0,
                entry_price=50.0,
                entry_date=entry_date,
                current_price=50.0,
                why_en="",
                why_he="",
            )
        )
        p.cash = 1_000.0
        p.last_open_run = "2026-08-01T14:00:00+00:00"
        # A book that has been running since before the ex-dates under
        # test. Without an inception the first settlement stamps today
        # and pays nothing earlier, which is the correct default for a
        # book of unknown age but not what these tests are about.
        p.dividend_floor_date = "2026-07-01"
        p.save(directory=runner.portfolio_dir)

    def _run(self, runner: DailyRunner, loader) -> object:  # type: ignore[no-untyped-def]
        runner.price_loader = loader
        adapter = _StubAdapter("stub_agent", [_target("DIV", weight=0.9)])
        return runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["DIV"],
            {"DIV": 50.0},
            {"DIV": None},
        )

    def test_a_dividend_becomes_cash(self, runner: DailyRunner) -> None:
        self._held(runner)
        loader = _StubPriceLoaderWithDividends(
            {"DIV": 50.0}, {"DIV": [(date(2026, 8, 4), 1.00)]}
        )

        result = self._run(runner, loader)

        # 100 shares x $1.00.
        assert result.portfolio.cumulative_dividends == pytest.approx(100.0)

    def test_the_same_dividend_is_not_paid_twice(
        self, runner: DailyRunner
    ) -> None:
        self._held(runner)
        loader = _StubPriceLoaderWithDividends(
            {"DIV": 50.0}, {"DIV": [(date(2026, 8, 4), 1.00)]}
        )
        self._run(runner, loader)
        second = runner._run_one(
            _StubAdapter("stub_agent", [_target("DIV", weight=0.9)]),  # type: ignore[arg-type]
            AS_OF,
            ["DIV"],
            {"DIV": 50.0},
            {"DIV": None},
            force=True,  # bypass the idempotency guard to isolate this
        )

        assert second.portfolio.cumulative_dividends == pytest.approx(100.0)

    def test_a_dividend_before_purchase_is_not_collected(
        self, runner: DailyRunner
    ) -> None:
        self._held(runner, entry_date="2026-08-05")
        loader = _StubPriceLoaderWithDividends(
            {"DIV": 50.0}, {"DIV": [(date(2026, 8, 2), 1.00)]}
        )

        result = self._run(runner, loader)

        assert result.portfolio.cumulative_dividends == 0.0

    def test_dividends_are_settled_before_the_sell(
        self, runner: DailyRunner
    ) -> None:
        # A name leaving the target list still collects income whose
        # ex-date has already passed. Settling after the sell forfeits it.
        self._held(runner)
        loader = _StubPriceLoaderWithDividends(
            {"DIV": 50.0, "OTHER": 10.0}, {"DIV": [(date(2026, 8, 4), 1.00)]}
        )
        runner.price_loader = loader
        adapter = _StubAdapter("stub_agent", [_target("OTHER", weight=0.9)])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["DIV", "OTHER"],
            {"DIV": 50.0, "OTHER": 10.0},
            {"DIV": None, "OTHER": None},
        )

        assert not result.portfolio.has("DIV")  # it was sold
        assert result.portfolio.cumulative_dividends == pytest.approx(100.0)

    def test_dividends_reach_the_snapshot(self, runner: DailyRunner) -> None:
        from core.live.snapshots import make_snapshot

        self._held(runner)
        loader = _StubPriceLoaderWithDividends(
            {"DIV": 50.0}, {"DIV": [(date(2026, 8, 4), 1.00)]}
        )
        result = self._run(runner, loader)

        snap = make_snapshot(result.portfolio, as_of=AS_OF, trades=[])

        assert snap.dividends_received_usd == pytest.approx(100.0)

    def _rerun(self, runner: DailyRunner, loader) -> object:  # type: ignore[no-untyped-def]
        """Another run of the same day, bypassing the idempotency guard."""
        runner.price_loader = loader
        return runner._run_one(
            _StubAdapter("stub_agent", [_target("DIV", weight=0.9)]),  # type: ignore[arg-type]
            AS_OF,
            ["DIV"],
            {"DIV": 50.0},
            {"DIV": None},
            force=True,
        )

    def test_a_run_stamped_today_still_collects(
        self, runner: DailyRunner
    ) -> None:
        """The production condition, which nothing used to cover.

        Settlement took its floor from ``last_open_run``, and the
        previous run had already advanced that to the current date — so
        the query asked for dividends over an empty interval, and 68
        consecutive daily runs credited $0.00 while eleven ex-dates
        passed on Neff's book. Every existing test set that stamp in the
        past, a state production is never in.
        """
        self._held(runner)
        p = LivePortfolio.load_or_seed("stub_agent", directory=runner.portfolio_dir)
        p.last_open_run = f"{AS_OF.isoformat()}T15:11:28+00:00"
        p.save(directory=runner.portfolio_dir)
        loader = _StubPriceLoaderWithDividends(
            {"DIV": 50.0}, {"DIV": [(date(2026, 8, 4), 1.00)]}
        )

        result = self._run(runner, loader)

        assert result.portfolio.cumulative_dividends == pytest.approx(100.0)

    def test_a_dividend_learned_late_is_still_collected(
        self, runner: DailyRunner
    ) -> None:
        """Self-healing, which a one-day window cannot do.

        yfinance publishes an ex-date row when it publishes it. Under a
        window bounded by the previous run, a row arriving even a day
        late fell behind the mark and was lost for good.
        """
        self._held(runner)
        # The cache does not know about the ex-date yet.
        first = self._run(runner, _StubPriceLoaderWithDividends({"DIV": 50.0}, {}))
        assert first.portfolio.cumulative_dividends == 0.0

        # It shows up later, dated three days back.
        late = _StubPriceLoaderWithDividends(
            {"DIV": 50.0}, {"DIV": [(date(2026, 8, 4), 1.00)]}
        )
        second = self._rerun(runner, late)

        assert second.portfolio.cumulative_dividends == pytest.approx(100.0)

    def test_the_paid_record_is_what_prevents_the_double_pay(
        self, runner: DailyRunner
    ) -> None:
        self._held(runner)
        loader = _StubPriceLoaderWithDividends(
            {"DIV": 50.0}, {"DIV": [(date(2026, 8, 4), 1.00)]}
        )

        result = self._run(runner, loader)

        assert result.portfolio.paid_dividends == {"DIV": ["2026-08-04"]}

    def test_closing_a_line_forgets_its_dividend_history(
        self, runner: DailyRunner
    ) -> None:
        # Otherwise the map grows with the book's whole history rather
        # than its size. Safe because a re-entry carries a later
        # entry_date, which excludes the old ex-dates on its own.
        self._held(runner)
        loader = _StubPriceLoaderWithDividends(
            {"DIV": 50.0, "OTHER": 10.0}, {"DIV": [(date(2026, 8, 4), 1.00)]}
        )
        runner.price_loader = loader

        result = runner._run_one(
            _StubAdapter("stub_agent", [_target("OTHER", weight=0.9)]),  # type: ignore[arg-type]
            AS_OF,
            ["DIV", "OTHER"],
            {"DIV": 50.0, "OTHER": 10.0},
            {"DIV": None, "OTHER": None},
        )

        assert result.portfolio.cumulative_dividends == pytest.approx(100.0)
        assert "DIV" not in result.portfolio.paid_dividends

    def test_inception_is_stamped_once_and_never_moves(
        self, runner: DailyRunner
    ) -> None:
        self._held(runner)

        result = self._run(runner, _StubPriceLoaderWithDividends({"DIV": 50.0}, {}))

        assert result.portfolio.dividend_floor_date == "2026-07-01"

    def test_a_book_of_unknown_age_stamps_today_and_pays_nothing_earlier(
        self, runner: DailyRunner
    ) -> None:
        # The safe direction on an upgrade: a missing dividend understates
        # a return, an invented one overstates it.
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="DIV",
                shares=100.0,
                entry_price=50.0,
                entry_date="2026-07-01",
                current_price=50.0,
                why_en="",
                why_he="",
            )
        )
        p.cash = 1_000.0  # no dividend_floor_date
        p.save(directory=runner.portfolio_dir)
        loader = _StubPriceLoaderWithDividends(
            {"DIV": 50.0}, {"DIV": [(date(2026, 8, 4), 1.00)]}
        )

        result = self._run(runner, loader)

        assert result.portfolio.cumulative_dividends == 0.0
        assert result.portfolio.dividend_floor_date == AS_OF.isoformat()

    def test_a_freshly_seeded_book_does_not_harvest_history(
        self, runner: DailyRunner
    ) -> None:
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="DIV",
                shares=100.0,
                entry_price=50.0,
                entry_date="2020-01-01",
                current_price=50.0,
                why_en="",
                why_he="",
            )
        )
        p.cash = 1_000.0  # no last_open_run, no last_dividend_date
        p.save(directory=runner.portfolio_dir)
        loader = _StubPriceLoaderWithDividends(
            {"DIV": 50.0}, {"DIV": [(date(2021, 5, 4), 5.00)]}
        )

        result = self._run(runner, loader)

        assert result.portfolio.cumulative_dividends == 0.0


# ---------------------------------------------------------------------------
# Stock splits (audit item 22 / CE-5)
# ---------------------------------------------------------------------------
class _StubPriceLoaderWithSplits(_StubPriceLoader):
    def __init__(
        self,
        prices: dict[str, float | None],
        splits: dict[str, list[tuple[date, float]]] | None = None,
    ) -> None:
        super().__init__(prices)
        self._splits = splits or {}
        self.split_queries: list[tuple[str, date, date]] = []

    def splits_between(self, ticker, start, end):  # type: ignore[no-untyped-def]
        from core.backtest.data_loader import _to_date

        s, e = _to_date(start), _to_date(end)
        self.split_queries.append((ticker, s, e))
        return [(d, r) for d, r in self._splits.get(ticker, []) if s < d <= e]


class TestSplitsAdjustTheBook:
    """The cache is split-adjusted, so on an ex-date the quote halves
    (2:1) while the book still held the old share count — the line's
    value halved with nothing having happened to the investment."""

    EX = AS_OF  # the split takes effect on the day being run

    def _held(
        self,
        runner: DailyRunner,
        *,
        shares: float = 10.0,
        entry_date: str = "2026-07-01",
        last_close: str = "2026-08-04",
    ) -> None:
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="NVDA",
                shares=shares,
                entry_price=100.0,
                entry_date=entry_date,
                current_price=120.0,  # yesterday's close, pre-split
                why_en="",
                why_he="",
            )
        )
        p.cash = 1_000.0
        p.last_close_date = last_close
        p.last_open_date = last_close
        p.last_rebalance_date = "2026-08-03"
        p.save(directory=runner.portfolio_dir)

    def _run(self, runner: DailyRunner, loader, *, weight: float = 0.55):  # type: ignore[no-untyped-def]
        runner.price_loader = loader
        # Keep NVDA a target at about the weight it already has, so the
        # run neither sells nor rebalances it and NAV isolates the split.
        adapter = _StubAdapter("stub_agent", [_target("NVDA", weight=weight)])
        return runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["NVDA"],
            {"NVDA": 60.0},
            {"NVDA": None},
        )

    def test_two_for_one_split_leaves_nav_unchanged(
        self, runner: DailyRunner
    ) -> None:
        self._held(runner)
        nav_before = 1_000.0 + 10 * 120.0
        loader = _StubPriceLoaderWithSplits(
            {"NVDA": 60.0}, {"NVDA": [(self.EX, 2.0)]}
        )

        result = self._run(runner, loader)

        book = result.portfolio
        pos = book.positions[0]
        assert pos.shares == 20.0
        assert pos.shares == int(pos.shares)
        assert pos.entry_price == pytest.approx(50.0)
        assert result.trades == []
        assert book.total_nav == pytest.approx(nav_before)
        # And it is what was saved, not just what was returned.
        saved = LivePortfolio.load_or_seed(
            "stub_agent", directory=runner.portfolio_dir
        )
        assert saved.positions[0].shares == 20.0

    def test_three_for_two_floors_with_cash_in_lieu(
        self, runner: DailyRunner
    ) -> None:
        # 3 shares x 1.5 = 4.5 -> 4 shares and half a share in cash at
        # the post-split price: $40.
        self._held(runner, shares=3.0)
        nav_before = 1_000.0 + 3 * 120.0
        loader = _StubPriceLoaderWithSplits(
            {"NVDA": 80.0}, {"NVDA": [(self.EX, 1.5)]}
        )
        # The stub duck-types PriceDataLoader; it is not a subclass.
        runner.price_loader = loader  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("NVDA", weight=0.25)])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["NVDA"],
            {"NVDA": 80.0},
            {"NVDA": None},
        )

        book = result.portfolio
        assert book.positions[0].shares == 4.0
        assert book.cash == pytest.approx(1_040.0)
        assert book.total_nav == pytest.approx(nav_before)

    def test_the_split_is_journaled_as_a_corporate_action(
        self, runner: DailyRunner, tmp_path: Path
    ) -> None:
        self._held(runner)
        loader = _StubPriceLoaderWithSplits(
            {"NVDA": 60.0}, {"NVDA": [(self.EX, 2.0)]}
        )

        self._run(runner, loader)

        rows = DecisionLogger(root=tmp_path / "decisions").read_day(
            "stub_agent", AS_OF
        )
        splits = [r for r in rows if r.decision == "SPLIT"]
        assert len(splits) == 1
        row = splits[0]
        assert row.ticker == "NVDA"
        assert row.market_conditions["corporate_action"] == "split"
        assert row.market_conditions["ratio"] == 2.0
        assert row.market_conditions["shares_before"] == 10.0
        assert row.market_conditions["shares_after"] == 20.0
        assert row.market_conditions["cash_in_lieu"] == 0.0
        assert row.entry_price == pytest.approx(50.0)
        assert "2-for-1" in (row.rationale or "")

    def test_a_second_run_does_not_split_again(self, runner: DailyRunner) -> None:
        self._held(runner)
        loader = _StubPriceLoaderWithSplits(
            {"NVDA": 60.0}, {"NVDA": [(self.EX, 2.0)]}
        )
        self._run(runner, loader)

        again = runner._run_one(
            _StubAdapter("stub_agent", [_target("NVDA", weight=0.55)]),  # type: ignore[arg-type]
            AS_OF,
            ["NVDA"],
            {"NVDA": 60.0},
            {"NVDA": None},
            force=True,
        )

        assert again.portfolio.positions[0].shares == 20.0

    def test_a_split_on_the_entry_date_is_not_applied(
        self, runner: DailyRunner
    ) -> None:
        # Bought on the ex-date means bought at the post-split price.
        self._held(runner, entry_date=self.EX.isoformat())
        loader = _StubPriceLoaderWithSplits(
            {"NVDA": 60.0}, {"NVDA": [(self.EX, 2.0)]}
        )

        result = self._run(runner, loader)

        assert result.portfolio.positions[0].shares == 10.0

    def test_the_floor_is_the_books_last_mark_and_older_splits_are_left(
        self, runner: DailyRunner
    ) -> None:
        # A split from before this book was last marked is history the
        # book already priced through (or an owner's decision to
        # backfill), not today's event.
        self._held(runner, last_close="2026-08-04")
        loader = _StubPriceLoaderWithSplits(
            {"NVDA": 60.0}, {"NVDA": [(date(2026, 8, 3), 2.0)]}
        )

        result = self._run(runner, loader)

        assert result.portfolio.split_floor_date == "2026-08-04"
        assert result.portfolio.positions[0].shares == 10.0

    def test_a_book_already_marked_today_still_takes_todays_split(
        self, runner: DailyRunner
    ) -> None:
        # Deploy day: the open run (old code) marked the book today, and
        # the close mark is the first run to stamp the floor. A floor of
        # today would exclude today's ex-date for good.
        self._held(runner, last_close="2026-08-04")
        book = LivePortfolio.load_or_seed("stub_agent", directory=runner.portfolio_dir)
        book.last_open_date = AS_OF.isoformat()
        book.save(directory=runner.portfolio_dir)
        runner.price_loader = _StubPriceLoaderWithSplits(  # type: ignore[assignment]
            {"NVDA": 60.0}, {"NVDA": [(self.EX, 2.0)]}
        )
        runner.adapters = [_StubAdapter("stub_agent", [])]  # type: ignore[list-item]

        results = runner.run_mark_to_market(as_of=AS_OF)

        assert results[0].portfolio.split_floor_date == "2026-08-04"
        assert results[0].portfolio.positions[0].shares == 20.0

    def test_a_split_only_day_passes_the_run_gate(
        self, runner: DailyRunner, tmp_path: Path
    ) -> None:
        # The workflow's verify step fails a book whose holdings moved
        # with no ledger day; a split places no order and writes none.
        # Its SPLIT journal row is what puts the change on record.
        from scripts.verify_run_state import check_portfolios

        self._held(runner)
        loader = _StubPriceLoaderWithSplits(
            {"NVDA": 60.0}, {"NVDA": [(self.EX, 2.0)]}
        )

        result = self._run(runner, loader)
        assert result.trades == []

        report = check_portfolios(
            runner.portfolio_dir,
            mode="open",
            as_of=AS_OF,
            previous=lambda path: {"NVDA": 10.0},
            ledger_dir=tmp_path / "trades",
            journal_dir=tmp_path / "decisions",
        )

        # Freshness is not asserted: the runner stamps the wall clock,
        # not AS_OF. What matters here is the ledger check.
        assert report.unledgered == []
        assert report.unreadable == []
        assert report.split_adjusted == ["stub_agent (NVDA 10→20)"]

    def test_a_split_learned_a_day_late_is_still_applied(
        self, runner: DailyRunner
    ) -> None:
        # The floor is stamped once and never moved, so an ex-date the
        # cache records after the day it happened is still caught.
        self._held(runner, last_close="2026-08-03")
        book = LivePortfolio.load_or_seed("stub_agent", directory=runner.portfolio_dir)
        book.split_floor_date = "2026-08-03"
        book.save(directory=runner.portfolio_dir)
        loader = _StubPriceLoaderWithSplits(
            {"NVDA": 60.0}, {"NVDA": [(date(2026, 8, 4), 2.0)]}
        )

        result = self._run(runner, loader)

        assert result.portfolio.positions[0].shares == 20.0

    def test_a_failed_lookup_changes_nothing_and_is_said(
        self, runner: DailyRunner
    ) -> None:
        # The project logs through loguru, which caplog never sees.
        from loguru import logger as loguru_logger

        self._held(runner)

        class _Broken(_StubPriceLoader):
            def splits_between(self, ticker, start, end):  # type: ignore[no-untyped-def]
                raise RuntimeError("sqlite is locked")

        seen: list[str] = []
        sink = loguru_logger.add(
            lambda m: seen.append(m.record["message"]), level="WARNING"
        )
        try:
            result = self._run(runner, _Broken({"NVDA": 60.0}))
        finally:
            loguru_logger.remove(sink)

        assert result.portfolio.positions[0].shares == 10.0
        assert any("split lookup NVDA failed" in m for m in seen)

    def test_an_implausible_ratio_is_refused_at_error_level(
        self, runner: DailyRunner
    ) -> None:
        # A 1000:1 from the vendor is a glitch, not a corporate action;
        # refusing it must reach the ERROR log the owner reads, not sit
        # among the warnings.
        from loguru import logger as loguru_logger

        self._held(runner)
        loader = _StubPriceLoaderWithSplits(
            {"NVDA": 60.0}, {"NVDA": [(self.EX, 1000.0)]}
        )

        seen: list[str] = []
        sink = loguru_logger.add(
            lambda m: seen.append(m.record["message"]), level="ERROR"
        )
        try:
            result = self._run(runner, loader)
        finally:
            loguru_logger.remove(sink)

        assert result.portfolio.positions[0].shares == 10.0
        assert any("split of NVDA refused" in m for m in seen)

    def _held_with_aapl(self, runner: DailyRunner) -> None:
        self._held(runner)
        book = LivePortfolio.load_or_seed("stub_agent", directory=runner.portfolio_dir)
        book.positions.append(
            Position(
                ticker="AAPL",
                shares=10.0,
                entry_price=100.0,
                entry_date="2026-07-01",
                current_price=100.0,  # yesterday's close, pre-split
                why_en="",
                why_he="",
            )
        )
        book.save(directory=runner.portfolio_dir)

    class _BrokenForNvda(_StubPriceLoaderWithSplits):
        def splits_between(
            self, ticker: str, start: date | str, end: date | str
        ) -> list[tuple[date, float]]:
            from core.backtest.data_loader import _to_date

            if ticker == "NVDA":
                raise RuntimeError("sqlite is locked")
            s, e = _to_date(start), _to_date(end)
            return [(d, r) for d, r in self._splits.get(ticker, []) if s < d <= e]

    def test_a_failed_lookup_leaves_the_name_unmarked_and_untraded(
        self, runner: DailyRunner, errors: list[str]
    ) -> None:
        # NVDA's quote of 60 may be on the post-split basis while its 10
        # shares are not: marked, the line would read $600 instead of
        # $1,200, and a 90% target would top it up on that wrong weight.
        self._held_with_aapl(runner)
        loader = self._BrokenForNvda(
            {"NVDA": 60.0, "AAPL": 50.0}, {"AAPL": [(self.EX, 2.0)]}
        )
        runner.price_loader = loader  # type: ignore[assignment]
        adapter = _StubAdapter(
            "stub_agent",
            [_target("NVDA", weight=0.9), _target("AAPL", weight=0.31, rank=2)],
        )

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["NVDA", "AAPL"],
            {"NVDA": 60.0, "AAPL": 50.0},
            {"NVDA": None, "AAPL": None},
        )

        by_ticker = {p.ticker: p for p in result.portfolio.positions}
        nvda = by_ticker["NVDA"]
        assert nvda.shares == 10.0
        assert nvda.current_price == 120.0
        assert [t for t in result.trades if t.ticker == "NVDA"] == []
        # The other holding is split and marked as normal.
        assert by_ticker["AAPL"].shares == 20.0
        assert by_ticker["AAPL"].current_price == 50.0
        assert any("split lookup NVDA failed" in m for m in errors)

    def test_a_failed_lookup_leaves_the_close_mark_alone(
        self, runner: DailyRunner, errors: list[str]
    ) -> None:
        self._held_with_aapl(runner)
        runner.price_loader = self._BrokenForNvda(  # type: ignore[assignment]
            {"NVDA": 60.0, "AAPL": 50.0}, {"AAPL": [(self.EX, 2.0)]}
        )
        runner.adapters = [_StubAdapter("stub_agent", [])]  # type: ignore[list-item]

        results = runner.run_mark_to_market(as_of=AS_OF)

        by_ticker = {p.ticker: p for p in results[0].portfolio.positions}
        assert by_ticker["NVDA"].shares == 10.0
        assert by_ticker["NVDA"].current_price == 120.0
        assert by_ticker["AAPL"].shares == 20.0
        assert by_ticker["AAPL"].current_price == 50.0
        assert any("split lookup NVDA failed" in m for m in errors)

    def test_the_close_of_day_mark_applies_it(self, runner: DailyRunner) -> None:
        self._held(runner)
        nav_before = 1_000.0 + 10 * 120.0
        runner.price_loader = _StubPriceLoaderWithSplits(  # type: ignore[assignment]
            {"NVDA": 60.0}, {"NVDA": [(self.EX, 2.0)]}
        )
        runner.adapters = [_StubAdapter("stub_agent", [])]  # type: ignore[list-item]

        results = runner.run_mark_to_market(as_of=AS_OF)

        book = results[0].portfolio
        assert book.positions[0].shares == 20.0
        assert book.total_nav == pytest.approx(nav_before)


# ---------------------------------------------------------------------------
# What the strategy is allowed to see
# ---------------------------------------------------------------------------
class TestHeldPositionsAreVisible:
    """Without this a strategy cannot express an exit rule at all, so
    every agent inherited the same one — 'sold because you left today's
    top N'. Greenblatt's twelve-month hold, Fisher's decades and
    Schloss's fifty-percent rule were all unimplementable."""

    def test_the_strategy_receives_its_holdings(
        self, runner: DailyRunner
    ) -> None:
        _seed_holding(
            runner, "stub_agent", "OWNED", entry_price=40.0, current_price=60.0
        )
        runner.price_loader = _StubPriceLoader({"OWNED": 60.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("OWNED")])

        runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["OWNED"],
            {"OWNED": 60.0},
            {"OWNED": None},
        )

        held = adapter.last_held
        assert held is not None
        assert set(held) == {"OWNED"}
        pos = held["OWNED"]
        assert pos.shares == pytest.approx(10.0)
        assert pos.entry_price == pytest.approx(40.0)
        assert pos.entry_date == date(2026, 7, 1)
        assert pos.current_price == pytest.approx(60.0)

    def test_an_empty_book_is_an_empty_mapping(
        self, runner: DailyRunner
    ) -> None:
        # Not None — "I own nothing" is different from "I was not told".
        runner.price_loader = _StubPriceLoader({"NEW": 10.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("NEW")])

        runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["NEW"],
            {"NEW": 10.0},
            {"NEW": None},
        )

        assert adapter.last_held == {}

    def test_days_held_and_return_are_derivable(self) -> None:
        from core.backtest.strategy_runner import HeldPosition

        pos = HeldPosition(
            ticker="X",
            shares=10.0,
            entry_price=40.0,
            entry_date=date(2026, 7, 1),
            current_price=60.0,
        )

        assert pos.days_held_at(date(2026, 8, 5)) == 35
        assert pos.return_pct == pytest.approx(50.0)

    def test_return_is_none_on_an_unusable_basis(self) -> None:
        from core.backtest.strategy_runner import HeldPosition

        pos = HeldPosition(
            ticker="X",
            shares=10.0,
            entry_price=0.0,
            entry_date=date(2026, 7, 1),
            current_price=60.0,
        )

        assert pos.return_pct is None

    def test_a_same_day_purchase_is_zero_days_held(self) -> None:
        from core.backtest.strategy_runner import HeldPosition

        pos = HeldPosition(
            ticker="X",
            shares=1.0,
            entry_price=10.0,
            entry_date=AS_OF,
            current_price=10.0,
        )

        assert pos.days_held_at(AS_OF) == 0


class TestTheGuardComparesLogicalDates:
    """The idempotency guard read a wall-clock stamp as a logical date.

    ``last_open_run`` is ``now_iso()`` — when the run happened. ``as_of``
    is which trading day it covers. The guard compared
    ``last_open_run[:10]`` to ``as_of``, which agree only when a run
    executes on the same calendar day it represents.

    That is not guaranteed. A run dispatched near the UTC day boundary,
    or a watchdog make-up for a missed session, stamps one date while
    covering another. The 2026-08-07 00:51Z make-up run is exactly that
    shape. Two failures follow: a genuine repeat is not recognised, and
    worse, the *next* day's run sees its own date already stamped and
    skips itself — a trading day lost with only a log line.

    It also made the test suite date-dependent. These tests passed when
    written on 2026-08-05 and CI failed on 2026-08-07 with the same
    code.
    """

    @staticmethod
    def _scan(runner: DailyRunner, as_of: date) -> object:
        adapter = _StubAdapter("stub_agent", [_target("KEEP", weight=0.5)])
        return runner._run_one(
            adapter,  # type: ignore[arg-type]
            as_of,
            ["KEEP"],
            {"KEEP": 20.0},
            {"KEEP": None},
        )

    def test_a_repeat_is_skipped_whatever_today_is(
        self, runner: DailyRunner
    ) -> None:
        # A date deliberately far from any plausible "today", so the
        # wall-clock stamp cannot accidentally match it.
        runner.price_loader = _StubPriceLoader({"KEEP": 20.0})  # type: ignore[assignment]
        past = date(2019, 3, 14)

        first = self._scan(runner, past)
        second = self._scan(runner, past)

        assert first.trades  # type: ignore[attr-defined]
        assert second.skipped is True  # type: ignore[attr-defined]

    def test_a_new_date_is_not_skipped_after_a_midnight_crossing(
        self, runner: DailyRunner
    ) -> None:
        # The damaging half. Under the old guard the first run stamped
        # today's wall clock, so a run for *today* was refused as a
        # duplicate of a run that actually covered yesterday.
        runner.price_loader = _StubPriceLoader({"KEEP": 20.0})  # type: ignore[assignment]

        self._scan(runner, date(2019, 3, 14))
        next_day = self._scan(runner, date(2019, 3, 15))

        assert next_day.skipped is False  # type: ignore[attr-defined]

    def test_the_wall_clock_stamp_is_still_wall_clock(
        self, runner: DailyRunner
    ) -> None:
        # verify_run_state reads last_open_run to answer "did a run
        # happen recently", which is a freshness question and wants the
        # real time. Only the guard changed.
        runner.price_loader = _StubPriceLoader({"KEEP": 20.0})  # type: ignore[assignment]
        past = date(2019, 3, 14)

        result = self._scan(runner, past)

        p = result.portfolio  # type: ignore[attr-defined]
        assert p.last_open_date == "2019-03-14"
        assert not p.last_open_run.startswith("2019-03-14")

    def test_a_portfolio_from_before_the_field_existed_runs(
        self, runner: DailyRunner
    ) -> None:
        # Existing JSON on disk has no last_open_date. Empty never
        # equals an as_of, so the first run after the upgrade proceeds
        # rather than silently skipping.
        runner.price_loader = _StubPriceLoader({"KEEP": 20.0})  # type: ignore[assignment]

        result = self._scan(runner, date(2019, 3, 14))

        assert result.skipped is False  # type: ignore[attr-defined]


class TestTheHoldingFloor:
    """A name that slips a rank for a day must not be sold.

    Across the ten agents the decision logs hold 246 completed
    round-trips. Graham accounts for 75: he bought CRMD on 2026-05-12,
    sold it on the 14th, bought back on the 15th, sold again on the
    18th - four entries and three exits in three months, on a doctrine
    whose holding period is measured in years.
    """

    @staticmethod
    def _pos(ticker: str, entry_date: str) -> Position:
        return Position(
            ticker=ticker,
            shares=10.0,
            entry_price=20.0,
            entry_date=entry_date,
            current_price=20.0,
        )

    def test_a_fresh_position_is_not_rotated_out(
        self, runner: DailyRunner
    ) -> None:
        # Bought two days ago and absent from today's targets — exactly
        # the CRMD shape.
        runner.price_loader = _StubPriceLoader({"KEEP": 20.0, "NEW": 20.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("NEW", weight=0.5)])
        portfolio = LivePortfolio(agent="stub_agent", cash=5_000.0)
        portfolio.positions.append(self._pos("KEEP", "2026-08-03"))
        portfolio.save(directory=runner.portfolio_dir)

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            date(2026, 8, 5),
            ["NEW"],
            {"NEW": 20.0, "KEEP": 20.0},
            {"NEW": None, "KEEP": None},
        )

        assert "KEEP" in {p.ticker for p in result.portfolio.positions}

    def test_an_aged_position_is_rotated_out(self, runner: DailyRunner) -> None:
        # Past the floor, the rotation works exactly as before.
        runner.price_loader = _StubPriceLoader({"KEEP": 20.0, "NEW": 20.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("NEW", weight=0.5)])
        portfolio = LivePortfolio(agent="stub_agent", cash=5_000.0)
        portfolio.positions.append(self._pos("KEEP", "2026-01-05"))
        portfolio.save(directory=runner.portfolio_dir)

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            date(2026, 8, 5),
            ["NEW"],
            {"NEW": 20.0, "KEEP": 20.0},
            {"NEW": None, "KEEP": None},
        )

        assert "KEEP" not in {p.ticker for p in result.portfolio.positions}

    def test_the_floor_is_thirty_days(self) -> None:
        assert DEFAULT_MIN_HOLDING_DAYS == 30

    def test_an_unreadable_entry_date_does_not_trap_a_position(self) -> None:
        # "Cannot tell" must not become an infinite floor - a position
        # with a corrupt date should still be sellable.
        assert pos_age_days(Position(
            ticker="ODD", shares=1.0, entry_price=1.0,
            entry_date="not-a-date", current_price=1.0,
        ), date(2026, 8, 5)) is None

    def test_age_counts_from_entry(self) -> None:
        assert pos_age_days(
            Position(ticker="X", shares=1.0, entry_price=1.0,
                     entry_date="2026-07-06", current_price=1.0),
            date(2026, 8, 5),
        ) == 30


class TestAgentFilter:
    """`--agent` restricts the run without silently running nothing.

    Written because the Council joined the roster after the eleven had
    already traded, and catching it up must not move eleven other books
    on a day the schedule did not intend to touch them.
    """

    @staticmethod
    def _adapters() -> list[_StubAdapter]:
        return [
            _StubAdapter("benjamin_graham", []),
            _StubAdapter("walter_schloss", []),
            _StubAdapter("mohnish_pabrai", []),
        ]

    def test_keeps_only_the_named_agent(self) -> None:
        kept = _filter_adapters(self._adapters(), ["mohnish_pabrai"])  # type: ignore[arg-type]
        assert [a.name for a in kept] == ["mohnish_pabrai"]

    def test_keeps_several(self) -> None:
        kept = _filter_adapters(
            self._adapters(),  # type: ignore[arg-type]
            ["mohnish_pabrai", "benjamin_graham"],
        )
        assert {a.name for a in kept} == {"mohnish_pabrai", "benjamin_graham"}

    def test_registration_order_is_preserved_not_argument_order(self) -> None:
        # The Council is registered last on purpose. Argument order must
        # not be able to run it before an agent it reads.
        kept = _filter_adapters(
            self._adapters(),  # type: ignore[arg-type]
            ["mohnish_pabrai", "benjamin_graham"],
        )
        assert [a.name for a in kept] == ["benjamin_graham", "mohnish_pabrai"]

    def test_names_are_matched_case_insensitively_and_trimmed(self) -> None:
        kept = _filter_adapters(self._adapters(), ["  Mohnish_Pabrai "])  # type: ignore[arg-type]
        assert [a.name for a in kept] == ["mohnish_pabrai"]

    def test_an_unknown_name_raises_rather_than_running_nothing(self) -> None:
        # The failure this guards: a typo produces an empty run, no
        # portfolio is written, and the report says success — which is
        # exactly what a quiet day looks like.
        with pytest.raises(ValueError, match="unknown agent"):
            _filter_adapters(self._adapters(), ["the_counsel"])  # type: ignore[arg-type]

    def test_the_error_lists_the_valid_names(self) -> None:
        with pytest.raises(ValueError, match="benjamin_graham"):
            _filter_adapters(self._adapters(), ["nope"])  # type: ignore[arg-type]

    def test_one_bad_name_among_good_ones_still_raises(self) -> None:
        with pytest.raises(ValueError, match="nope"):
            _filter_adapters(
                self._adapters(),  # type: ignore[arg-type]
                ["mohnish_pabrai", "nope"],
            )

    def test_an_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="no names"):
            _filter_adapters(self._adapters(), [])  # type: ignore[arg-type]

    def test_blank_strings_are_not_names(self) -> None:
        with pytest.raises(ValueError, match="no names"):
            _filter_adapters(self._adapters(), ["", "   "])  # type: ignore[arg-type]

    def test_the_runner_applies_the_filter(self, tmp_path: Path) -> None:
        runner = DailyRunner(
            market="US",
            adapters=self._adapters(),  # type: ignore[arg-type]
            portfolio_dir=tmp_path / "portfolios",
            price_loader=_StubPriceLoader({}),  # type: ignore[arg-type]
            universe=object(),  # type: ignore[arg-type]
            pit_loader=object(),  # type: ignore[arg-type]
            cache=object(),  # type: ignore[arg-type]
            decision_logger=DecisionLogger(root=tmp_path / "decisions"),
            only_agents=["mohnish_pabrai"],
        )
        assert [a.name for a in runner.adapters] == ["mohnish_pabrai"]

    def test_none_runs_everyone(self, tmp_path: Path) -> None:
        runner = DailyRunner(
            market="US",
            adapters=self._adapters(),  # type: ignore[arg-type]
            portfolio_dir=tmp_path / "portfolios",
            price_loader=_StubPriceLoader({}),  # type: ignore[arg-type]
            universe=object(),  # type: ignore[arg-type]
            pit_loader=object(),  # type: ignore[arg-type]
            cache=object(),  # type: ignore[arg-type]
            decision_logger=DecisionLogger(root=tmp_path / "decisions"),
            only_agents=None,
        )
        assert len(runner.adapters) == 3


class TestAgentFlagParsing:
    """The CLI flag must be repeatable and default to everyone."""

    def test_absent_means_all(self) -> None:
        assert parse_args([]).agents is None

    def test_one(self) -> None:
        assert parse_args(["--agent", "mohnish_pabrai"]).agents == ["mohnish_pabrai"]

    def test_repeatable(self) -> None:
        args = parse_args(["--agent", "mohnish_pabrai", "--agent", "john_neff"])
        assert args.agents == ["mohnish_pabrai", "john_neff"]


class TestForcedExits:
    """A terminal filing must not wait out the churn floor.

    The minimum-holding floor was written against 246 round-trips of
    rank oscillation and is doing real work. It was never meant to sit
    on an 8-K item 4.02 for the remainder of a month, and under
    COUNCIL_SELECTION E2 those sell next session, "no council, no
    discussion".
    """

    @staticmethod
    def _adapter(name: str, targets: list[LiveTarget], forced: list[str]):
        class _Forcing(_StubAdapter):
            def run_scan(self, as_of, universe, prices, fundamentals, *, held=None):
                result = super().run_scan(
                    as_of, universe, prices, fundamentals, held=held
                )
                result.forced_exits = forced
                return result

        return _Forcing(name, targets)

    def _run(self, runner: DailyRunner, adapter, prices: dict[str, float | None]):
        runner.price_loader = _StubPriceLoader(prices)  # type: ignore[assignment]
        return runner._run_one(
            adapter,
            AS_OF,
            list(prices),
            dict(prices),
            dict.fromkeys(prices),
        )

    def test_a_forced_exit_beats_the_holding_floor(
        self, runner: DailyRunner
    ) -> None:
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="BAD", shares=10.0, entry_price=50.0,
                # Twelve days old, well inside the thirty-day floor.
                entry_date=(AS_OF - timedelta(days=12)).isoformat(),
                current_price=50.0, why_en="s", why_he="s",
            )
        )
        p.cash = 1_000.0
        p.save(directory=runner.portfolio_dir)

        adapter = self._adapter("stub_agent", [_target("KEEP")], ["BAD"])
        result = self._run(runner, adapter, {"KEEP": 20.0, "BAD": 45.0})

        sells = [t for t in result.trades if t.side == "SELL"]
        assert [s.ticker for s in sells] == ["BAD"]
        assert not result.portfolio.has("BAD")

    def test_an_ordinary_exit_still_waits_out_the_floor(
        self, runner: DailyRunner
    ) -> None:
        """The churn guard must survive the exemption."""
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="SLIP", shares=10.0, entry_price=50.0,
                entry_date=(AS_OF - timedelta(days=12)).isoformat(),
                current_price=50.0, why_en="s", why_he="s",
            )
        )
        p.cash = 1_000.0
        p.save(directory=runner.portfolio_dir)

        adapter = self._adapter("stub_agent", [_target("KEEP")], [])
        result = self._run(runner, adapter, {"KEEP": 20.0, "SLIP": 45.0})

        assert [t for t in result.trades if t.side == "SELL"] == []
        assert result.portfolio.has("SLIP")

    def test_a_forced_exit_runs_on_a_no_trade_day(
        self, runner: DailyRunner
    ) -> None:
        """An empty target list is ambiguous; a named exit is not."""
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="BAD", shares=10.0, entry_price=50.0,
                entry_date="2026-01-05", current_price=50.0,
                why_en="s", why_he="s",
            )
        )
        p.cash = 1_000.0
        p.save(directory=runner.portfolio_dir)

        adapter = self._adapter("stub_agent", [], ["BAD"])
        result = self._run(runner, adapter, {"BAD": 45.0})

        assert [t.ticker for t in result.trades if t.side == "SELL"] == ["BAD"]

    def test_an_empty_target_list_still_never_liquidates(
        self, runner: DailyRunner
    ) -> None:
        """With nothing forced, the no-trade signal keeps its meaning."""
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="HOLD", shares=10.0, entry_price=50.0,
                entry_date="2026-01-05", current_price=50.0,
                why_en="s", why_he="s",
            )
        )
        p.cash = 1_000.0
        p.save(directory=runner.portfolio_dir)

        adapter = self._adapter("stub_agent", [], [])
        result = self._run(runner, adapter, {"HOLD": 45.0})

        assert [t for t in result.trades if t.side == "SELL"] == []
        assert result.portfolio.has("HOLD")

    def test_a_forced_exit_outranks_being_a_current_target(
        self, runner: DailyRunner
    ) -> None:
        """Delisting and still-cheap at once exits for the delisting."""
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="BAD", shares=10.0, entry_price=50.0,
                entry_date="2026-01-05", current_price=50.0,
                why_en="s", why_he="s",
            )
        )
        p.cash = 1_000.0
        p.save(directory=runner.portfolio_dir)

        adapter = self._adapter("stub_agent", [_target("BAD")], ["BAD"])
        result = self._run(runner, adapter, {"BAD": 45.0})

        assert [t.ticker for t in result.trades if t.side == "SELL"] == ["BAD"]

    def test_a_forced_exit_still_needs_a_fresh_price(
        self, runner: DailyRunner
    ) -> None:
        """Forcing an exit must not book fabricated proceeds."""
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="DEAD", shares=10.0, entry_price=50.0,
                entry_date="2026-01-05", current_price=50.0,
                why_en="s", why_he="s",
            )
        )
        p.cash = 1_000.0
        p.save(directory=runner.portfolio_dir)

        adapter = self._adapter("stub_agent", [], ["DEAD"])
        result = self._run(runner, adapter, {"DEAD": None})

        assert [t for t in result.trades if t.side == "SELL"] == []
        assert result.portfolio.has("DEAD")

    def test_the_default_is_no_forced_exits(self) -> None:
        assert ScanResult(targets=[], watchlist=[], universe_size=0).forced_exits == []


class TestGoingFlatIsExpressible:
    """A doctrine that wants 100% cash has to be able to say so.

    An empty target list means "no trade" — the universe produced no
    candidates, the data was thin, something upstream gave up — and the
    runner is right to hold everything when it sees one. But under the
    circuit breaker, or a regime row whose entry scale is zero, wanting
    to be entirely in cash is a real and legitimate position, and it
    produces exactly the same empty list. The two were indistinguishable,
    so the safe reading won and going flat could not be expressed at all.

    The flag separates them. Silence still means hold; saying so means
    sell.
    """

    @staticmethod
    def _adapter(name: str, *, flat: bool):
        class _Flat(_StubAdapter):
            def run_scan(self, as_of, universe, prices, fundamentals, *, held=None):
                result = super().run_scan(
                    as_of, universe, prices, fundamentals, held=held
                )
                result.targets = []
                result.flat_is_intentional = flat
                return result

        return _Flat(name, [])

    def _run(self, runner: DailyRunner, adapter, prices: dict[str, float | None]):
        runner.price_loader = _StubPriceLoader(prices)  # type: ignore[assignment]
        return runner._run_one(
            adapter, AS_OF, list(prices), dict(prices), dict.fromkeys(prices)
        )

    @staticmethod
    def _book(*tickers: str, entry_date: str = "2026-01-05") -> LivePortfolio:
        p = LivePortfolio(agent="stub_agent")
        for t in tickers:
            p.positions.append(
                Position(
                    ticker=t, shares=10.0, entry_price=50.0,
                    entry_date=entry_date, current_price=50.0,
                    why_en="s", why_he="s",
                )
            )
        p.cash = 1_000.0
        return p

    def test_an_intentional_flat_sells_everything(self, runner: DailyRunner) -> None:
        self._book("AAA", "BBB").save(directory=runner.portfolio_dir)

        result = self._run(
            runner,
            self._adapter("stub_agent", flat=True),
            {"AAA": 45.0, "BBB": 55.0},
        )

        assert sorted(t.ticker for t in result.trades if t.side == "SELL") == [
            "AAA",
            "BBB",
        ]
        assert result.portfolio.positions == []

    def test_it_beats_the_holding_floor(self, runner: DailyRunner) -> None:
        """For the same reason a forced exit does.

        The floor is aimed at a name that slipped a rank and will slip
        back. "Hold no equities" is not oscillation — it is the most
        specific instruction the doctrine can give, and a floor that
        outranked it would leave the book part-invested against an
        explicit decision to be in cash.
        """
        self._book("NEW", entry_date=AS_OF.isoformat()).save(
            directory=runner.portfolio_dir
        )

        result = self._run(runner, self._adapter("stub_agent", flat=True), {"NEW": 45.0})

        assert [t.ticker for t in result.trades if t.side == "SELL"] == ["NEW"]

    def test_without_the_flag_an_empty_list_still_holds(
        self, runner: DailyRunner
    ) -> None:
        """The no-trade signal keeps the meaning it has always had."""
        self._book("HOLD").save(directory=runner.portfolio_dir)

        result = self._run(
            runner, self._adapter("stub_agent", flat=False), {"HOLD": 45.0}
        )

        assert [t for t in result.trades if t.side == "SELL"] == []
        assert result.portfolio.has("HOLD")

    def test_a_flat_book_still_will_not_sell_at_a_stale_mark(
        self, runner: DailyRunner
    ) -> None:
        """Going flat is a decision about weight, not about price.

        Nothing about wanting to hold cash makes it acceptable to book a
        sale at a price the loader could not produce today.
        """
        self._book("NOPRICE").save(directory=runner.portfolio_dir)

        result = self._run(
            runner, self._adapter("stub_agent", flat=True), {"NOPRICE": None}
        )

        assert [t for t in result.trades if t.side == "SELL"] == []
        assert result.portfolio.has("NOPRICE")

    def test_the_default_is_not_flat(self) -> None:
        assert (
            ScanResult(
                targets=[], watchlist=[], universe_size=0
            ).flat_is_intentional
            is False
        )


class TestRunTypesGateWhatMayOpen:
    """Part 7 reaches the only path that trades.

    The table is the twelfth agent's doctrine, not the eleven's. Graham
    runs his own screen every session and Part 7 has nothing to say
    about it, so the gate is opt-in per adapter: the Council sets
    ``honours_run_types`` and everyone else is untouched.

    What the gate enforces is the single rule the cooling-off depends
    on — a position is opened in a COUNCIL run or not at all. Exits are
    deliberately left alone: the heartbeat exists to catch a delisting
    notice, and a run type that could see one and not act would be worse
    than no run.
    """

    @staticmethod
    def _council(targets: list[LiveTarget]):
        class _Council(_StubAdapter):
            honours_run_types = True

        return _Council("stub_agent", targets)

    def _run(self, runner: DailyRunner, adapter, prices, run_type):
        runner.price_loader = _StubPriceLoader(prices)  # type: ignore[assignment]
        return runner._run_one(
            adapter,
            AS_OF,
            list(prices),
            dict(prices),
            dict.fromkeys(prices),
            run_type=run_type,
        )

    def test_a_council_run_opens(self, runner: DailyRunner) -> None:
        _not_a_rebalance_book().save(directory=runner.portfolio_dir)
        result = self._run(
            runner,
            self._council([_target("NEW")]),
            {"NEW": 50.0},
            RunType.COUNCIL,
        )
        assert [t.ticker for t in result.trades if t.side == "BUY"] == ["NEW"]

    def test_a_heartbeat_does_not_open(self, runner: DailyRunner) -> None:
        """'may not open, add to, or resize anything' — Part 7, verbatim."""
        _not_a_rebalance_book().save(directory=runner.portfolio_dir)
        result = self._run(
            runner,
            self._council([_target("NEW")]),
            {"NEW": 50.0},
            RunType.HEARTBEAT,
        )
        assert [t for t in result.trades if t.side == "BUY"] == []
        assert not result.portfolio.has("NEW")

    def test_a_reading_run_does_not_open(self, runner: DailyRunner) -> None:
        _not_a_rebalance_book().save(directory=runner.portfolio_dir)
        result = self._run(
            runner,
            self._council([_target("NEW")]),
            {"NEW": 50.0},
            RunType.READING,
        )
        assert [t for t in result.trades if t.side == "BUY"] == []

    def test_the_other_eleven_are_not_gated(self, runner: DailyRunner) -> None:
        """Graham's doctrine is chapter 14, not Part 7."""
        _not_a_rebalance_book().save(directory=runner.portfolio_dir)
        plain = _StubAdapter(
            "stub_agent",
            [_target("NEW")],
        )
        result = self._run(runner, plain, {"NEW": 50.0}, RunType.HEARTBEAT)
        assert [t.ticker for t in result.trades if t.side == "BUY"] == ["NEW"]

    def test_a_gated_run_still_takes_a_forced_exit(self, runner: DailyRunner) -> None:
        """The heartbeat's whole purpose is catching what must be sold.

        A kill trigger or a breaking 8-K is exactly what Part 7 puts in
        its "may do" column, so the gate on opening must not reach it.
        """
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="BAD", shares=10.0, entry_price=50.0,
                entry_date="2026-01-05", current_price=50.0,
                why_en="s", why_he="s",
            )
        )
        p.cash = 1_000.0
        p.save(directory=runner.portfolio_dir)

        class _ForcingCouncil(_StubAdapter):
            honours_run_types = True

            def run_scan(self, as_of, universe, prices, fundamentals, *, held=None):
                result = super().run_scan(
                    as_of, universe, prices, fundamentals, held=held
                )
                result.forced_exits = ["BAD"]
                return result

        result = self._run(
            runner,
            _ForcingCouncil("stub_agent", [_target("KEEP", weight=1.0)]),
            {"BAD": 50.0, "KEEP": 25.0},
            RunType.HEARTBEAT,
        )

        assert [t.ticker for t in result.trades if t.side == "SELL"] == ["BAD"]
        assert not result.portfolio.has("KEEP")

    def test_a_gated_run_does_not_sell_on_rank_drift(
        self, runner: DailyRunner
    ) -> None:
        """Only forced exits, though — not an ordinary departure.

        Part 7 gives the heartbeat kill triggers and breaking filings.
        A name that merely fell out of the target list is a rebalance
        decision, and belongs to the run that may rebalance. Selling it
        here would turn every intraday invocation into a trading run,
        which is the exact failure the table exists to prevent.
        """
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="DRIFTED", shares=10.0, entry_price=50.0,
                entry_date="2026-01-05", current_price=50.0,
                why_en="s", why_he="s",
            )
        )
        p.cash = 1_000.0
        p.save(directory=runner.portfolio_dir)

        result = self._run(
            runner,
            self._council([_target("KEEP", weight=1.0)]),
            {"DRIFTED": 50.0, "KEEP": 25.0},
            RunType.HEARTBEAT,
        )

        assert [t for t in result.trades if t.side == "SELL"] == []
        assert result.portfolio.has("DRIFTED")

    def test_the_default_run_type_still_trades(self, runner: DailyRunner) -> None:
        """Omitting it must not silently freeze the live book."""
        _not_a_rebalance_book().save(directory=runner.portfolio_dir)
        runner.price_loader = _StubPriceLoader({"NEW": 50.0})  # type: ignore[assignment]
        result = runner._run_one(
            self._council(
                [_target("NEW")]
            ),
            AS_OF,
            ["NEW"],
            {"NEW": 50.0},
            {"NEW": None},
        )
        assert [t.ticker for t in result.trades if t.side == "BUY"] == ["NEW"]

    def test_adapters_do_not_honour_run_types_by_default(self) -> None:
        assert _StubAdapter("x", []).__class__.__dict__.get("honours_run_types") is None
        assert AgentAdapter.honours_run_types is False


class TestTheRunTypeIsDerivedFromTheCalendar:
    """§7 puts the COUNCIL weekly, and this is where that becomes true.

    The derivation is per agent, from that agent's own ``last_open_date``
    rather than a shared clock, because the runner can be invoked for one
    agent at a time and a book that missed a week must still get its
    council on the next run rather than waiting for the fleet.

    This is the commit that changes live behaviour: before it, the
    twelfth agent could open a position on any day the scan ran. Part 7
    is explicit that this is the failure the table exists to prevent —
    *"an agent initiating many discretionary trades is malfunctioning,
    not working"*.
    """

    @staticmethod
    def _council(targets: list[LiveTarget]):
        class _Council(_StubAdapter):
            honours_run_types = True

        return _Council("stub_agent", targets)

    def _run(self, runner: DailyRunner, adapter, prices, **kw):
        runner.price_loader = _StubPriceLoader(prices)  # type: ignore[assignment]
        return runner._run_one(
            adapter, AS_OF, list(prices), dict(prices), dict.fromkeys(prices), **kw
        )

    @staticmethod
    def _book(last_open: str | None) -> LivePortfolio:
        p = _not_a_rebalance_book()
        if last_open is not None:
            p.last_open_date = last_open
        return p

    def test_the_first_run_ever_is_a_council_run(self, runner: DailyRunner) -> None:
        self._book(None).save(directory=runner.portfolio_dir)
        result = self._run(runner, self._council([_target("NEW")]), {"NEW": 50.0})
        assert [t.ticker for t in result.trades if t.side == "BUY"] == ["NEW"]

    def test_a_second_run_in_the_same_week_is_a_heartbeat(
        self, runner: DailyRunner
    ) -> None:
        """AS_OF is Wednesday 2026-08-05; Monday the 3rd is its week."""
        self._book("2026-08-03").save(directory=runner.portfolio_dir)
        result = self._run(runner, self._council([_target("NEW")]), {"NEW": 50.0})
        assert [t for t in result.trades if t.side == "BUY"] == []

    def test_the_first_run_of_a_new_week_is_a_council_run(
        self, runner: DailyRunner
    ) -> None:
        """Last ran Friday the 31st; AS_OF opens the following week."""
        self._book("2026-07-31").save(directory=runner.portfolio_dir)
        result = self._run(runner, self._council([_target("NEW")]), {"NEW": 50.0})
        assert [t.ticker for t in result.trades if t.side == "BUY"] == ["NEW"]

    def test_a_book_that_missed_a_week_still_gets_its_council(
        self, runner: DailyRunner
    ) -> None:
        self._book("2026-06-01").save(directory=runner.portfolio_dir)
        result = self._run(runner, self._council([_target("NEW")]), {"NEW": 50.0})
        assert [t.ticker for t in result.trades if t.side == "BUY"] == ["NEW"]

    def test_an_explicit_run_type_overrides_the_calendar(
        self, runner: DailyRunner
    ) -> None:
        """A manual dispatch must be able to say what it is."""
        self._book("2026-07-31").save(directory=runner.portfolio_dir)
        result = self._run(
            runner,
            self._council([_target("NEW")]),
            {"NEW": 50.0},
            run_type=RunType.HEARTBEAT,
        )
        assert [t for t in result.trades if t.side == "BUY"] == []

    def test_the_eleven_trade_on_any_day_of_the_week(
        self, runner: DailyRunner
    ) -> None:
        """Mid-week, and Graham buys anyway. Part 7 is not his."""
        self._book("2026-08-03").save(directory=runner.portfolio_dir)
        plain = _StubAdapter("stub_agent", [_target("NEW")])
        result = self._run(runner, plain, {"NEW": 50.0})
        assert [t.ticker for t in result.trades if t.side == "BUY"] == ["NEW"]

    def test_an_unreadable_last_open_date_opens_a_week(
        self, runner: DailyRunner
    ) -> None:
        """Garbage in the field must not freeze the book forever.

        Treating it as "no previous run" makes the next run a COUNCIL,
        which is the direction that keeps the agent working rather than
        silently idle.
        """
        p = self._book(None)
        p.last_open_date = "not-a-date"
        p.save(directory=runner.portfolio_dir)
        result = self._run(runner, self._council([_target("NEW")]), {"NEW": 50.0})
        assert [t.ticker for t in result.trades if t.side == "BUY"] == ["NEW"]


class TestTheQuarterlyRebalanceTradesACooledOffList:
    """§7's cooling-off rule, reaching the path that actually trades.

    Every Council scan writes its would-be list; the quarterly rebalance
    trades from whatever was written *before* today. The rank computed
    a second ago is never the one executed, which is the whole point —
    "the cooling-off rule holds even for the machine".
    """

    @staticmethod
    def _council(targets: list[LiveTarget]):
        class _Council(_StubAdapter):
            honours_run_types = True

        return _Council("stub_agent", targets)

    def _run(self, runner: DailyRunner, adapter, prices):
        runner.price_loader = _StubPriceLoader(prices)  # type: ignore[assignment]
        return runner._run_one(
            adapter, AS_OF, list(prices), dict(prices), dict.fromkeys(prices)
        )

    @staticmethod
    def _fresh_book() -> LivePortfolio:
        """No prior rebalance, so AS_OF in August is the quarterly one."""
        return LivePortfolio(agent="stub_agent", cash=10_000)

    def test_every_council_scan_publishes_its_list(
        self, runner: DailyRunner
    ) -> None:
        _not_a_rebalance_book().save(directory=runner.portfolio_dir)

        self._run(runner, self._council([_target("NEW")]), {"NEW": 50.0})

        published = executable_list(runner.published_dir, as_of=AS_OF + timedelta(days=1))
        assert published is not None
        assert published.tickers == ("NEW",)

    def test_a_rebalance_with_nothing_published_holds(
        self, runner: DailyRunner
    ) -> None:
        """Refusing to trade is the correct answer, not a degraded one.

        The alternative is ranking now and executing now, which is the
        exact breach the rule forbids and would arrive looking like
        resilience.
        """
        self._fresh_book().save(directory=runner.portfolio_dir)

        result = self._run(runner, self._council([_target("NEW")]), {"NEW": 50.0})

        assert [t for t in result.trades if t.side == "BUY"] == []

    def test_a_rebalance_trades_the_list_published_earlier(
        self, runner: DailyRunner
    ) -> None:
        """And not today's scan, even where the two disagree."""
        self._fresh_book().save(directory=runner.portfolio_dir)
        publish(
            PublishedList(
                published_on=AS_OF - timedelta(days=30),
                as_of=AS_OF - timedelta(days=30),
                tickers=("COOLED",),
                weights={"COOLED": 0.5},
                note="last quarter's rank",
            ),
            directory=runner.published_dir,
        )

        result = self._run(
            runner,
            self._council([_target("FRESH")]),
            {"FRESH": 50.0, "COOLED": 25.0},
        )

        bought = [t.ticker for t in result.trades if t.side == "BUY"]
        assert bought == ["COOLED"]
        assert "FRESH" not in bought

    def test_a_name_on_both_lists_is_rebalanced_to_its_published_weight(
        self, runner: DailyRunner
    ) -> None:
        """The common case, and it used to raise.

        Most names survive a quarter, so most of a published list is
        still in today's scan. The code reused today's LiveTarget and
        assigned the published weight onto it — but LiveTarget is
        frozen, so that is a FrozenInstanceError, caught by the
        per-agent guard and reported as a failed run. Every quarterly
        rebalance whose list overlapped the current scan failed this
        way, and the only reason the suite stayed green is that its one
        test published a ticker today's scan did not name.
        """
        self._fresh_book().save(directory=runner.portfolio_dir)
        publish(
            PublishedList(
                published_on=AS_OF - timedelta(days=30),
                as_of=AS_OF - timedelta(days=30),
                tickers=("BOTH",),
                # Deliberately not the weight today's scan carries.
                weights={"BOTH": 0.25},
                note="last quarter's rank",
            ),
            directory=runner.published_dir,
        )

        result = self._run(
            runner,
            self._council([_target("BOTH", weight=0.5)]),
            {"BOTH": 50.0},
        )

        assert result.error is None
        buys = [t for t in result.trades if t.side == "BUY"]
        assert [t.ticker for t in buys] == ["BOTH"]
        # $10,000 at the published 25% is $2,500 — about 50 shares at
        # $50, not the 100 that today's 50% weight would have. Exactly
        # 49: the list leaves 75% in cash, and the 50th share plus the
        # $2.50 commission would take the book past that (audit SF-X2).
        assert buys[0].shares == 49.0

    def test_the_rebalance_is_stamped_so_it_happens_once(
        self, runner: DailyRunner
    ) -> None:
        """Four Mondays in February must not rebalance four times."""
        self._fresh_book().save(directory=runner.portfolio_dir)

        result = self._run(runner, self._council([_target("NEW")]), {"NEW": 50.0})

        assert result.portfolio.last_rebalance_date == AS_OF.isoformat()

    def test_a_book_already_rebalanced_this_month_scans_normally(
        self, runner: DailyRunner
    ) -> None:
        _not_a_rebalance_book().save(directory=runner.portfolio_dir)

        result = self._run(runner, self._council([_target("NEW")]), {"NEW": 50.0})

        assert [t.ticker for t in result.trades if t.side == "BUY"] == ["NEW"]

    def test_the_eleven_are_not_published_or_rebalanced(
        self, runner: DailyRunner
    ) -> None:
        """Part 7 and §7 both belong to the twelfth agent alone."""
        self._fresh_book().save(directory=runner.portfolio_dir)

        result = self._run(
            runner, _StubAdapter("stub_agent", [_target("NEW")]), {"NEW": 50.0}
        )

        assert [t.ticker for t in result.trades if t.side == "BUY"] == ["NEW"]
        assert executable_list(
            runner.published_dir, as_of=AS_OF + timedelta(days=1)
        ) is None


class TestTheLedgerRecordsWhatWasExecuted:
    """The number that used to be computed and thrown away.

    The daily snapshot keeps tickers only, which is how a book could
    report +24.55% with every open position losing money and no file
    able to say which sale paid for it.
    """

    def test_a_sale_is_written_with_its_realized_pnl(
        self, runner: DailyRunner
    ) -> None:
        _seed_holding(
            runner, "stub_agent", "GONE", entry_price=50.0, current_price=50.0
        )
        runner.price_loader = _StubPriceLoader({"GONE": 55.0, "KEEP": 20.0})  # type: ignore[assignment]
        runner._run_one(
            _StubAdapter("stub_agent", [_target("KEEP")]),
            AS_OF,
            ["KEEP", "GONE"],
            {"KEEP": 20.0, "GONE": 55.0},
            {"KEEP": None, "GONE": None},
        )

        entries = read_ledger("stub_agent", directory=runner.ledger_dir)
        sells = [e for e in entries if e.side == "SELL"]
        assert [e.ticker for e in sells] == ["GONE"]
        # 10 shares bought at 50, sold at 55, less the 0.1% cost.
        assert sells[0].realized_pnl_usd == pytest.approx(50.0, abs=1.0)

    def test_a_day_with_no_trades_leaves_no_ledger_file(
        self, runner: DailyRunner
    ) -> None:
        _seed_holding(
            runner, "stub_agent", "HOLD", entry_price=50.0, current_price=50.0
        )
        runner.price_loader = _StubPriceLoader({"HOLD": 50.0})  # type: ignore[assignment]
        runner._run_one(
            _StubAdapter("stub_agent", []),
            AS_OF, ["HOLD"], {"HOLD": 50.0}, {"HOLD": None},
        )

        assert read_ledger("stub_agent", directory=runner.ledger_dir) == []

    def test_a_ledger_failure_never_costs_the_run(
        self, runner: DailyRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The trades already happened and the book is already saved.

        Failing here costs attribution, not money, so it must not take
        down a run that has moved real positions.
        """
        def _boom(*a: object, **k: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("core.live.runner.record_day", _boom)
        _seed_holding(
            runner, "stub_agent", "GONE", entry_price=50.0, current_price=50.0
        )
        runner.price_loader = _StubPriceLoader({"GONE": 55.0, "KEEP": 20.0})  # type: ignore[assignment]

        result = runner._run_one(
            _StubAdapter("stub_agent", [_target("KEEP")]),
            AS_OF, ["KEEP", "GONE"], {"KEEP": 20.0, "GONE": 55.0},
            {"KEEP": None, "GONE": None},
        )

        assert result.error is None
        assert [t.ticker for t in result.trades if t.side == "SELL"] == ["GONE"]


class TestForceFlagParsing:
    """The once-a-day guard has an override, and it defaults to off."""

    def test_absent_means_no_force(self) -> None:
        assert parse_args([]).force is False

    def test_the_flag_sets_it(self) -> None:
        assert parse_args(["--force"]).force is True

    def test_it_composes_with_agent(self) -> None:
        args = parse_args(["--agent", "mohnish_pabrai", "--force"])
        assert args.agents == ["mohnish_pabrai"]
        assert args.force is True


# ---------------------------------------------------------------------------
# The watch list a run replaces
# ---------------------------------------------------------------------------
class _WatchingAdapter(_StubAdapter):
    """A stub whose scan carries a watch list."""

    def __init__(self, name: str, watch: list[LiveWatch]) -> None:
        super().__init__(name, [])
        self._watch = watch

    def run_scan(  # type: ignore[no-untyped-def]
        self, as_of, universe, prices, fundamentals, *, held=None
    ) -> ScanResult:
        self.last_held = held
        return ScanResult(
            targets=[], watchlist=list(self._watch), universe_size=len(universe)
        )


def _a_watch(
    ticker: str, target: float = 12.0, price: float | None = 20.0
) -> LiveWatch:
    return LiveWatch(
        ticker=ticker,
        rank=1,
        entry_trigger="Defensive thresholds met",
        why_en="stub",
        why_he="stub",
        entry_price_target=target,
        last_price=price,
    )


class TestThePreviousWatchlistSurvivesTheRun:
    """A run overwrites the stored list before anything can read it.

    So the result has to carry what was there when the run started, or a
    notifier can only report the list — which means reporting the same
    names every day until its reader stops looking.
    """

    def test_the_result_carries_what_was_there_before(
        self, runner: DailyRunner
    ) -> None:
        portfolio = _seed_holding(
            runner, "stub_agent", "HOLD", entry_price=50.0, current_price=60.0
        )
        portfolio.watchlist = [
            WatchEntry(ticker="OLD", identified_date="2026-10-01"),
            WatchEntry(ticker="KEPT", identified_date="2026-10-01"),
        ]
        portfolio.save(directory=runner.portfolio_dir)
        runner.price_loader = _StubPriceLoader({"HOLD": 60.0})  # type: ignore[assignment]

        result = runner._run_one(
            _WatchingAdapter("stub_agent", [_a_watch("KEPT"), _a_watch("NEW")]),  # type: ignore[arg-type]
            AS_OF,
            ["HOLD"],
            {"HOLD": 60.0},
            {"HOLD": None},
        )

        assert set(result.previous_watchlist) == {"OLD", "KEPT"}
        assert [w.ticker for w in result.watchlist] == ["KEPT", "NEW"]

    def test_the_stored_list_is_the_new_one(self, runner: DailyRunner) -> None:
        """The capture must read before the overwrite, not instead of it."""
        portfolio = _seed_holding(
            runner, "stub_agent", "HOLD", entry_price=50.0, current_price=60.0
        )
        portfolio.watchlist = [WatchEntry(ticker="OLD", identified_date="2026-10-01")]
        portfolio.save(directory=runner.portfolio_dir)
        runner.price_loader = _StubPriceLoader({"HOLD": 60.0})  # type: ignore[assignment]

        result = runner._run_one(
            _WatchingAdapter("stub_agent", [_a_watch("NEW")]),  # type: ignore[arg-type]
            AS_OF,
            ["HOLD"],
            {"HOLD": 60.0},
            {"HOLD": None},
        )

        assert result.previous_watchlist == ("OLD",)
        assert [w.ticker for w in result.portfolio.watchlist] == ["NEW"]

    def test_the_stored_entry_keeps_both_prices(self, runner: DailyRunner) -> None:
        """The hand-off from LiveWatch to the stored entry, field by field.

        The dashboard subtracts one from the other to say how far the
        price must fall. The adapter records both and the file holds
        both, and in between is this copy — which dropped the quotation
        silently for as long as it was not listed here.
        """
        _seed_holding(
            runner, "stub_agent", "HOLD", entry_price=50.0, current_price=60.0
        )
        runner.price_loader = _StubPriceLoader({"HOLD": 60.0})  # type: ignore[assignment]

        result = runner._run_one(
            _WatchingAdapter(  # type: ignore[arg-type]
                "stub_agent", [_a_watch("NEW", target=74.2, price=91.0)]
            ),
            AS_OF,
            ["HOLD"],
            {"HOLD": 60.0},
            {"HOLD": None},
        )

        stored = result.portfolio.watchlist[0]
        assert stored.entry_price_target == pytest.approx(74.2)
        assert stored.last_price == pytest.approx(91.0)

    def test_an_adapter_that_records_no_price_stores_none(
        self, runner: DailyRunner
    ) -> None:
        _seed_holding(
            runner, "stub_agent", "HOLD", entry_price=50.0, current_price=60.0
        )
        runner.price_loader = _StubPriceLoader({"HOLD": 60.0})  # type: ignore[assignment]

        result = runner._run_one(
            _WatchingAdapter(  # type: ignore[arg-type]
                "stub_agent", [_a_watch("NEW", target=74.2, price=None)]
            ),
            AS_OF,
            ["HOLD"],
            {"HOLD": 60.0},
            {"HOLD": None},
        )

        assert result.portfolio.watchlist[0].last_price is None

    def test_a_first_run_has_no_previous_list(self, runner: DailyRunner) -> None:
        _seed_holding(
            runner, "stub_agent", "HOLD", entry_price=50.0, current_price=60.0
        )
        runner.price_loader = _StubPriceLoader({"HOLD": 60.0})  # type: ignore[assignment]

        result = runner._run_one(
            _WatchingAdapter("stub_agent", [_a_watch("NEW")]),  # type: ignore[arg-type]
            AS_OF,
            ["HOLD"],
            {"HOLD": 60.0},
            {"HOLD": None},
        )

        assert result.previous_watchlist == ()


class TestARebalanceIsRecorded:
    """A trade the journal does not hold is a trade nobody can audit.

    The rotation path logs FILL and EXIT; the rebalance path logged
    nothing at all, while executing real buys and sells through the
    same portfolio. Both halves of a run reached data/trades and the
    Telegram message, so the notifier reported a purchase the journal
    had no row for — the dashboard, which reads the journal, could not
    show it and the count it produced was wrong.

    ADD and TRIM are not new vocabulary: both are already in
    VALID_DECISION_TYPES, already buy-kinds and sell-kinds to the
    dashboard, and already entry and exit kinds to the notifier.
    """

    def _drifted(
        self, runner: DailyRunner, *, shares: float, now: float, cash: float
    ) -> None:
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="DRIFT",
                shares=shares,
                entry_price=100.0,
                entry_date="2026-07-01",
                current_price=now,
                why_en="",
                why_he="",
            )
        )
        p.cash = cash
        p.save(directory=runner.portfolio_dir)

    def _run(self, runner: DailyRunner, price: float):
        adapter = _StubAdapter("stub_agent", [_target("DRIFT", weight=0.5)])
        runner.price_loader = _StubPriceLoader({"DRIFT": price})  # type: ignore[assignment]
        return runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["DRIFT"],
            {"DRIFT": price},
            {"DRIFT": None},
        )

    def _journal(self, runner: DailyRunner) -> list:
        return runner.decision_logger.read_day("stub_agent", AS_OF)

    @staticmethod
    def _stated(rationale: str | None) -> tuple[float, float]:
        """The shares and price the row states, read as a reader reads them.

        ``Decision`` has no field for a share count, so the number lives
        in the rationale — the string the dashboard renders. Asserting
        on the parse rather than on a field means these tests check what
        a human is actually shown.
        """
        m = re.search(r"([\d.]+) shares at \$([\d.]+)", rationale or "")
        assert m is not None, f"no shares/price in rationale: {rationale!r}"
        return float(m.group(1)), float(m.group(2))

    def test_a_trim_is_written_to_the_journal(self, runner: DailyRunner) -> None:
        # 50 × $300 = $15,000 against $5,000 cash: drift +50%.
        self._drifted(runner, shares=50.0, now=300.0, cash=5_000.0)

        result = self._run(runner, 300.0)

        assert [t.side for t in result.trades] == ["SELL"]
        rows = [r for r in self._journal(runner) if r.decision == "TRIM"]
        assert len(rows) == 1, [r.decision for r in self._journal(runner)]
        assert rows[0].ticker == "DRIFT"

    def test_an_add_is_written_to_the_journal(self, runner: DailyRunner) -> None:
        # 10 × $100 = $1,000 against $9,000 cash: NAV 10,000, target
        # 5,000, drift -80%.
        self._drifted(runner, shares=10.0, now=100.0, cash=9_000.0)

        result = self._run(runner, 100.0)

        assert [t.side for t in result.trades] == ["BUY"]
        rows = [r for r in self._journal(runner) if r.decision == "ADD"]
        assert len(rows) == 1, [r.decision for r in self._journal(runner)]
        assert rows[0].ticker == "DRIFT"

    def test_the_row_carries_the_shares_and_the_price(
        self, runner: DailyRunner
    ) -> None:
        # Without these the row says a trade happened and nothing more,
        # which is the complaint that started this.
        self._drifted(runner, shares=10.0, now=100.0, cash=9_000.0)

        self._run(runner, 100.0)

        row = next(r for r in self._journal(runner) if r.decision == "ADD")
        assert row.entry_price == pytest.approx(100.0)
        assert "rebalance" in (row.rationale or "").lower()

    def test_the_add_row_states_the_shares_that_were_traded(
        self, runner: DailyRunner
    ) -> None:
        """A whole-share book must not be journalled in fractions.

        This test is named after the share count and so was the one
        before it — which never asserted the count, and so passed
        against a row that overstated it. ``buy`` floors what is left
        of the spend after the commission, so the fill can be fewer
        shares than the request — under the old 0.1% cost a $5,000 add
        at $100 moved 49 shares and not 50. The row said 50.0000 until the
        number came from the fill rather than from the request, and the
        journal is the only store the dashboard reads.
        """
        self._drifted(runner, shares=10.0, now=100.0, cash=9_000.0)

        result = self._run(runner, 100.0)

        fill = result.trades[0]
        row = next(r for r in self._journal(runner) if r.decision == "ADD")
        shares, price = self._stated(row.rationale)
        assert shares == pytest.approx(fill.shares)
        assert price == pytest.approx(fill.price)
        assert row.entry_price == pytest.approx(fill.price)
        # Whole shares, because whole shares are what the book trades.
        assert fill.shares == float(int(fill.shares))

    def test_the_trim_row_states_the_shares_that_were_traded(
        self, runner: DailyRunner
    ) -> None:
        """The same, on the sell side, where ``sell`` floors outright.

        A 50% drift on a $15,000 line at $300 is $5,000 of trim, which
        is 16.6667 shares requested and 16 shares sold.
        """
        self._drifted(runner, shares=50.0, now=300.0, cash=5_000.0)

        result = self._run(runner, 300.0)

        fill = result.trades[0]
        row = next(r for r in self._journal(runner) if r.decision == "TRIM")
        shares, price = self._stated(row.rationale)
        assert shares == pytest.approx(fill.shares)
        assert price == pytest.approx(fill.price)
        assert fill.shares == float(int(fill.shares))

    def test_a_book_inside_the_band_writes_no_rebalance_row(
        self, runner: DailyRunner
    ) -> None:
        self._drifted(runner, shares=50.0, now=110.0, cash=5_000.0)

        result = self._run(runner, 110.0)

        assert result.trades == []
        kinds = {r.decision for r in self._journal(runner)}
        assert "TRIM" not in kinds and "ADD" not in kinds

    def test_the_journal_and_the_ledger_agree(self, runner: DailyRunner) -> None:
        # The two stores are written by different calls; a trade in one
        # and not the other is how this started.
        self._drifted(runner, shares=50.0, now=300.0, cash=5_000.0)

        result = self._run(runner, 300.0)

        ledger = read_ledger("stub_agent", directory=runner.ledger_dir)
        rebalance_rows = [
            r for r in self._journal(runner) if r.decision in {"ADD", "TRIM"}
        ]
        assert len(ledger) == len(result.trades) == len(rebalance_rows) == 1
        assert ledger[0].ticker == rebalance_rows[0].ticker
        # Agreeing on the ticker is not agreeing. This test passed while
        # the journal claimed 16.6667 shares for the 16 the ledger
        # recorded, because it compared names and counted rows and
        # stopped there. Two stores agree when the numbers match.
        stated_shares, stated_price = self._stated(rebalance_rows[0].rationale)
        assert stated_shares == pytest.approx(ledger[0].shares)
        assert stated_price == pytest.approx(ledger[0].price)
        assert ledger[0].shares == pytest.approx(result.trades[0].shares)


# ---------------------------------------------------------------------------
# Unpriced holdings (audit CE-1a / PD-09 / SF-X4)
# ---------------------------------------------------------------------------
@pytest.fixture
def errors() -> Iterator[list[str]]:
    """Every ERROR-level message the runner logs while the test runs.

    The project logs through loguru, which pytest's caplog never sees.
    """
    from loguru import logger as loguru_logger

    seen: list[str] = []
    sink_id = loguru_logger.add(
        lambda m: seen.append(m.record["message"]), level="ERROR"
    )
    try:
        yield seen
    finally:
        loguru_logger.remove(sink_id)


class TestUnpricedHoldingsAreFlagged:
    """A held name with no bar for more than five sessions is flagged.

    LEG was acquired on 26 August 2026 and stopped printing. The runner
    refused, correctly, to sell it at a dead quote, and mark_to_market
    kept its last close — so for 78 days the book carried it at $9.20,
    priced every run, with nothing on the position, in the log or on the
    dashboard saying the number referred to nothing.
    """

    def test_held_name_without_bar_for_6_sessions_is_flagged_not_marked(
        self, runner: DailyRunner, errors: list[str]
    ) -> None:
        _seed_holding(
            runner, "stub_agent", "LEG", entry_price=9.66, current_price=9.20
        )
        # Last bar Tue 28 July; AS_OF Wed 5 August is the sixth session
        # without one.
        runner.price_loader = _StubPriceLoader(  # type: ignore[assignment]
            {"LEG": None}, last_bars={"LEG": date(2026, 7, 28)}
        )
        adapter = _StubAdapter("stub_agent", [])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["LEG"],
            {"LEG": None},
            {"LEG": None},
        )

        pos = result.portfolio.positions[0]
        assert pos.unpriced_since == "2026-07-29"
        # Not re-marked: the last close stays, and is now labelled.
        assert pos.current_price == pytest.approx(9.20)
        assert any("LEG" in m for m in errors)
        # And the flag is what reaches disk, where the dashboard reads it.
        saved = LivePortfolio.load_or_seed(
            "stub_agent", directory=runner.portfolio_dir
        )
        assert saved.positions[0].unpriced_since == "2026-07-29"

    def test_five_sessions_without_a_bar_is_not_yet_flagged(
        self, runner: DailyRunner, errors: list[str]
    ) -> None:
        _seed_holding(
            runner, "stub_agent", "LATE", entry_price=50.0, current_price=50.0
        )
        runner.price_loader = _StubPriceLoader(  # type: ignore[assignment]
            {"LATE": None}, last_bars={"LATE": date(2026, 7, 29)}
        )

        result = runner._run_one(
            _StubAdapter("stub_agent", []),  # type: ignore[arg-type]
            AS_OF,
            ["LATE"],
            {"LATE": None},
            {"LATE": None},
        )

        assert result.portfolio.positions[0].unpriced_since == ""
        assert errors == []

    def test_a_fresh_price_clears_the_flag(self, runner: DailyRunner) -> None:
        p = _seed_holding(
            runner, "stub_agent", "BACK", entry_price=50.0, current_price=50.0
        )
        p.positions[0].unpriced_since = "2026-07-20"
        p.save(directory=runner.portfolio_dir)
        runner.price_loader = _StubPriceLoader({"BACK": 52.0})  # type: ignore[assignment]

        result = runner._run_one(
            _StubAdapter("stub_agent", []),  # type: ignore[arg-type]
            AS_OF,
            ["BACK"],
            {"BACK": 52.0},
            {"BACK": None},
        )

        pos = result.portfolio.positions[0]
        assert pos.unpriced_since == ""
        assert pos.current_price == pytest.approx(52.0)

    def test_a_name_with_no_history_at_all_is_flagged_from_entry(
        self, runner: DailyRunner
    ) -> None:
        _seed_holding(
            runner, "stub_agent", "GHOST", entry_price=5.0, current_price=5.0
        )
        runner.price_loader = _StubPriceLoader({"GHOST": None})  # type: ignore[assignment]

        result = runner._run_one(
            _StubAdapter("stub_agent", []),  # type: ignore[arg-type]
            AS_OF,
            ["GHOST"],
            {"GHOST": None},
            {"GHOST": None},
        )

        assert result.portfolio.positions[0].unpriced_since == "2026-07-01"

    def test_the_close_mark_flags_too(
        self, runner: DailyRunner, errors: list[str]
    ) -> None:
        _seed_holding(
            runner, "stub_agent", "LEG", entry_price=9.66, current_price=9.20
        )
        runner.price_loader = _StubPriceLoader(  # type: ignore[assignment]
            {"LEG": None}, last_bars={"LEG": date(2026, 7, 28)}
        )
        runner.adapters = [_StubAdapter("stub_agent", [])]  # type: ignore[list-item]

        results = runner.run_mark_to_market(as_of=AS_OF)

        pos = results[0].portfolio.positions[0]
        assert pos.unpriced_since == "2026-07-29"
        assert pos.current_price == pytest.approx(9.20)
        assert any("LEG" in m for m in errors)


# ---------------------------------------------------------------------------
# Fundamentals freshness (audit FUND-2)
# ---------------------------------------------------------------------------
class _StubUniverse:
    def __init__(self, members: list[str]) -> None:
        self._members = members

    def _ensure_loaded(self) -> None:
        return None

    def constituents_at(self, as_of: date) -> list[str]:
        return list(self._members)


class _StubPitLoader:
    """Hands back one filing per ticker, filed on the date the test names."""

    def __init__(self, filed: dict[str, date]) -> None:
        self._filed = filed

    def get_financials(self, ticker: str, as_of: date):  # type: ignore[no-untyped-def]
        from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials

        filed = self._filed.get(ticker)
        if filed is None:
            return None
        return PointInTimeFinancials(
            ticker=ticker,
            as_of=as_of,
            source_filing=FilingMetadata(
                ticker=ticker,
                cik=None,
                form_type="10-Q",
                filing_date=filed,
                period_of_report=None,
                accession_number=f"ACC-{ticker}",
            ),
            net_income=1.0,
        )


class TestFundamentalsFreshnessGate:
    """The scan says loudly when the filings it reads are old — and trades.

    Every live decision since about May 2026 read filings frozen at
    2026-04-28 with nothing in the log, the books or the dashboard
    saying so. Halting on it would freeze every book the day the check
    shipped, because the cache is stale today; whether a stale corpus
    should ever halt trading is the owner's call, so the default warns.
    """

    @pytest.fixture
    def snaps(
        self, runner: DailyRunner, monkeypatch: pytest.MonkeyPatch
    ) -> list[object]:
        saved: list[object] = []
        monkeypatch.setattr("core.live.runner.save_snapshot", saved.append)
        monkeypatch.setattr("core.live.runner.export_sectors", lambda *a, **k: None)
        monkeypatch.setattr("core.live.runner.export_prices", lambda *a, **k: None)
        monkeypatch.setattr("core.live.runner.notify_run", lambda *a, **k: None)
        runner.universe = _StubUniverse(["KO"])  # type: ignore[assignment]
        runner.price_loader = _StubPriceLoader({"KO": 60.0})  # type: ignore[assignment]
        # The stub duck-types AgentAdapter; mypy reports the item's line.
        runner.adapters = [
            _StubAdapter("stub_agent", [_target("KO", weight=0.5)])  # type: ignore[list-item]
        ]
        _not_a_rebalance_book().save(directory=runner.portfolio_dir)
        return saved

    def test_stale_fundamentals_log_error_flag_snapshot_and_still_trade(
        self, runner: DailyRunner, snaps: list[object], errors: list[str]
    ) -> None:
        # 126 days before AS_OF (2026-08-05).
        runner.pit_loader = _StubPitLoader({"KO": date(2026, 4, 1)})  # type: ignore[assignment]

        results = runner.run(as_of=AS_OF)

        # Not halted: the book still trades.
        assert [t.ticker for t in results[0].trades] == ["KO"]
        assert any("fundamentals are stale" in m and "2026-04-01" in m for m in errors)
        assert len(snaps) == 1
        snap = snaps[0]
        assert snap.fundamentals_stale is True  # type: ignore[attr-defined]
        assert snap.fundamentals_newest_filed == "2026-04-01"  # type: ignore[attr-defined]

    def test_fresh_fundamentals_flag_false_and_log_no_error(
        self, runner: DailyRunner, snaps: list[object], errors: list[str]
    ) -> None:
        runner.pit_loader = _StubPitLoader({"KO": date(2026, 7, 28)})  # type: ignore[assignment]

        runner.run(as_of=AS_OF)

        assert not any("fundamentals" in m for m in errors)
        snap = snaps[0]
        assert snap.fundamentals_stale is False  # type: ignore[attr-defined]
        assert snap.fundamentals_newest_filed == "2026-07-28"  # type: ignore[attr-defined]

    def test_no_fundamentals_at_all_is_flagged(
        self, runner: DailyRunner, snaps: list[object], errors: list[str]
    ) -> None:
        runner.pit_loader = _StubPitLoader({})  # type: ignore[assignment]

        runner.run(as_of=AS_OF)

        assert any("fundamentals are stale" in m for m in errors)
        snap = snaps[0]
        assert snap.fundamentals_stale is True  # type: ignore[attr-defined]
        assert snap.fundamentals_newest_filed is None  # type: ignore[attr-defined]

    def test_the_close_mark_does_not_measure(
        self, runner: DailyRunner, snaps: list[object]
    ) -> None:
        """It reads no filings, so it leaves the morning's flag alone."""
        runner.run_mark_to_market(as_of=AS_OF)

        assert snaps[0].fundamentals_stale is None  # type: ignore[attr-defined]

    def test_both_snapshots_say_the_session_was_open(
        self, runner: DailyRunner, snaps: list[object]
    ) -> None:
        """The runner asked the calendar before writing, so it says so.

        An explicit False is what clears a closed-session tag put on an
        open day by mistake; None would leave it stuck.
        """
        runner.pit_loader = _StubPitLoader({"KO": date(2026, 7, 28)})  # type: ignore[assignment]

        runner.run(as_of=AS_OF)
        runner.run_mark_to_market(as_of=AS_OF)

        assert [s.closed_session for s in snaps] == [False, False]  # type: ignore[attr-defined]

    def test_the_close_mark_clears_a_stored_tag_on_an_open_day(
        self,
        runner: DailyRunner,
        snaps: list[object],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End to end: a wrong tag in the day's file does not survive.

        The mark books no trades, so only the runner's explicit False can
        clear the tag; a save that passed None would inherit it.
        """
        from core.live.snapshots import save_snapshot

        root = tmp_path / "snapshots"
        day = root / "stub_agent" / f"{AS_OF.isoformat()}.json"
        day.parent.mkdir(parents=True)
        day.write_text(json.dumps({"closed_session": True}))
        monkeypatch.setattr(
            "core.live.runner.save_snapshot", lambda s: save_snapshot(s, root=root)
        )

        runner.run_mark_to_market(as_of=AS_OF)

        assert "closed_session" not in json.loads(day.read_text())


class _DoctrineAdapter(_StubAdapter):
    """A stub that declares the doctrine's band and per-name cap, the
    way the real adapters do (audit SF-X3 / SF-X5)."""

    def __init__(
        self,
        name: str,
        targets: list[LiveTarget],
        *,
        rebalance_band: float | str | None = "runner",
        max_position_pct: float | None = None,
    ) -> None:
        super().__init__(name, targets)
        self.rebalance_band = rebalance_band
        self.max_position_pct = max_position_pct


class TestTheDoctrineOwnsTheBandAndTheCap:
    """Audit SF-X3: the 25% band trimmed Lynch's EXEL winner while the
    book held fifteen losers, against playbook §6.4. Audit SF-X5:
    ``reachable_targets`` handed unreachable weight to the survivors
    until Graham held five names at 15.8-20.9% (cap 10%) and Schloss
    five at 11.7-25.9% (cap 5%)."""

    def _winner_book(self, runner: DailyRunner, *, price: float) -> None:
        # 50 shares bought at $100, $5,000 cash beside them.
        p = LivePortfolio(agent="stub_agent", cash=5_000.0)
        p.last_rebalance_date = "2026-08-03"
        p.positions.append(
            Position(
                ticker="EXEL",
                shares=50.0,
                entry_price=100.0,
                entry_date="2026-07-01",
                current_price=price,
            )
        )
        p.save(directory=runner.portfolio_dir)

    def _run_lynch(self, runner: DailyRunner, price: float) -> AgentRunResult:
        from core.live.agent_adapter import LynchLive

        adapter = _DoctrineAdapter(
            "stub_agent",
            [_target("EXEL", weight=0.5)],
            rebalance_band=LynchLive.rebalance_band,
        )
        runner.price_loader = _StubPriceLoader({"EXEL": price})  # type: ignore[assignment]
        return runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["EXEL"],
            {"EXEL": price},
            {"EXEL": None},
        )

    def test_lynch_winner_is_not_trimmed_by_band(
        self, runner: DailyRunner
    ) -> None:
        # 50 x $300 = $15,000 of a $20,000 book against a 50% target:
        # +50% drift, which the runner-wide band trims by 16 shares.
        self._winner_book(runner, price=300.0)

        result = self._run_lynch(runner, 300.0)

        assert [t for t in result.trades if t.side == "SELL"] == []
        assert result.portfolio.positions[0].shares == 50.0

    def test_a_band_of_none_still_tops_up_an_underweight_name(
        self, runner: DailyRunner
    ) -> None:
        """``None`` forbids the trim and nothing else: an underweight
        position is still added to at the runner's band."""
        # 50 x $20 = $1,000 against $5,000 cash: -67% drift.
        self._winner_book(runner, price=20.0)

        result = self._run_lynch(runner, 20.0)

        buys = [t for t in result.trades if t.side == "BUY"]
        assert len(buys) == 1 and buys[0].ticker == "EXEL"

    def test_graham_with_5_reachable_names_holds_50_percent_cash_not_20_percent_each(
        self, runner: DailyRunner
    ) -> None:
        # Graham's equal weights over five names are 20% each; his
        # playbook allows 10%. The other half stays cash.
        tickers = ["G1", "G2", "G3", "G4", "G5"]
        prices: dict[str, float | None] = dict.fromkeys(tickers, 45.0)
        adapter = _DoctrineAdapter(
            "stub_agent",
            [_target(t, weight=0.2, rank=i + 1) for i, t in enumerate(tickers)],
            max_position_pct=10.0,
        )
        runner.price_loader = _StubPriceLoader(dict(prices))  # type: ignore[assignment]
        runner_book = _not_a_rebalance_book()
        runner_book.save(directory=runner.portfolio_dir)

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            tickers,
            prices,
            dict.fromkeys(tickers),
        )

        book = result.portfolio
        assert {p.ticker for p in book.positions} == set(tickers)
        for pos in book.positions:
            assert pos.weight_pct <= 10.0, f"{pos.ticker} at {pos.weight_pct:.2f}%"
        assert book.cash / book.total_nav * 100.0 >= 50.0

    def test_a_held_name_above_the_cap_is_trimmed_toward_it(
        self, runner: DailyRunner
    ) -> None:
        """The live consequence: Graham's existing 20% positions are
        trimmed to the cap, and the proceeds stay in cash."""
        p = LivePortfolio(agent="stub_agent", cash=6_000.0)
        p.last_rebalance_date = "2026-08-03"
        for ticker in ("G1", "G2"):
            p.positions.append(
                Position(
                    ticker=ticker,
                    shares=40.0,
                    entry_price=50.0,
                    entry_date="2026-07-01",
                    current_price=50.0,
                )
            )
        p.save(directory=runner.portfolio_dir)
        prices: dict[str, float | None] = {"G1": 50.0, "G2": 50.0}
        runner.price_loader = _StubPriceLoader(dict(prices))  # type: ignore[assignment]
        adapter = _DoctrineAdapter(
            "stub_agent",
            [_target("G1", weight=0.5), _target("G2", weight=0.5, rank=2)],
            max_position_pct=10.0,
        )

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["G1", "G2"],
            prices,
            dict.fromkeys(prices),
        )

        sells = sorted(t.ticker for t in result.trades if t.side == "SELL")
        assert sells == ["G1", "G2"]
        assert [t for t in result.trades if t.side == "BUY"] == []
        book = result.portfolio
        one_share_pct = 50.0 / book.total_nav * 100.0
        for pos in book.positions:
            assert pos.weight_pct == pytest.approx(10.0, abs=one_share_pct)
        assert book.cash / book.total_nav * 100.0 >= 79.0

    def test_a_cap_lowers_a_weight_the_drop_would_lift(self) -> None:
        """The excess of a redistributed weight stays cash."""
        targets = [_target("A", weight=0.08), _target("DEAR", weight=0.08, rank=2)]
        kept = reachable_targets(
            targets, {"A": 10.0, "DEAR": 5_000.0}, nav=10_000.0, max_weight=0.10
        )
        assert [t.ticker for t in kept] == ["A"]
        assert kept[0].weight == pytest.approx(0.10)

    def test_a_cap_lowers_the_strategys_own_weight(self) -> None:
        targets = [_target("A", weight=0.2), _target("B", weight=0.2, rank=2)]
        kept = reachable_targets(
            targets, {"A": 10.0, "B": 10.0}, nav=10_000.0, max_weight=0.05
        )
        assert [t.weight for t in kept] == pytest.approx([0.05, 0.05])

    def test_a_name_the_capped_slot_cannot_hold_is_dropped(self) -> None:
        """Reach is judged against the capped slot. Uncapped, a 50% slot
        of $10,000 holds a $1,500 share; at Graham's 10% it cannot, and
        keeping the name would reserve posture for a buy that never
        happens."""
        targets = [_target("A", weight=0.5), _target("DEAR", weight=0.5, rank=2)]
        kept = reachable_targets(
            targets, {"A": 10.0, "DEAR": 1_500.0}, nav=10_000.0, max_weight=0.10
        )
        assert [t.ticker for t in kept] == ["A"]
        assert kept[0].weight == pytest.approx(0.10)

    def test_a_cap_outside_zero_to_one_is_refused(self) -> None:
        targets = [_target("A", weight=0.5)]
        for bad in (0.0, -0.1, 1.5):
            with pytest.raises(ValueError, match="max_weight"):
                reachable_targets(
                    targets, {"A": 10.0}, nav=10_000.0, max_weight=bad
                )

    def test_a_top_up_never_carries_a_position_past_the_cap(self) -> None:
        """The residue pass may top a slot up by the band, but not past
        the doctrine's cap: 12 x $40 fills a $500 slot, and a 13th share
        ($520) would be 5.2% of a 5% name."""
        from core.live.runner import plan_whole_share_buys

        kwargs = {
            "nav": 10_000.0,
            "cash": 10_000.0,
            "cost_bps": 0.0,
            "commission": 2.50,
            "band": 0.25,
        }
        targets = [_target("X", weight=0.05)]
        uncapped = plan_whole_share_buys(targets, {"X": 40.0}, **kwargs)
        capped = plan_whole_share_buys(
            targets, {"X": 40.0}, max_weight=0.05, **kwargs
        )
        assert uncapped == {"X": 13}
        assert capped == {"X": 12}

    def test_an_adapter_band_or_cap_out_of_range_is_refused(self) -> None:
        from core.live.runner import band_for, max_weight_for

        assert band_for(_DoctrineAdapter("a", []), default=0.25) == 0.25
        assert band_for(_StubAdapter("a", []), default=0.25) == 0.25
        assert band_for(_DoctrineAdapter("a", [], rebalance_band=None), default=0.25) is None
        assert band_for(_DoctrineAdapter("a", [], rebalance_band=0.1), default=0.25) == 0.1
        assert max_weight_for(_StubAdapter("a", [])) is None
        assert max_weight_for(_DoctrineAdapter("a", [], max_position_pct=5.0)) == 0.05
        with pytest.raises(ValueError, match="rebalance_band"):
            band_for(_DoctrineAdapter("a", [], rebalance_band=-0.1), default=0.25)
        with pytest.raises(ValueError, match="rebalance_band"):
            band_for(_DoctrineAdapter("a", [], rebalance_band="wide"), default=0.25)
        for bad in (0.0, 150.0):
            with pytest.raises(ValueError, match="max_position_pct"):
                max_weight_for(_DoctrineAdapter("a", [], max_position_pct=bad))


# ---------------------------------------------------------------------------
# Journal write failures (audit RK-04)
# ---------------------------------------------------------------------------
class _BrokenJournal(DecisionLogger):
    """A decision log whose every write fails, as a full disk would."""

    def log(self, decision):  # type: ignore[no-untyped-def]
        raise OSError("No space left on device")


@pytest.fixture
def warnings_seen() -> Iterator[list[str]]:
    """Every WARNING-or-worse message logged while the test runs.

    The project logs through loguru, which pytest's caplog never sees.
    """
    from loguru import logger as loguru_logger

    seen: list[str] = []
    sink_id = loguru_logger.add(
        lambda m: seen.append(m.record["message"]), level="WARNING"
    )
    try:
        yield seen
    finally:
        loguru_logger.remove(sink_id)


class TestJournalFailuresAreCounted:
    """A trade whose journal row could not be written is said and counted.

    The FILL, EXIT and rebalance rows were written under
    ``except Exception: logger.debug`` — below every level the daily job
    prints — so a journal that lost rows looked exactly like one that
    had none to write. 103 book changes reached the repo with no journal
    row (audit RK-04). The trade itself must still stand: the book is
    the record of money, the journal the record of reasons.
    """

    def test_journal_write_failure_is_counted_not_swallowed(
        self, runner: DailyRunner, tmp_path: Path, warnings_seen: list[str]
    ) -> None:
        runner.decision_logger = _BrokenJournal(root=tmp_path / "broken")
        runner.price_loader = _StubPriceLoader({"NEW": 20.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("NEW", weight=0.5)])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["NEW"],
            {"NEW": 20.0},
            {"NEW": None},
        )

        assert result.portfolio.has("NEW")  # the trade stands
        assert result.journal_failures == 1
        assert any(
            "NEW" in m and "journal" in m and "No space left" in m
            for m in warnings_seen
        ), warnings_seen

    def test_a_lost_exit_row_is_counted(
        self, runner: DailyRunner, tmp_path: Path, warnings_seen: list[str]
    ) -> None:
        _seed_holding(
            runner, "stub_agent", "GONE", entry_price=50.0, current_price=55.0
        )
        runner.decision_logger = _BrokenJournal(root=tmp_path / "broken")
        runner.price_loader = _StubPriceLoader({"GONE": 55.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("KEEP")])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["KEEP", "GONE"],
            {"KEEP": None, "GONE": 55.0},
            {"KEEP": None, "GONE": None},
        )

        assert not result.portfolio.has("GONE")
        assert result.journal_failures == 1
        assert any("EXIT GONE" in m for m in warnings_seen), warnings_seen

    def test_a_lost_rebalance_row_is_counted(
        self, runner: DailyRunner, tmp_path: Path, warnings_seen: list[str]
    ) -> None:
        # The TestRebalanceBand trim: 50 x $300 against $5,000 cash.
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="DRIFT", shares=50.0, entry_price=100.0,
                entry_date="2026-07-01", current_price=300.0,
                why_en="", why_he="",
            )
        )
        p.cash = 5_000.0
        p.save(directory=runner.portfolio_dir)
        runner.decision_logger = _BrokenJournal(root=tmp_path / "broken")
        runner.price_loader = _StubPriceLoader({"DRIFT": 300.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("DRIFT", weight=0.5)])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["DRIFT"],
            {"DRIFT": 300.0},
            {"DRIFT": None},
        )

        assert [t.side for t in result.trades] == ["SELL"]
        assert result.journal_failures == 1
        assert any("TRIM DRIFT" in m for m in warnings_seen), warnings_seen

    def test_a_healthy_journal_counts_nothing(self, runner: DailyRunner) -> None:
        runner.price_loader = _StubPriceLoader({"NEW": 20.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("NEW", weight=0.5)])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["NEW"],
            {"NEW": 20.0},
            {"NEW": None},
        )

        assert result.trades
        assert result.journal_failures == 0

    def test_the_count_is_per_agent_not_per_runner(
        self, runner: DailyRunner, tmp_path: Path
    ) -> None:
        # One runner serves every agent in turn; the second agent must
        # not report the first one's lost rows as its own.
        runner.decision_logger = _BrokenJournal(root=tmp_path / "broken")
        runner.price_loader = _StubPriceLoader({"NEW": 20.0})  # type: ignore[assignment]
        first = runner._run_one(
            _StubAdapter("agent_a", [_target("NEW", weight=0.5)]),  # type: ignore[arg-type]
            AS_OF, ["NEW"], {"NEW": 20.0}, {"NEW": None},
        )
        runner.decision_logger = DecisionLogger(root=tmp_path / "ok")
        second = runner._run_one(
            _StubAdapter("agent_b", [_target("NEW", weight=0.5)]),  # type: ignore[arg-type]
            AS_OF, ["NEW"], {"NEW": 20.0}, {"NEW": None},
        )

        assert first.journal_failures == 1
        assert second.journal_failures == 0

    def test_the_daily_report_prints_the_count(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from scripts.run_daily_paper_trading import print_report

        lost = AgentRunResult(
            agent="stub_agent",
            portfolio=LivePortfolio(agent="stub_agent", cash=10_000),
            journal_failures=2,
        )
        clean = AgentRunResult(
            agent="other_agent",
            portfolio=LivePortfolio(agent="other_agent", cash=10_000),
        )

        print_report([lost, clean])

        out = capsys.readouterr().out
        assert "2 journal row(s) NOT written" in out
        assert out.count("NOT written") == 1
