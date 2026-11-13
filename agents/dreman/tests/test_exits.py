"""Dreman's own exit rules, as shadow verdicts (audit SF-X1, item 13h).

Playbook §7.1: the contrarian thesis is the gap between a bottom-quintile
multiple and the market's, so it is complete when the gap closes — trim
from 80% of the market P/E, out by 100% of it — and §6.5's hard stop
sells at any price when the fundamental tests break. None of it ran:
``DavidDreman.select`` ignored ``held`` and the engine sold a Dreman name
only for leaving the day's target list (53 such sales since 2026-08-05,
none citing a P/E). These tests pin the rules as
:class:`~core.live.doctrine_exits.ExitVerdict` objects published on
``last_exit_decisions``:

* trigger 2 / hard stop, first and at any price — current ratio below
  1.0, D/E above the 1.0 maximum, ROE down two years running, pre-tax
  margin collapsing;
* trigger 1 — P/E at or past 100% of the market's;
* retention — anything else is kept, including the 80%-100% trim zone
  (the trim is not a keep-or-sell verdict), a name that slipped off
  today's list, and a name that fell further in price.

And in shadow — Dreman's mode — none of this changes a weight.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from agents.dreman import exits as dreman_exits
from agents.dreman.contrarian import DavidDreman
from agents.dreman.ranking import MarketPE, score_candidates
from core.backtest.decision_logger import DecisionLogger
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.backtest.strategy_runner import HeldPosition
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact
from core.live.agent_adapter import DremanLive
from core.live.doctrine_exits import ExitVerdict
from core.live.portfolio import LivePortfolio, Position
from core.live.runner import DailyRunner

AS_OF = date(2024, 6, 30)

#: A market whose median P/E is 10.
MARKET = MarketPE(peer_count=40, median_pe=10.0)


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


def _fin(
    ticker: str,
    *,
    eps: float = 2.0,
    net_income: float = 200_000_000.0,
    current_assets: float | None = 3_000_000_000.0,
    current_liabilities: float | None = 1_000_000_000.0,
    debt: float = 100_000_000.0,
) -> PointInTimeFinancials:
    """$2.00 EPS on 100M shares, book $8.00/share, CR 3.0, D/E 0.125."""
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=AS_OF,
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=date(2024, 2, 15),
            period_of_report=date(2023, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        eps_diluted=eps,
        eps_basic=eps,
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        total_equity=800_000_000.0,
        dividends_paid=-40_000_000.0,
        operating_cash_flow=300_000_000.0,
        total_debt=debt,
        long_term_debt=debt,
        net_income=net_income,
        shares_outstanding=100_000_000.0,
    )


def _flow(concept: str, value: float, fy: int, ticker: str) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace="us-gaap",
        unit="USD",
        value=value,
        period_start=date(fy, 1, 1),
        period_end=date(fy, 12, 31),
        filed=date(fy + 1, 2, 15),
        form="10-K",
        fiscal_year=fy,
        fiscal_period="FY",
        accession_number=f"{ticker}-{fy}",
    )


def _instant(concept: str, value: float, fy: int, ticker: str) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace="us-gaap",
        unit="USD",
        value=value,
        period_start=None,
        period_end=date(fy, 12, 31),
        filed=date(fy + 1, 2, 15),
        form="10-K",
        fiscal_year=fy,
        fiscal_period="FY",
        accession_number=f"{ticker}-{fy}",
    )


PRETAX = (
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
    "ExtraordinaryItemsNoncontrollingInterest"
)


def _facts(
    ticker: str,
    *,
    net_income: Mapping[int, float] | None = None,
    equity: Mapping[int, float] | None = None,
    pretax: Mapping[int, float] | None = None,
    revenue: Mapping[int, float] | None = None,
) -> list[XbrlFact]:
    """A healthy history by default.

    ROE 18.75% -> 22.5% -> 25.0% over FY2021-FY2023 (net income on an
    $800M book), and a pre-tax margin flat at 12.5% from FY2018 to
    FY2023 — the five years the margin test spans on AS_OF.
    """
    net_income = (
        {2021: 150e6, 2022: 180e6, 2023: 200e6} if net_income is None else net_income
    )
    equity = {2021: 800e6, 2022: 800e6, 2023: 800e6} if equity is None else equity
    pretax = {2018: 250e6, 2023: 300e6} if pretax is None else pretax
    revenue = {2018: 2.0e9, 2023: 2.4e9} if revenue is None else revenue
    return [
        *(_flow("NetIncomeLoss", v, fy, ticker) for fy, v in net_income.items()),
        *(_instant("StockholdersEquity", v, fy, ticker) for fy, v in equity.items()),
        *(_flow(PRETAX, v, fy, ticker) for fy, v in pretax.items()),
        *(_flow("Revenues", v, fy, ticker) for fy, v in revenue.items()),
    ]


def _cache(tmp_path: Path, **per_ticker: list[XbrlFact]) -> EdgarCache:
    cache = EdgarCache(cache_dir=tmp_path / "edgar")
    for ticker, facts in per_ticker.items():
        cache.save_facts(ticker, facts)
    return cache


def _held(ticker: str, *, entry: float = 12.0, days: int = 120) -> HeldPosition:
    return HeldPosition(
        ticker=ticker,
        shares=10.0,
        entry_price=entry,
        entry_date=AS_OF - timedelta(days=days),
        current_price=entry,
    )


def _decide(
    cache: EdgarCache | None,
    fin: PointInTimeFinancials | None,
    price: float | None,
    *,
    market: MarketPE | None = MARKET,
    entry: float = 12.0,
) -> ExitVerdict | None:
    ticker = fin.ticker if fin is not None else "X"
    return dreman_exits.decide(
        _held(ticker, entry=entry),
        fin,
        price,
        market,
        cache=cache,
        as_of=AS_OF,
    )


@pytest.fixture(autouse=True)
def _one_industry_each(monkeypatch: pytest.MonkeyPatch) -> None:
    """No synthetic ticker has an SIC code, so diversification gives each
    its own industry: a test ticker can be a real one the SIC table
    files beside another."""
    monkeypatch.setattr("agents.dreman.diversification.sic_for", lambda _t: None)


#: A day's screen, ten names alike but for price. A, B and HELD are the
#: three cheapest on P/E, P/CF and P/B and the three highest yields, so
#: at a 30% cut all three qualify on 4 of 4; the other seven set the
#: market. P/Es: 5, 5.5, 6.5, 12, 13, 14, 15, 16, 17, 18 — median 13.5.
UNIVERSE_PRICES = {
    "A": 10.0,
    "B": 11.0,
    "HELD": 13.0,
    "D": 24.0,
    "E": 26.0,
    "F": 28.0,
    "G": 30.0,
    "H": 32.0,
    "I": 34.0,
    "J": 36.0,
}


def _strategy(**kw: Any) -> DavidDreman:
    defaults: dict[str, Any] = {
        "portfolio_size": 2,
        "quintile": 0.3,
        "min_industries": 1,
        "min_market_cap": 100.0,
    }
    defaults.update(kw)
    return DavidDreman(**defaults)


def _universe() -> tuple[dict[str, PointInTimeFinancials], dict[str, float]]:
    return {t: _fin(t) for t in UNIVERSE_PRICES}, dict(UNIVERSE_PRICES)


# ---------------------------------------------------------------------------
# The two tests the audit names (report section 6, item 13)
# ---------------------------------------------------------------------------
def test_held_name_that_still_qualifies_is_retained_when_it_slips_in_rank() -> None:
    """A, B and HELD all pass the screen on 4 of 4; the strategy keeps
    room for two.

    HELD's P/E of 6.50 is the highest of the three, so it ranks third
    and is off the target list. At 48.1% of the market's 13.50 it is far
    from the 80% line where Dreman starts to trim, and nothing in its
    balance sheet broke: keep.
    """
    fins, prices = _universe()
    strategy = _strategy()
    held = {"HELD": _held("HELD", entry=12.0, days=200)}

    weights = strategy.select(
        AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
    )

    assert set(weights) == {"A", "B"}  # it did slip in rank
    assert strategy.last_exit_decisions == [
        ExitVerdict(
            "HELD",
            sell=False,
            reason=(
                "contrarian gap still open: P/E 6.50 is 48.1% of the "
                "market's 13.50 (median of the day's screen), under the 80% "
                "line where trimming begins; hard-stop tests: 2 of 4 judged, "
                "none broke"
            ),
        )
    ]


def test_exit_reason_in_journal_names_the_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real adapter, the real runner, shadow mode.

    G trades at a P/E of 15.00 against the market's 13.50 — 111.1%,
    past the 100% line. The doctrine journal says SELL and names the
    trigger; the engine's own action is recorded beside it and —
    shadow — is whatever it would have been.
    """
    fins, prices_ = _universe()
    prices: dict[str, float | None] = dict(prices_)
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
    adapter = DremanLive(_strategy(portfolio_size=3))
    assert adapter.exit_mode == "shadow"

    book = LivePortfolio(agent=adapter.name, cash=2_000.0)
    book.last_rebalance_date = "2024-06-27"
    book.positions.append(
        Position(
            ticker="G",
            shares=10.0,
            entry_price=18.0,
            entry_date=(AS_OF - timedelta(days=400)).isoformat(),
            current_price=30.0,
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
    assert [r["ticker"] for r in rows] == ["G"]
    row = rows[0]
    assert row["decision"] == "SELL"
    assert row["exit_trigger"] == (
        "P/E reached the market average: P/E 15.00 is 111.1% of the "
        "market's 13.50 (median of the day's screen), at or past the 100% "
        "line where the exit is complete"
    )
    assert "market average" in row["criteria_met"][0]
    assert row["market_conditions"]["exit_mode"] == "shadow"


# ---------------------------------------------------------------------------
# Trigger 1 — the multiple reverts to the market's
# ---------------------------------------------------------------------------
class TestMeanReversion:
    def test_under_eighty_percent_of_the_market_is_a_plain_keep(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _fin("X"), 15.98)  # P/E 7.99
        assert v == ExitVerdict(
            "X",
            sell=False,
            reason=(
                "contrarian gap still open: P/E 7.99 is 79.9% of the "
                "market's 10.00 (median of the day's screen), under the 80% "
                "line where trimming begins; hard-stop tests: 4 of 4 judged, "
                "none broke"
            ),
        )

    def test_at_eighty_percent_trimming_begins_and_the_rest_is_kept(
        self, tmp_path: Path
    ) -> None:
        """§7.1: "Begin trimming at 80% of market average P/E". A verdict
        is keep or sell, so the trim is named, not done."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _fin("X"), 16.0)  # P/E 8.00
        assert v == ExitVerdict(
            "X",
            sell=False,
            reason=(
                "trim zone: P/E 8.00 is 80.0% of the market's 10.00 (median "
                "of the day's screen), between the 80% line where trimming "
                "begins and the 100% line where the exit is complete; the "
                "trim is not executed (a verdict is keep or sell), the "
                "position is kept; hard-stop tests: 4 of 4 judged, none broke"
            ),
        )

    def test_just_under_the_market_multiple_is_still_kept(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _fin("X"), 19.98)  # P/E 9.99
        assert v is not None and v.sell is False
        assert v.reason.startswith("trim zone: P/E 9.99 is 99.9%")

    def test_at_the_market_multiple_the_exit_is_complete(
        self, tmp_path: Path
    ) -> None:
        """§7.1: "Full exit by 100% of market average P/E" — at the line,
        not only past it."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _fin("X"), 20.0)  # P/E 10.00
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "P/E reached the market average: P/E 10.00 is 100.0% of the "
                "market's 10.00 (median of the day's screen), at or past the "
                "100% line where the exit is complete"
            ),
        )

    def test_a_price_decline_of_any_size_is_never_a_sell(
        self, tmp_path: Path
    ) -> None:
        """§7.2 and §7.3: downturns create contrarian opportunities, and
        the agent does not capitulate at extremes. Down 80% from a $25
        cost, the multiple is only cheaper: keep."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _fin("X"), 5.0, entry=25.0)
        assert v is not None and v.sell is False
        assert v.reason.startswith("contrarian gap still open: P/E 2.50 is 25.0%")


# ---------------------------------------------------------------------------
# Trigger 2 — the hard stop, at any price
# ---------------------------------------------------------------------------
class TestHardStop:
    def test_current_ratio_below_one_sells_deep_below_the_market(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        fin = _fin("X", current_liabilities=3_200_000_000.0)  # CR 0.94
        v = _decide(cache, fin, 5.0)  # P/E 2.50
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "fundamental deterioration (hard stop): current ratio 0.94 "
                "below 1.0"
            ),
        )

    def test_a_current_ratio_of_exactly_one_is_not_a_breach(
        self, tmp_path: Path
    ) -> None:
        """§7.1: "drops below 1.0" — 1.0 is the playbook's acceptable band."""
        cache = _cache(tmp_path, X=_facts("X"))
        fin = _fin("X", current_liabilities=3_000_000_000.0)
        v = _decide(cache, fin, 15.0)
        assert v is not None and v.sell is False

    def test_leverage_above_the_maximum_sells(self, tmp_path: Path) -> None:
        """Test 4's maximum acceptable D/E is 1.0; $900M on an $800M book
        is 1.12."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _fin("X", debt=900_000_000.0), 15.0)
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "fundamental deterioration (hard stop): D/E 1.12 above the "
                "1.0 maximum"
            ),
        )

    def test_roe_down_two_years_running_sells(self, tmp_path: Path) -> None:
        """§7.1: "ROE declines for 2+ consecutive years"."""
        cache = _cache(
            tmp_path,
            X=_facts("X", net_income={2021: 200e6, 2022: 180e6, 2023: 150e6}),
        )
        v = _decide(cache, _fin("X"), 15.0)
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "fundamental deterioration (hard stop): ROE fell two years "
                "running, 25.0% (FY to 2021-12-31) -> 22.5% (FY to "
                "2022-12-31) -> 18.8% (FY to 2023-12-31)"
            ),
        )

    def test_one_year_of_falling_roe_is_not_a_breach(self, tmp_path: Path) -> None:
        cache = _cache(
            tmp_path,
            X=_facts("X", net_income={2021: 150e6, 2022: 200e6, 2023: 180e6}),
        )
        v = _decide(cache, _fin("X"), 15.0)
        assert v is not None and v.sell is False

    def test_a_skipped_year_is_not_two_consecutive_declines(
        self, tmp_path: Path
    ) -> None:
        """FY2019, FY2021, FY2023 falling is not three consecutive years:
        the trend is unknown, and the test is not judged."""
        cache = _cache(
            tmp_path,
            X=_facts(
                "X",
                net_income={2019: 200e6, 2021: 180e6, 2023: 150e6},
                equity={2019: 800e6, 2021: 800e6, 2023: 800e6},
            ),
        )
        assert dreman_exits.annual_roe_history(cache, "X", AS_OF) is None
        v = _decide(cache, _fin("X"), 15.0)
        assert v is not None and v.sell is False
        assert v.reason.endswith("hard-stop tests: 3 of 4 judged, none broke")

    def test_a_10k_not_yet_filed_is_not_read(self, tmp_path: Path) -> None:
        """Point in time: on 2024-01-31 the FY2023 10-K (filed 2024-02-15)
        is not known, so FY2020-FY2022 is the latest run of three."""
        cache = _cache(
            tmp_path,
            X=_facts(
                "X",
                net_income={2020: 120e6, 2021: 150e6, 2022: 180e6, 2023: 100e6},
                equity={2020: 800e6, 2021: 800e6, 2022: 800e6, 2023: 800e6},
            ),
        )
        early = dreman_exits.annual_roe_history(cache, "X", date(2024, 1, 31))
        late = dreman_exits.annual_roe_history(cache, "X", AS_OF)
        assert early is not None and [round(r.roe_pct, 2) for r in early] == [
            15.0,
            18.75,
            22.5,
        ]
        assert late is not None and [round(r.roe_pct, 2) for r in late] == [
            18.75,
            22.5,
            12.5,
        ]

    def test_pretax_margin_collapsing_sells(self, tmp_path: Path) -> None:
        """§7.1: "Pre-tax margins collapsing" — 12.5% to 6.0% over five
        years, past the battery's 3-point line."""
        cache = _cache(
            tmp_path,
            X=_facts("X", pretax={2018: 250e6, 2023: 144e6}),
        )
        v = _decide(cache, _fin("X"), 15.0)
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "fundamental deterioration (hard stop): pre-tax margin "
                "collapsing, down 6.5 points over 5 years"
            ),
        )

    def test_it_fires_even_when_earnings_are_gone(self, tmp_path: Path) -> None:
        """No P/E to judge, but the hard stop is a sale at any price."""
        cache = _cache(tmp_path, X=_facts("X"))
        fin = _fin(
            "X",
            eps=-0.5,
            net_income=-50e6,
            current_liabilities=4_000_000_000.0,
        )
        v = _decide(cache, fin, 5.0)
        assert v is not None and v.sell is True

    def test_an_unreported_figure_is_not_a_breach(self) -> None:
        """No current-asset tags, no cache: two of four tests cannot be
        judged and none of the rest broke. Absence is not evidence."""
        fin = _fin("X", current_assets=None, current_liabilities=None)
        v = _decide(None, fin, 15.0)
        assert v is not None and v.sell is False
        assert v.reason.endswith("hard-stop tests: 1 of 4 judged, none broke")


# ---------------------------------------------------------------------------
# What does not produce a verdict at all
# ---------------------------------------------------------------------------
class TestNoOpinion:
    def test_no_fundamentals_gives_no_verdict(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, None, 15.0) is None

    def test_no_price_gives_no_verdict(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, _fin("X"), None) is None

    def test_no_market_gives_no_verdict(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, _fin("X"), 15.0, market=None) is None

    def test_no_market_pe_gives_no_verdict(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        market = MarketPE(peer_count=0, median_pe=None)
        assert _decide(cache, _fin("X"), 15.0, market=market) is None

    def test_a_loss_with_intact_balance_sheet_gives_no_verdict(
        self, tmp_path: Path
    ) -> None:
        """Negative trailing EPS leaves the P/E undefined; with every
        hard-stop test holding there is nothing to judge on."""
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, _fin("X", eps=-0.5, net_income=-50e6), 5.0) is None


# ---------------------------------------------------------------------------
# The market the verdicts are measured against
# ---------------------------------------------------------------------------
class TestMarketPE:
    def test_it_is_the_screens_own_population_median(self) -> None:
        """The P/E population the quintile cut is taken from: median 13.5."""
        fins, prices = _universe()
        seen: list[MarketPE] = []
        candidates = [
            (fins[t], prices[t] * 100_000_000.0, prices[t]) for t in fins
        ]
        scores = score_candidates(candidates, quintile=0.3, on_market=seen.append)
        assert {s.ticker for s in scores} == {"A", "B", "HELD"}
        assert seen == [MarketPE(peer_count=10, median_pe=13.5)]

    def test_no_positive_pe_reports_no_median(self) -> None:
        fin = _fin("L", eps=-1.0)
        seen: list[MarketPE] = []
        score_candidates([(fin, 1e9, 10.0)], on_market=seen.append)
        assert seen == [MarketPE(peer_count=0, median_pe=None)]

    def test_no_candidates_reports_no_market(self) -> None:
        seen: list[MarketPE] = []
        assert score_candidates([], on_market=seen.append) == []
        assert seen == []


# ---------------------------------------------------------------------------
# The strategy wiring
# ---------------------------------------------------------------------------
class TestStrategyPublishesVerdicts:
    def test_nothing_held_publishes_nothing(self) -> None:
        fins, prices = _universe()
        strategy = _strategy()
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []

    def test_weights_do_not_depend_on_what_is_held(self) -> None:
        """Shadow: the verdicts are journaled, the target list is not
        touched. A held name the doctrine keeps is not added back, and
        one it sells is not removed."""
        fins, prices = _universe()
        held = {"HELD": _held("HELD"), "G": _held("G"), "A": _held("A")}
        bare = _strategy()
        holding = _strategy()
        w_bare = bare.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        w_held = holding.select(
            AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
        )
        assert w_held == w_bare
        verdicts = {v.ticker: v.sell for v in holding.last_exit_decisions}
        assert verdicts == {"A": False, "G": True, "HELD": False}

    def test_verdicts_are_published_when_nothing_qualifies(self) -> None:
        """An empty screen is when a held name's verdict matters most.
        Every name losing money: nothing passes the quality gates, so no
        market — but a broken balance sheet is still a sale."""
        fins = {
            "X": _fin("X", eps=-0.5, net_income=-50e6, current_liabilities=4e9),
        }
        strategy = _strategy()
        weights = strategy.select(
            AS_OF,
            ["X"],
            _lookup({"X": 5.0}),
            _lookup(fins),
            held={"X": _held("X")},
        )
        assert weights == {}
        assert [(v.ticker, v.sell) for v in strategy.last_exit_decisions] == [
            ("X", True)
        ]

    def test_a_held_name_outside_the_universe_is_judged_from_its_lookup(
        self,
    ) -> None:
        fins, prices = _universe()
        strategy = _strategy()
        strategy.select(
            AS_OF,
            [t for t in fins if t != "HELD"],
            _lookup(prices),
            _lookup(fins),
            held={"HELD": _held("HELD")},
        )
        assert [(v.ticker, v.sell) for v in strategy.last_exit_decisions] == [
            ("HELD", False)
        ]

    def test_the_hard_stop_reads_the_strategys_own_leverage_ceiling(self) -> None:
        """D/E 0.125 breaks a 0.1 ceiling set on the strategy."""
        fins, prices = _universe()
        strategy = _strategy(max_de=0.1)
        strategy.select(
            AS_OF,
            [],
            _lookup(prices),
            _lookup(fins),
            held={"HELD": _held("HELD")},
        )
        assert strategy.last_exit_decisions == [
            ExitVerdict(
                "HELD",
                sell=True,
                reason=(
                    "fundamental deterioration (hard stop): D/E 0.12 above "
                    "the 0.1 maximum"
                ),
            )
        ]

    def test_the_previous_days_verdicts_do_not_survive_a_new_select(
        self,
    ) -> None:
        fins, prices = _universe()
        strategy = _strategy()
        strategy.select(
            AS_OF,
            list(fins),
            _lookup(prices),
            _lookup(fins),
            held={"G": _held("G")},
        )
        assert strategy.last_exit_decisions
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []
