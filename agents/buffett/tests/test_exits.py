"""Buffett's own exit rules, as shadow verdicts (audit SF-X1, item 13d).

Playbook §7.1 names four legitimate sell triggers and §7.2 a longer
list of non-triggers — a price decline of any size, a bad quarter,
reaching fair value. None of it ran: ``WarrenBuffett.select`` ignored
``held`` and the engine sold a Buffett name only for leaving the day's
target list (SFM went on 2026-10-29 at -42.8% and was bought back on
11-03). These tests pin the rules as
:class:`~core.live.doctrine_exits.ExitVerdict` objects published on
``last_exit_decisions``:

* trigger 1 — moat erosion: the franchise record the entry screen reads
  (:func:`agents.buffett.moat.assess_franchise`) no longer shows a
  durable advantage;
* trigger 2 — management failure, by the screen's own proxy: a reported
  year of negative operating cash flow among the last five;
* trigger 4 — extreme overvaluation: price above 1.5x the
  owner-earnings intrinsic value;
* retention — anything else is kept, however far the price fell and
  whether or not the name is still on today's list.

And in shadow — Buffett's mode — none of this changes a weight.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from agents.buffett import exits as buffett_exits
from agents.buffett.wonderful_business import WarrenBuffett
from core.backtest.decision_logger import DecisionLogger
from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import HeldPosition
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact
from core.live.agent_adapter import BuffettLive
from core.live.doctrine_exits import ExitVerdict
from core.live.portfolio import LivePortfolio, Position
from core.live.runner import DailyRunner

from .conftest import make_fact, make_pit

AS_OF = date(2024, 6, 30)
YEARS = range(2014, 2024)  # FY2014..FY2023, ten 10-Ks filed by AS_OF

#: Flat $1.0bn owner earnings (OCF $1.2bn less capex $0.2bn), no growth,
#: 5% discount, 13x terminal: $15.7026bn, or $15.70 on 1bn shares.
IV_PER_SHARE = 15.70260722521468


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
    years: range = YEARS,
    net_income: Mapping[int, float] | None = None,
    ocf: Mapping[int, float] | None = None,
) -> list[XbrlFact]:
    """Ten clean years: NI $1.0bn on $5.0bn equity (ROE 20%), OCF $1.2bn,
    capex $0.2bn. ``net_income`` and ``ocf`` override single years."""
    out: list[XbrlFact] = []
    for fy in years:
        end = date(fy, 12, 31)
        filed = date(fy + 1, 2, 15)
        acc = f"{ticker}-{fy}"
        values = {
            "NetIncomeLoss": (net_income or {}).get(fy, 1_000_000_000.0),
            "StockholdersEquity": 5_000_000_000.0,
            "NetCashProvidedByUsedInOperatingActivities": (ocf or {}).get(
                fy, 1_200_000_000.0
            ),
            "PaymentsToAcquirePropertyPlantAndEquipment": 200_000_000.0,
        }
        out.extend(
            make_fact(concept=c, value=v, period_end=end, filed=filed, accession=acc)
            for c, v in values.items()
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
    position: HeldPosition,
) -> ExitVerdict | None:
    market_cap = (
        price * fin.shares_outstanding
        if fin is not None and price is not None and fin.shares_outstanding
        else None
    )
    return buffett_exits.decide(
        position, fin, price, market_cap, cache=cache, as_of=AS_OF
    )


@pytest.fixture(autouse=True)
def _beverages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every synthetic ticker is a beverage company: not excluded by
    SIC, not a financial. Both lookups, because a test ticker can be a
    real one — "C" is Citigroup, which the real table calls a bank."""
    monkeypatch.setattr("agents.buffett.filters.sic_for", lambda _t: 2080)
    monkeypatch.setattr("core.screener.business_type.sic_for", lambda _t: 2080)


