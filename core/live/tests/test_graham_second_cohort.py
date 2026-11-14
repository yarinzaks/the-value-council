"""The second Graham is a control, and a control only works if it is identical.

The pair exists to isolate one variable — the date the book opened. Every
other difference between the two Grahams is a confound that makes the
comparison mean nothing, and the parameters are written out twice in
``build_default_adapters`` rather than shared, so nothing in the code
stops the two drifting apart. These tests are what stops it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date
from pathlib import Path

import pytest

from agents.graham.net_net import BenjaminGraham
from core.backtest.decision_logger import DecisionLogger
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.backtest.strategy_runner import FundamentalsLookup, PriceLookup
from core.live.agent_adapter import AgentAdapter, GrahamLive, GrahamSecondCohortLive
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


# ---------------------------------------------------------------------------
# The journal (audit SF-X7)
#
# The strategy writes its intent rows under ``strategy.name``, which is a
# class attribute on BenjaminGraham. Both cohorts run that class, so the
# second Graham's BUY/WATCH rows used to land in the first one's folder,
# and the journal could not tell the two books apart.
# ---------------------------------------------------------------------------
def _defensive_fin(ticker: str) -> PointInTimeFinancials:
    # P/E 5, P/B 1.25 at $10, current ratio 2.5, D/E 0.125, $1B cap:
    # a Defensive buy and nowhere near a Net-Net (NCAV is $2/share).
    as_of = date(2024, 6, 30)
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=as_of,
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=date(2024, 2, 15),
            period_of_report=date(2023, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        eps_diluted=2.0,
        eps_basic=2.0,
        total_equity=800_000_000.0,
        current_assets=500_000_000.0,
        current_liabilities=200_000_000.0,
        total_debt=100_000_000.0,
        long_term_debt=100_000_000.0,
        net_income=50_000_000.0,
        shares_outstanding=100_000_000.0,
        total_liabilities=300_000_000.0,
        dividends_paid=-20_000_000.0,
    )


class _Prices(PriceLookup):
    # Subclassed for the type, not the behaviour: no loader behind it.
    def __init__(self, values: Mapping[str, float]) -> None:
        self._values = dict(values)

    def get(self, ticker: str) -> float | None:
        return self._values.get(ticker)


class _Fundamentals(FundamentalsLookup):
    def __init__(self, values: Mapping[str, PointInTimeFinancials]) -> None:
        self._values = dict(values)

    def get(self, ticker: str) -> PointInTimeFinancials | None:
        return self._values.get(ticker)


def _run_select(adapter: GrahamLive, tickers: list[str]) -> None:
    fins = {t: _defensive_fin(t) for t in tickers}
    adapter.strategy.select(
        date(2024, 6, 30),
        tickers,
        _Prices({t: 10.0 for t in tickers}),
        _Fundamentals(fins),
    )


def _journal_agents(root: Path) -> dict[str, list[str]]:
    """Folder name -> agent field of every row written under it."""
    out: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.json")):
        rows = json.loads(path.read_text())
        out.setdefault(path.parent.name, []).extend(r["agent"] for r in rows)
    return out


def _graham(logger: DecisionLogger, cls: type[GrahamLive]) -> GrahamLive:
    # No edgar_cache: market cap falls back to the filed share count,
    # so the test reads no cache and touches no network.
    return cls(
        BenjaminGraham(
            portfolio_size=30,
            min_market_cap=500_000_000.0,
            decision_logger=logger,
        )
    )


def test_second_cohort_intent_rows_land_in_benjamin_graham_2(tmp_path: Path) -> None:
    logger = DecisionLogger(root=tmp_path / "decisions")
    second = _graham(logger, GrahamSecondCohortLive)

    _run_select(second, ["AAA", "BBB"])

    journal = _journal_agents(tmp_path / "decisions")
    assert set(journal) == {SECOND}, (
        f"the second Graham journaled under {sorted(journal)}; its rows "
        "belong to its own book"
    )
    assert journal[SECOND] and set(journal[SECOND]) == {SECOND}


def test_the_first_cohort_still_journals_under_benjamin_graham(tmp_path: Path) -> None:
    logger = DecisionLogger(root=tmp_path / "decisions")
    first = _graham(logger, GrahamLive)

    _run_select(first, ["AAA"])

    journal = _journal_agents(tmp_path / "decisions")
    assert set(journal) == {FIRST}
    assert set(journal[FIRST]) == {FIRST}


def test_both_cohorts_on_one_logger_keep_separate_journals(tmp_path: Path) -> None:
    # The live shape: one DecisionLogger shared by every adapter. The
    # same name bought by both books is two rows, one in each folder —
    # before, the second row was dropped as a duplicate of the first.
    logger = DecisionLogger(root=tmp_path / "decisions")
    first = _graham(logger, GrahamLive)
    second = _graham(logger, GrahamSecondCohortLive)

    _run_select(first, ["AAA"])
    _run_select(second, ["AAA"])

    journal = _journal_agents(tmp_path / "decisions")
    assert journal == {FIRST: [FIRST], SECOND: [SECOND]}


def test_the_rename_leaves_the_first_cohort_strategy_alone(
    adapters: list[AgentAdapter],
) -> None:
    # The rename belongs to the second adapter's own strategy instance;
    # the class attribute every other BenjaminGraham reads is untouched.
    # The roster is read directly, not through the untyped ``_by_name``.
    by_name = {a.name: a for a in adapters}
    assert BenjaminGraham.name == FIRST
    assert by_name[FIRST].strategy.name == FIRST
    assert by_name[SECOND].strategy.name == SECOND
