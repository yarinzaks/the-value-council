"""The second Graham is a control, and a control only works if it is identical.

The pair exists to isolate one variable — the date the book opened. Every
other difference between the two Grahams is a confound that makes the
comparison mean nothing, and the parameters are written out twice in
``build_default_adapters`` rather than shared, so nothing in the code
stops the two drifting apart. These tests are what stops it.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

import pytest

from core.backtest.decision_logger import DecisionLogger
from core.live.agent_adapter import GrahamLive, GrahamSecondCohortLive
from core.live.runner import build_default_adapters

FIRST = "benjamin_graham"
SECOND = "benjamin_graham_2"


@pytest.fixture
def adapters(tmp_path):
    return build_default_adapters(
        decision_logger=DecisionLogger(root=tmp_path / "decisions"),
    )


def _by_name(adapters, name):
    found = [a for a in adapters if a.name == name]
    assert len(found) == 1, f"expected exactly one {name}, got {len(found)}"
    return found[0]


def test_both_grahams_are_in_the_roster(adapters):
    names = [a.name for a in adapters]
    assert FIRST in names
    assert SECOND in names


def test_the_two_books_have_distinct_slugs(adapters):
    # They would share a portfolio file and overwrite each other.
    assert _by_name(adapters, FIRST).name != _by_name(adapters, SECOND).name


def test_the_second_graham_runs_the_same_strategy_class(adapters):
    first = _by_name(adapters, FIRST).strategy
    second = _by_name(adapters, SECOND).strategy
    assert type(first) is type(second)
    assert first is not second, "each book needs its own strategy instance"


def test_the_two_strategies_are_parameterised_identically(adapters):
    """The test the comment in ``build_default_adapters`` points at.

    Tuning one Graham and not the other turns the pair from a measurement
    of entry timing into a measurement of two different methods. Compare
    every public scalar on the strategy, not a hand-listed few, so a
    parameter added later is covered without anyone remembering to.
    """
    first = _by_name(adapters, FIRST).strategy
    second = _by_name(adapters, SECOND).strategy

    if is_dataclass(first):
        names = [f.name for f in fields(first)]
    else:
        names = [
            k
            for k, v in vars(first).items()
            if not k.startswith("_") and isinstance(v, (int, float, str, bool, type(None)))
        ]

    assert names, "found no parameters to compare — the check would pass vacuously"

    differences = {
        name: (getattr(first, name, None), getattr(second, name, None))
        for name in names
        if not callable(getattr(first, name, None))
        and isinstance(
            getattr(first, name, None), (int, float, str, bool, type(None))
        )
        and getattr(first, name, None) != getattr(second, name, None)
    }
    assert not differences, (
        "the two Grahams are configured differently, so the pair no longer "
        f"isolates entry date: {differences}"
    )


def test_the_second_graham_inherits_the_first_adapter(adapters):
    # Not decoration: the narrative, entry trigger and target collection
    # all come from GrahamLive, and a divergence there would describe the
    # same decision two different ways on the dashboard.
    second = _by_name(adapters, SECOND)
    assert isinstance(second, GrahamLive)
    assert type(second) is GrahamSecondCohortLive
    assert second.entry_trigger == _by_name(adapters, FIRST).entry_trigger
