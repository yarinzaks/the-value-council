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

    def run_scan(self, as_of, universe, prices, fundamentals) -> ScanResult:  # type: ignore[no-untyped-def]
        return ScanResult(
            targets=self._targets, watchlist=[], universe_size=len(universe)
        )


class _StubPriceLoader:
    """Returns whatever the test dictates, including None."""

    def __init__(self, prices: dict[str, float | None]) -> None:
        self._prices = prices
        self.calls: list[tuple[str, date]] = []

    def get_price_on(self, ticker: str, as_of: date) -> float | None:
        self.calls.append((ticker, as_of))
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