# ---------------------------------------------------------------------------
# The two tests the audit names (report section 6, item 13)
# ---------------------------------------------------------------------------
def test_held_name_that_still_qualifies_is_retained_when_it_slips_in_rank(
    tmp_path: Path,
) -> None:
    """Three wonderful businesses pass; the strategy keeps room for two.

    HELD ranks third — its worst year was a 16% ROE against 20% for the
    others, and the ranking orders franchises by their floor — so it is
    off the target list. Its moat holds (ROE >= 15% every year), cash
    flow never went negative and it trades at 0.64x intrinsic value.
    Buffett's favourite holding period is forever: keep.
    """
    cache = _cache(
        tmp_path,
        A=_facts("A"),
        B=_facts("B"),
        HELD=_facts("HELD", net_income={2015: 800_000_000.0}),
    )
    fins = {t: make_pit(t) for t in ("A", "B", "HELD")}
    prices = {"A": 10.0, "B": 10.0, "HELD": 10.0}
    strategy = WarrenBuffett(edgar_cache=cache, portfolio_size=2)
    held = {"HELD": _held("HELD", entry=12.0)}

    weights = strategy.select(
        AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
    )

    assert set(weights) == {"A", "B"}  # it did slip in rank
    assert strategy.last_exit_decisions == [
        ExitVerdict(
            "HELD",
            sell=False,
            reason=(
                "moat intact (ROE >= 15% in 10/10 years (worst 16.0%)); "
                "operating cash flow positive in each of the last 5 reported "
                "years; price $10.00 is 0.64x intrinsic value $15.70/share, "
                "under the 1.5x line"
            ),
        )
    ]


