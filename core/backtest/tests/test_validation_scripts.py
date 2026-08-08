"""Invariants every full-market validation script has to hold.

Two of them, both learned the hard way:

* **One window.** The leaderboard is only a ranking if every agent ran
  the same dates.
* **One journal.** A backtest's decisions belong in their own log, not
  mixed into the record of what the agent did live.

These tests read the scripts as source rather than importing them,
because each builds its :class:`RunnerConfig` inside ``main()`` behind a
multi-gigabyte EDGAR cache load. Both invariants are source-level
anyway: *no script names its own dates, and none takes the default
decision root*.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.backtest.validation_window import VALIDATION_END, VALIDATION_START
from core.paths import backtest_decisions_dir, decisions_dir

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: One per agent. These log decisions; the council script does not.
AGENT_SCRIPTS: list[Path] = sorted(
    _REPO_ROOT.glob("agents/*/run_full_market_validation.py")
)

#: Every script whose output the dashboard leaderboard ranks.
VALIDATION_SCRIPTS: list[Path] = [
    *AGENT_SCRIPTS,
    _REPO_ROOT / "scripts" / "run_council_validation.py",
]


def _ids(paths: list[Path]) -> list[str]:
    return [p.relative_to(_REPO_ROOT).as_posix() for p in paths]


class TestTheWindowItself:
    def test_it_spans_five_calendar_years(self) -> None:
        assert VALIDATION_START.year == 2019
        assert VALIDATION_END.year == 2024

    def test_it_starts_before_it_ends(self) -> None:
        assert VALIDATION_START < VALIDATION_END

    def test_it_starts_in_december_not_january(self) -> None:
        # The first mark has to be a 100%-cash baseline struck before
        # 2020 opens. Starting on 2020-01-02 would fold the first day's
        # move into the strategy's return.
        assert VALIDATION_START.month == 12


class TestEveryScriptIsFound:
    def test_every_agent_plus_the_council(self) -> None:
        # Ten named investors and one factor composite. A new agent that
        # forgets its validation script would silently shrink this list
        # instead of failing anything — and an agent added without one
        # never appears on the leaderboard at all.
        assert len(AGENT_SCRIPTS) == 11
        assert len(VALIDATION_SCRIPTS) == 12

    def test_the_paths_exist(self) -> None:
        missing = [p for p in VALIDATION_SCRIPTS if not p.is_file()]
        assert missing == []


@pytest.mark.parametrize("script", VALIDATION_SCRIPTS, ids=_ids(VALIDATION_SCRIPTS))
class TestNoScriptNamesItsOwnDates:
    def test_it_uses_the_shared_constants(self, script: Path) -> None:
        src = script.read_text()
        assert "start_date=VALIDATION_START" in src
        assert "end_date=VALIDATION_END" in src

    def test_it_uses_the_shared_rebalance_frequency(self, script: Path) -> None:
        # Same failure mode as the window: a per-script literal makes two
        # rows in one table mean different things. A doctrine asked once
        # a year and one asked quarterly are not the same strategy.
        src = script.read_text()
        assert "rebalance_freq=VALIDATION_REBALANCE" in src
        assert 'rebalance_freq="' not in src

    def test_it_constructs_no_date_literal(self, script: Path) -> None:
        # Catches the regression directly: Schloss ran from 2022-01-04
        # and Greenblatt from 2022-12-30, each behind a comment calling
        # itself a "quick" run, while the leaderboard showed all ten
        # side by side.
        calls = [
            node
            for node in ast.walk(ast.parse(script.read_text()))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "date"
        ]
        assert calls == [], (
            f"{script.name} builds a date() literal; import the window from "
            "core.backtest.validation_window instead"
        )


@pytest.mark.parametrize("script", AGENT_SCRIPTS, ids=_ids(AGENT_SCRIPTS))
class TestNoScriptWritesIntoTheLiveJournal:
    def test_it_passes_an_explicit_decision_root(self, script: Path) -> None:
        # A bare DecisionLogger() defaults to the live journal. Graham's
        # 2020-2024 rebalances landed there as 52 BUY rows dated in the
        # past, and the dashboard concatenates every file in an agent's
        # directory — so a backtest wrote itself into the record of what
        # the agent actually did.
        src = script.read_text()
        assert "DecisionLogger()" not in src, (
            f"{script.name} takes the default decision root; pass "
            "root=backtest_decisions_dir()"
        )
        assert "DecisionLogger(root=backtest_decisions_dir())" in src

    def test_the_two_roots_are_different_directories(self, script: Path) -> None:
        # Belt and braces: the assertion above is only meaningful while
        # the two helpers actually resolve somewhere different.
        assert backtest_decisions_dir() != decisions_dir()
