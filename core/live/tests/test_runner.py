"""Tests for DailyRunner's trade-execution seam.

``_run_one`` is the method that turns a scan into trades. It had no test
coverage at all, which is how a held position could be sold at a price
that no longer existed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.backtest.decision_logger import DecisionLogger
from core.live.agent_adapter import LiveTarget, ScanResult
from core.live.portfolio import LivePortfolio, Position
from core.live.runner import DailyRunner

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

    def __init__(self, prices: dict[str, float | None]) -> None:
        self._prices = prices
        self.calls: list[tuple[str, date, bool]] = []

    def get_price_on(
        self, ticker: str, as_of: date, *, force_refresh: bool = False
    ) -> float | None:
        self.calls.append((ticker, as_of, force_refresh))
        return self._prices.get(ticker)


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
        price_loader=_StubPriceLoader({}),  # type: ignore[arg-type]
        universe=object(),  # type: ignore[arg-type]
        pit_loader=object(),  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
        decision_logger=DecisionLogger(root=tmp_path / "decisions"),
    )


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


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
class TestFractionalSizing:
    """Whole-share rounding discarded the remainder of every position.
    On a $10,000 book across 27 names that residue compounded into
    roughly a fifth of the portfolio sitting in cash against a design
    target of zero."""

    def test_equal_weight_book_deploys_its_cash(self, runner: DailyRunner) -> None:
        n = 27
        weight = 1.0 / n
        # Deliberately awkward prices — the whole-share remainder is
        # worst when the price does not divide the slot.
        prices = {f"T{i:02d}": 37.0 + i * 11.3 for i in range(n)}
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
        cash_pct = p.cash / p.total_nav * 100.0
        assert cash_pct < 1.0, f"{cash_pct:.2f}% left idle"
        for pos in p.positions:
            assert pos.weight_pct == pytest.approx(100.0 / n, abs=0.10)

    def test_a_slot_smaller_than_one_share_still_fills(
        self, runner: DailyRunner
    ) -> None:
        # $10,000 / 30 = $333 per slot. A $2,000 share used to buy zero.
        runner.price_loader = _StubPriceLoader({"PRICEY": 2_000.0})  # type: ignore[assignment]
        adapter = _StubAdapter("stub_agent", [_target("PRICEY", weight=1 / 30)])

        result = runner._run_one(
            adapter,  # type: ignore[arg-type]
            AS_OF,
            ["PRICEY"],
            {"PRICEY": 2_000.0},
            {"PRICEY": None},
        )

        assert result.portfolio.has("PRICEY")
        assert result.portfolio.positions[0].shares < 1.0


class TestRebalanceBand:
    """Positions were sized once at entry and never touched again, so a
    name that doubled became twice its intended weight."""

    def _drifted(
        self, runner: DailyRunner, *, entry: float, now: float
    ) -> DailyRunner:
        p = LivePortfolio(agent="stub_agent")
        p.positions.append(
            Position(
                ticker="DRIFT",
                shares=50.0,
                entry_price=entry,
                entry_date="2026-07-01",
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
        assert pos.weight_pct == pytest.approx(50.0, abs=1.0)

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