def test_exit_reason_in_journal_names_the_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real adapter, the real runner, shadow mode.

    RICH ran to $25 against $15.70 of intrinsic value — 1.59x. The
    doctrine journal says SELL and names the trigger; the engine's own
    action is recorded beside it and — shadow — is whatever it would
    have been.
    """
    cache = _cache(tmp_path, CHEAP=_facts("CHEAP"), RICH=_facts("RICH"))
    fins = {"CHEAP": make_pit("CHEAP"), "RICH": make_pit("RICH")}
    prices: dict[str, float | None] = {"CHEAP": 10.0, "RICH": 25.0}
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
    adapter = BuffettLive(WarrenBuffett(edgar_cache=cache, portfolio_size=8))
    assert adapter.exit_mode == "shadow"

    book = LivePortfolio(agent=adapter.name, cash=2_000.0)
    book.last_rebalance_date = "2024-06-27"
    book.positions.append(
        Position(
            ticker="RICH",
            shares=10.0,
            entry_price=11.0,
            entry_date=(AS_OF - timedelta(days=400)).isoformat(),
            current_price=25.0,
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
        "extreme overvaluation: price $25.00 is 1.59x intrinsic value "
        "$15.70/share (owner-earnings DCF), above the 1.5x line"
    )
    assert "intrinsic value" in row["criteria_met"][0]
    assert row["market_conditions"]["exit_mode"] == "shadow"


# ---------------------------------------------------------------------------
# Trigger 1 — moat erosion
# ---------------------------------------------------------------------------
class TestMoatErosion:
    def test_a_franchise_record_that_no_longer_holds_sells_even_cheap(
        self, tmp_path: Path
    ) -> None:
        """Three of ten years under 15%: the moat evidence is gone, and
        a $8 price on $15.70 of value does not save it."""
        weak = {fy: 500_000_000.0 for fy in (2021, 2022, 2023)}  # ROE 10%
        cache = _cache(tmp_path, X=_facts("X", net_income=weak))
        v = _decide(cache, make_pit("X"), 8.0, _held("X", entry=10.0))
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "moat eroded: ROE >= 15% in only 7/10 years "
                "— 80% required"
            ),
        )

    def test_two_bad_years_in_ten_is_not_erosion(self, tmp_path: Path) -> None:
        """The franchise test tolerates a recession and a write-down."""
        weak = {fy: 500_000_000.0 for fy in (2022, 2023)}
        cache = _cache(tmp_path, X=_facts("X", net_income=weak))
        v = _decide(cache, make_pit("X"), 8.0, _held("X", entry=10.0))
        assert v is not None and v.sell is False
        assert v.reason.startswith("moat intact (ROE >= 15% in 8/10 years")


# ---------------------------------------------------------------------------
# Trigger 2 — management failure, by the screen's own proxy
# ---------------------------------------------------------------------------
class TestManagement:
    def test_a_recent_negative_cash_flow_year_sells(self, tmp_path: Path) -> None:
        cache = _cache(
            tmp_path, X=_facts("X", ocf={2022: -100_000_000.0})
        )
        v = _decide(cache, make_pit("X"), 8.0, _held("X", entry=10.0))
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "management: operating cash flow negative in FY2022 "
                "($-100,000,000) while earnings were reported positive"
            ),
        )

    def test_a_negative_year_older_than_five_reports_is_not_a_sell(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(tmp_path, X=_facts("X", ocf={2017: -100_000_000.0}))
        v = _decide(cache, make_pit("X"), 8.0, _held("X", entry=10.0))
        assert v is not None and v.sell is False


# ---------------------------------------------------------------------------
# Trigger 4 — extreme overvaluation, and what is not a sell
# ---------------------------------------------------------------------------
class TestValuation:
    def test_exactly_one_and_a_half_times_value_is_kept(
        self, tmp_path: Path
    ) -> None:
        """§7.1: "price exceeds 1.5x" — at the line is not past it."""
        cache = _cache(tmp_path, X=_facts("X"))
        price = 1.5 * IV_PER_SHARE
        v = _decide(cache, make_pit("X"), price, _held("X", entry=10.0))
        assert v is not None and v.sell is False
        assert "1.50x intrinsic value $15.70/share" in v.reason

    def test_just_past_the_line_sells(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X"), 23.56, _held("X", entry=10.0))
        assert v is not None and v.sell is True
        assert v.reason.startswith("extreme overvaluation: price $23.56 is 1.50x")

    def test_reaching_fair_value_is_not_a_sell(self, tmp_path: Path) -> None:
        """§7.2: Buffett's departure from Graham. $18 is 1.15x value."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X"), 18.0, _held("X", entry=10.0))
        assert v is not None and v.sell is False

    def test_a_price_decline_of_any_size_is_never_a_sell(
        self, tmp_path: Path
    ) -> None:
        """§7.2: down 75% from cost, moat intact — "a buying opportunity"."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, make_pit("X"), 3.0, _held("X", entry=12.0))
        assert v is not None and v.sell is False


# ---------------------------------------------------------------------------
# What does not produce a verdict at all
# ---------------------------------------------------------------------------
class TestNoOpinion:
    def test_no_fundamentals_gives_no_verdict(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, None, 10.0, _held("X", entry=10.0)) is None

    def test_no_price_gives_no_verdict(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, make_pit("X"), None, _held("X", entry=10.0)) is None

    def test_too_short_a_record_to_judge_the_moat_gives_no_verdict(
        self, tmp_path: Path
    ) -> None:
        """Four 10-Ks: enough to value (three needed), not enough to
        call a moat durable or eroded (five needed). A keep here would
        hold the name on no evidence."""
        cache = _cache(tmp_path, X=_facts("X", years=range(2020, 2024)))
        assert _decide(cache, make_pit("X"), 10.0, _held("X", entry=10.0)) is None

    def test_but_a_short_record_still_sells_on_overvaluation(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(tmp_path, X=_facts("X", years=range(2020, 2024)))
        v = _decide(cache, make_pit("X"), 25.0, _held("X", entry=10.0))
        assert v is not None and v.sell is True
        assert v.reason.startswith("extreme overvaluation")

    def test_a_financial_gives_no_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bank's ROE is a leverage choice and its OCF - capex is not
        owner earnings: neither the moat test nor the DCF applies."""
        monkeypatch.setattr("core.screener.business_type.sic_for", lambda _t: 6020)
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, make_pit("X"), 25.0, _held("X", entry=10.0)) is None

    def test_no_intrinsic_value_gives_no_verdict(self, tmp_path: Path) -> None:
        """Owner earnings negative on average: the DCF cannot value it,
        so overvaluation cannot be ruled out."""
        burn = {fy: -500_000_000.0 for fy in range(2019, 2024)}
        # Cash flow negative but earnings positive would trip trigger 2;
        # here capex swamps a positive OCF instead.
        facts = [
            f
            if f.concept != "PaymentsToAcquirePropertyPlantAndEquipment"
            or f.fiscal_year not in burn
            else make_fact(
                concept=f.concept,
                value=2_000_000_000.0,
                period_end=f.period_end,
                filed=f.filed,
                accession=f.accession_number,
            )
            for f in _facts("X")
        ]
        cache = _cache(tmp_path, X=facts)
        assert _decide(cache, make_pit("X"), 10.0, _held("X", entry=10.0)) is None


# ---------------------------------------------------------------------------
# The strategy wiring
# ---------------------------------------------------------------------------
class TestStrategyPublishesVerdicts:
    def _setup(
        self, tmp_path: Path
    ) -> tuple[EdgarCache, dict[str, PointInTimeFinancials], dict[str, float]]:
        cache = _cache(tmp_path, A=_facts("A"), B=_facts("B"), C=_facts("C"))
        fins = {t: make_pit(t) for t in ("A", "B", "C")}
        prices = {"A": 10.0, "B": 10.0, "C": 25.0}
        return cache, fins, prices

    def test_nothing_held_publishes_nothing(self, tmp_path: Path) -> None:
        cache, fins, prices = self._setup(tmp_path)
        strategy = WarrenBuffett(edgar_cache=cache)
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []

    def test_weights_do_not_depend_on_what_is_held(self, tmp_path: Path) -> None:
        """Shadow: the verdicts are journaled, the target list is not
        touched. A held name the doctrine keeps is not added back, and
        one it sells is not removed."""
        cache, fins, prices = self._setup(tmp_path)
        held = {"A": _held("A", entry=9.0), "C": _held("C", entry=9.0)}
        bare = WarrenBuffett(edgar_cache=cache)
        holding = WarrenBuffett(edgar_cache=cache)
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
        strategy = WarrenBuffett(edgar_cache=cache)
        weights = strategy.select(
            AS_OF,
            ["C"],
            _lookup({"C": 25.0}),
            _lookup(fins),
            held={"C": _held("C", entry=9.0)},
        )
        assert weights == {}
        assert [v.sell for v in strategy.last_exit_decisions] == [True]

    def test_a_held_name_outside_the_universe_is_judged_from_its_lookup(
        self, tmp_path: Path
    ) -> None:
        cache, fins, prices = self._setup(tmp_path)
        strategy = WarrenBuffett(edgar_cache=cache)
        strategy.select(
            AS_OF,
            ["B"],
            _lookup(prices),
            _lookup(fins),
            held={"A": _held("A", entry=9.0)},
        )
        assert [(v.ticker, v.sell) for v in strategy.last_exit_decisions] == [
            ("A", False)
        ]

    def test_the_previous_days_verdicts_do_not_survive_a_new_select(
        self, tmp_path: Path
    ) -> None:
        cache, fins, prices = self._setup(tmp_path)
        strategy = WarrenBuffett(edgar_cache=cache)
        strategy.select(
            AS_OF,
            list(fins),
            _lookup(prices),
            _lookup(fins),
            held={"C": _held("C", entry=9.0)},
        )
        assert strategy.last_exit_decisions
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []
