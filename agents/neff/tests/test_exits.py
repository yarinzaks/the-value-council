"""Neff's own exit rules, as shadow verdicts (audit SF-X1, item 13g).

Playbook §7: Neff sold when the framework no longer applied — trim as
the P/E climbs to 80% of the market's, out by 100% of it; sell when
Total-Return/PE falls below the market's; sell at any price when the
fundamentals break. §6.2 expects a holding to need two to four years.
None of it ran: ``JohnNeff.select`` ignored ``held`` and the engine
sold a Neff name only for leaving the day's target list (53 such sales
since 2026-08-05, median hold 33 days). These tests pin the rules as
:class:`~core.live.doctrine_exits.ExitVerdict` objects published on
``last_exit_decisions``:

* trigger 3, first and at any price — revenue in the latest 10-K below
  the year before;
* trigger 1 — P/E at or past 100% of the market's;
* trigger 2 — Total-Return/PE below 1x the market's;
* retention — anything else is kept, including the 80%-100% trim zone
  (the trim is not a keep-or-sell verdict), a name that slipped off
  today's list, and a holding of any age.

And in shadow — Neff's mode — none of this changes a weight.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from agents.neff import exits as neff_exits
from agents.neff.filters import total_return_to_pe
from agents.neff.ranking import MarketBenchmarks, score_candidates
from agents.neff.total_return import JohnNeff
from core.backtest.decision_logger import DecisionLogger
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.backtest.strategy_runner import HeldPosition
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact
from core.live.agent_adapter import NeffLive
from core.live.doctrine_exits import ExitVerdict
from core.live.portfolio import LivePortfolio, Position
from core.live.runner import DailyRunner

AS_OF = date(2024, 6, 30)

#: 1.0 in FY2019 to 1.12**4 in FY2023: a 12.0% four-year EPS CAGR, the
#: growth the screen reads on AS_OF (its "then" is 2020-06-30).
EPS_NOW = 1.12**4

#: A market whose median P/E is 10 and median Total-Return/PE 1.5.
MARKET = MarketBenchmarks(peer_count=40, median_pe=10.0, median_tr_pe=1.5)


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
    dividends: float | None = -40_000_000.0,
    operating_cash_flow: float | None = 300_000_000.0,
    net_income: float = 200_000_000.0,
) -> PointInTimeFinancials:
    """$2.00 EPS on 100M shares, $40M of dividends, D/E 0.125."""
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
        total_equity=800_000_000.0,
        dividends_paid=dividends,
        operating_cash_flow=operating_cash_flow,
        total_debt=100_000_000.0,
        long_term_debt=100_000_000.0,
        net_income=net_income,
        shares_outstanding=100_000_000.0,
    )


def _fact(concept: str, value: float, fy: int, ticker: str) -> XbrlFact:
    per_share = concept.startswith("EarningsPerShare")
    return XbrlFact(
        concept=concept,
        namespace="us-gaap",
        unit="USD/shares" if per_share else "USD",
        value=value,
        period_start=date(fy, 1, 1),
        period_end=date(fy, 12, 31),
        filed=date(fy + 1, 2, 15),
        form="10-K",
        fiscal_year=fy,
        fiscal_period="FY",
        accession_number=f"{ticker}-{fy}",
    )


def _facts(
    ticker: str,
    *,
    revenue: Mapping[int, float] | None = None,
    eps: Mapping[int, float] | None = None,
) -> list[XbrlFact]:
    """12% EPS and revenue growth FY2019 -> FY2023 by default, with
    FY2022 revenue $1.4bn so the latest year grew too."""
    revenue = (
        {2019: 1.0e9, 2022: 1.4e9, 2023: 1.0e9 * EPS_NOW}
        if revenue is None
        else revenue
    )
    eps = {2019: 1.0, 2023: EPS_NOW} if eps is None else eps
    return [
        *(_fact("Revenues", v, fy, ticker) for fy, v in revenue.items()),
        *(_fact("EarningsPerShareDiluted", v, fy, ticker) for fy, v in eps.items()),
    ]


def _cache(tmp_path: Path, **per_ticker: list[XbrlFact]) -> EdgarCache:
    cache = EdgarCache(cache_dir=tmp_path / "edgar")
    for ticker, facts in per_ticker.items():
        cache.save_facts(ticker, facts)
    return cache


def _held(ticker: str, *, entry: float = 15.0, days: int = 120) -> HeldPosition:
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
    market: MarketBenchmarks | None = MARKET,
    days: int = 120,
) -> ExitVerdict | None:
    ticker = fin.ticker if fin is not None else "X"
    market_cap = (
        price * fin.shares_outstanding
        if fin is not None and price is not None and fin.shares_outstanding
        else None
    )
    return neff_exits.decide(
        _held(ticker, days=days),
        fin,
        price,
        market_cap,
        market,
        cache=cache,
        as_of=AS_OF,
    )


@pytest.fixture(autouse=True)
def _one_market(monkeypatch: pytest.MonkeyPatch) -> None:
    """No synthetic ticker has an industry, so the screen measures every
    one against the universe: a test ticker can be a real one the SIC
    table files in a crowded industry."""
    monkeypatch.setattr("agents.neff.ranking.industry_for", lambda _t: None)


#: A day's screen: three cheap payers (A, B, HELD) and three names at
#: P/E 15-17 that set the market median (10.75) and fail the hard gate.
UNIVERSE_PRICES = {
    "A": 10.0,
    "B": 11.0,
    "HELD": 13.0,
    "D": 30.0,
    "E": 32.0,
    "F": 34.0,
}


def _universe(
    tmp_path: Path,
) -> tuple[EdgarCache, dict[str, PointInTimeFinancials], dict[str, float]]:
    cache = _cache(tmp_path, **{t: _facts(t) for t in UNIVERSE_PRICES})
    fins = {t: _fin(t) for t in UNIVERSE_PRICES}
    return cache, fins, dict(UNIVERSE_PRICES)


# ---------------------------------------------------------------------------
# The two tests the audit names (report section 6, item 13)
# ---------------------------------------------------------------------------
def test_held_name_that_still_qualifies_is_retained_when_it_slips_in_rank(
    tmp_path: Path,
) -> None:
    """A, B and HELD all pass the screen; the strategy keeps room for two.

    HELD's P/E of 6.50 is the highest of the three, so it ranks third
    and is off the target list. At 60.5% of the market's 10.75 it is
    nowhere near the 80% line where Neff starts to trim, and its
    Total-Return/PE (12% growth + 3.1% yield over 6.5 = 2.320) is above
    the market's 1.604: keep.
    """
    cache, fins, prices = _universe(tmp_path)
    strategy = JohnNeff(edgar_cache=cache, portfolio_size=2, min_market_cap=100)
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
                "framework still applies: P/E 6.50 is 60.5% of the market's "
                "10.75 (median of the day's screen), under the 80% line "
                "where trimming begins; Total-Return/PE 2.320 against the "
                "market's 1.604; held 200 days of an expected 2-4 years"
            ),
        )
    ]


def test_exit_reason_in_journal_names_the_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real adapter, the real runner, shadow mode.

    D trades at a P/E of 15.00 against the market's 10.75 — 139.5%,
    past the 100% line. The doctrine journal says SELL and names the
    trigger; the engine's own action is recorded beside it and —
    shadow — is whatever it would have been.
    """
    cache, fins, prices_ = _universe(tmp_path)
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
    adapter = NeffLive(JohnNeff(edgar_cache=cache, min_market_cap=100))
    assert adapter.exit_mode == "shadow"

    book = LivePortfolio(agent=adapter.name, cash=2_000.0)
    book.last_rebalance_date = "2024-06-27"
    book.positions.append(
        Position(
            ticker="D",
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
    assert [r["ticker"] for r in rows] == ["D"]
    row = rows[0]
    assert row["decision"] == "SELL"
    assert row["exit_trigger"] == (
        "P/E expanded to the market average: P/E 15.00 is 139.5% of the "
        "market's 10.75 (median of the day's screen), at or past the 100% "
        "line where the exit is complete"
    )
    assert "market average" in row["criteria_met"][0]
    assert row["market_conditions"]["exit_mode"] == "shadow"


# ---------------------------------------------------------------------------
# Trigger 1 — P/E expanded toward the market's
# ---------------------------------------------------------------------------
class TestMultipleExpansion:
    def test_under_eighty_percent_of_the_market_is_a_plain_keep(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _fin("X"), 15.98)  # P/E 7.99
        assert v is not None and v.sell is False
        assert v.reason.startswith(
            "framework still applies: P/E 7.99 is 79.9% of the market's 10.00"
        )

    def test_at_eighty_percent_trimming_begins_and_the_rest_is_kept(
        self, tmp_path: Path
    ) -> None:
        """§7.1: "Begin trimming at 80% of market average". A verdict is
        keep or sell, so the trim is named, not done."""
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
                "position is kept; Total-Return/PE 1.813 against the "
                "market's 1.500; held 120 days of an expected 2-4 years"
            ),
        )

    def test_just_under_the_market_multiple_is_still_kept(
        self, tmp_path: Path
    ) -> None:
        """Trigger 1 alone: the market's TR/PE (1.0) is set under this
        name's 1.402 so trigger 2 stays out of it."""
        cache = _cache(tmp_path, X=_facts("X"))
        market = MarketBenchmarks(peer_count=40, median_pe=10.0, median_tr_pe=1.0)
        v = _decide(cache, _fin("X"), 19.98, market=market)  # P/E 9.99
        assert v is not None and v.sell is False
        assert v.reason.startswith("trim zone: P/E 9.99 is 99.9%")

    def test_at_the_market_multiple_the_exit_is_complete(
        self, tmp_path: Path
    ) -> None:
        """§7.1: "Full exit by 100% of market average" — at the line,
        not only past it, and whatever the growth and dividend: "the
        multiple expansion alone is the win"."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _fin("X"), 20.0)  # P/E 10.00
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "P/E expanded to the market average: P/E 10.00 is 100.0% of "
                "the market's 10.00 (median of the day's screen), at or past "
                "the 100% line where the exit is complete"
            ),
        )

    def test_a_price_decline_of_any_size_is_never_a_sell(
        self, tmp_path: Path
    ) -> None:
        """§7.2: a generic downturn "creates more buying opportunities".
        Down 80% from a $25 cost, the multiple is only cheaper: keep."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _fin("X"), 5.0)
        assert v is not None and v.sell is False
        assert v.reason.startswith("framework still applies: P/E 2.50 is 25.0%")


# ---------------------------------------------------------------------------
# The 2-4 year holding expectation is not a trigger in either direction
# ---------------------------------------------------------------------------
class TestHoldingPeriod:
    def test_a_name_held_past_four_years_is_still_kept_while_cheap(
        self, tmp_path: Path
    ) -> None:
        """§6.2's 2-4 years is what the multiple is expected to need, not
        a deadline: a cheap name at five years is kept."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _fin("X"), 15.0, days=1826)
        assert v is not None and v.sell is False
        assert v.reason.endswith("held 1826 days of an expected 2-4 years")

    def test_a_short_hold_is_no_shield_once_the_multiple_is_spent(
        self, tmp_path: Path
    ) -> None:
        """The doctrine has no minimum hold; the engine's 30-day floor is
        the engine's, applied separately."""
        cache = _cache(tmp_path, X=_facts("X"))
        v = _decide(cache, _fin("X"), 22.0, days=5)
        assert v is not None and v.sell is True


# ---------------------------------------------------------------------------
# Trigger 2 — Total-Return/PE below the market's
# ---------------------------------------------------------------------------
class TestSignatureMetric:
    def test_below_the_markets_total_return_pe_sells(self, tmp_path: Path) -> None:
        """At P/E 7.50 with 12% growth and a 2.7% yield, TR/PE is 1.956;
        against a market at 2.5 the signature metric has fallen out of
        the qualifying range."""
        cache = _cache(tmp_path, X=_facts("X"))
        market = MarketBenchmarks(peer_count=40, median_pe=10.0, median_tr_pe=2.5)
        v = _decide(cache, _fin("X"), 15.0, market=market)
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "Total-Return/PE 1.956 fell below 1x the market's 2.500 "
                "(EPS growth 12.0% + yield 2.7%, over P/E 7.50)"
            ),
        )

    def test_a_dividend_cut_to_nothing_can_trip_it(self, tmp_path: Path) -> None:
        """§7.1 trigger 2's first cause: "the dividend has been cut". The
        filer reports cash flow and no dividend line, so the yield is a
        measured zero: TR/PE 12/7.5 = 1.600, under a market at 1.7."""
        cache = _cache(tmp_path, X=_facts("X"))
        market = MarketBenchmarks(peer_count=40, median_pe=10.0, median_tr_pe=1.7)
        paying = _decide(cache, _fin("X"), 15.0, market=market)
        cut = _decide(cache, _fin("X", dividends=None), 15.0, market=market)
        assert paying is not None and paying.sell is False
        assert cut is not None and cut.sell is True
        assert cut.reason.startswith("Total-Return/PE 1.600 fell below")

    def test_exactly_the_markets_total_return_pe_is_kept(
        self, tmp_path: Path
    ) -> None:
        """"Below 1x" — at 1x the metric is still in range."""
        cache = _cache(tmp_path, X=_facts("X"))
        own = total_return_to_pe(12.0, 40e6 / 1.5e9 * 100.0, 7.5)
        assert own is not None
        market = MarketBenchmarks(peer_count=40, median_pe=10.0, median_tr_pe=own)
        v = _decide(cache, _fin("X"), 15.0, market=market)
        assert v is not None and v.sell is False

    def test_unmeasurable_growth_is_judged_on_the_multiple_alone(
        self, tmp_path: Path
    ) -> None:
        """No four-year EPS history: TR/PE is unknown, never a breach."""
        cache = _cache(tmp_path, X=_facts("X", eps={2023: 2.0}))
        market = MarketBenchmarks(peer_count=40, median_pe=10.0, median_tr_pe=99.0)
        v = _decide(cache, _fin("X"), 15.0, market=market)
        assert v is not None and v.sell is False
        assert "; Total-Return/PE not measurable; " in v.reason


# ---------------------------------------------------------------------------
# Trigger 3 — fundamental deterioration, at any price
# ---------------------------------------------------------------------------
class TestDeterioration:
    def test_sales_turning_negative_sells_even_deep_below_the_market(
        self, tmp_path: Path
    ) -> None:
        cache = _cache(
            tmp_path,
            X=_facts("X", revenue={2019: 1.0e9, 2022: 1.4e9, 2023: 1.3e9}),
        )
        v = _decide(cache, _fin("X"), 5.0)  # P/E 2.50
        assert v == ExitVerdict(
            "X",
            sell=True,
            reason=(
                "fundamental deterioration: sales growth turned negative, "
                "revenue $1,300,000,000 in the year to 2023-12-31 against "
                "$1,400,000,000 the year before (-7.1%)"
            ),
        )

    def test_it_fires_even_when_earnings_are_gone(self, tmp_path: Path) -> None:
        """No P/E to judge, but a deterioration is a sale at any price."""
        cache = _cache(
            tmp_path,
            X=_facts("X", revenue={2022: 1.4e9, 2023: 1.3e9}),
        )
        v = _decide(cache, _fin("X", eps=-0.5, net_income=-50e6), 5.0)
        assert v is not None and v.sell is True

    def test_flat_sales_are_not_a_deterioration(self, tmp_path: Path) -> None:
        cache = _cache(
            tmp_path,
            X=_facts("X", revenue={2019: 1.0e9, 2022: 1.4e9, 2023: 1.4e9}),
        )
        v = _decide(cache, _fin("X"), 15.0)
        assert v is not None and v.sell is False

    def test_a_skipped_year_is_not_a_turn(self, tmp_path: Path) -> None:
        """FY2021 against FY2023 is not "turning negative"; unknown."""
        cache = _cache(
            tmp_path,
            X=_facts("X", revenue={2019: 1.0e9, 2021: 1.6e9, 2023: 1.5e9}),
        )
        assert neff_exits.latest_sales_change(cache, "X", AS_OF) is None
        v = _decide(cache, _fin("X"), 15.0)
        assert v is not None and v.sell is False

    def test_a_10k_not_yet_filed_is_not_read(self, tmp_path: Path) -> None:
        """Point in time: on 2024-01-31 the FY2023 10-K (filed 2024-02-15)
        is not known, so FY2022 against FY2021 is the latest year."""
        cache = _cache(
            tmp_path,
            X=_facts("X", revenue={2021: 1.2e9, 2022: 1.4e9, 2023: 1.3e9}),
        )
        early = neff_exits.latest_sales_change(cache, "X", date(2024, 1, 31))
        late = neff_exits.latest_sales_change(cache, "X", AS_OF)
        assert early is not None and early.latest.value == 1.4e9
        assert late is not None and late.latest.value == 1.3e9

    def test_a_loss_year_alone_gives_no_verdict(self, tmp_path: Path) -> None:
        """"Earnings declining for 2+ quarters" needs quarterly EPS the
        cache does not carry; a negative trailing EPS leaves the P/E
        undefined, and with sales still growing nothing is known."""
        cache = _cache(tmp_path, X=_facts("X"))
        assert _decide(cache, _fin("X", eps=-0.5, net_income=-50e6), 5.0) is None


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
        market = MarketBenchmarks(peer_count=0, median_pe=None, median_tr_pe=None)
        assert _decide(cache, _fin("X"), 15.0, market=market) is None


# ---------------------------------------------------------------------------
# The market the verdicts are measured against
# ---------------------------------------------------------------------------
class TestMarketBenchmarks:
    def test_it_is_the_screens_own_universe_median(self, tmp_path: Path) -> None:
        """P/Es 5, 5.5, 6.5, 15, 16, 17: the median is 10.75 — the same
        number the screen's hard gate rejects at."""
        cache, fins, prices = _universe(tmp_path)
        seen: list[MarketBenchmarks] = []
        candidates = [
            (fins[t], prices[t] * 100_000_000.0, prices[t]) for t in fins
        ]
        scores = score_candidates(
            candidates, as_of=AS_OF, edgar_cache=cache, on_market=seen.append
        )
        assert {s.ticker for s in scores} == {"A", "B", "HELD"}
        assert len(seen) == 1
        assert seen[0].peer_count == 6
        assert seen[0].median_pe == pytest.approx(10.75)
        # The middle two of six: D's (P/E 15) and HELD's (P/E 6.5).
        d_tr_pe = total_return_to_pe(12.0, 40e6 / 3.0e9 * 100.0, 15.0)
        held_tr_pe = total_return_to_pe(12.0, 40e6 / 1.3e9 * 100.0, 6.5)
        assert d_tr_pe is not None and held_tr_pe is not None
        assert seen[0].median_tr_pe == pytest.approx((d_tr_pe + held_tr_pe) / 2.0)

    def test_no_candidates_reports_no_market(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        seen: list[MarketBenchmarks] = []
        assert score_candidates([], as_of=AS_OF, edgar_cache=cache, on_market=seen.append) == []
        assert seen == []


# ---------------------------------------------------------------------------
# The strategy wiring
# ---------------------------------------------------------------------------
class TestStrategyPublishesVerdicts:
    def test_nothing_held_publishes_nothing(self, tmp_path: Path) -> None:
        cache, fins, prices = _universe(tmp_path)
        strategy = JohnNeff(edgar_cache=cache, min_market_cap=100)
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []

    def test_weights_do_not_depend_on_what_is_held(self, tmp_path: Path) -> None:
        """Shadow: the verdicts are journaled, the target list is not
        touched. A held name the doctrine keeps is not added back, and
        one it sells is not removed."""
        cache, fins, prices = _universe(tmp_path)
        held = {"HELD": _held("HELD"), "D": _held("D")}
        bare = JohnNeff(edgar_cache=cache, portfolio_size=2, min_market_cap=100)
        holding = JohnNeff(edgar_cache=cache, portfolio_size=2, min_market_cap=100)
        w_bare = bare.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        w_held = holding.select(
            AS_OF, list(fins), _lookup(prices), _lookup(fins), held=held
        )
        assert w_held == w_bare
        verdicts = {v.ticker: v.sell for v in holding.last_exit_decisions}
        assert verdicts == {"D": True, "HELD": False}

    def test_verdicts_are_published_when_nothing_qualifies(
        self, tmp_path: Path
    ) -> None:
        """An empty screen is when a held name's verdict matters most.
        Only D, E and F: none is under the market multiple."""
        cache, fins, prices = _universe(tmp_path)
        strategy = JohnNeff(edgar_cache=cache, min_market_cap=100)
        weights = strategy.select(
            AS_OF,
            ["D", "E", "F"],
            _lookup(prices),
            _lookup(fins),
            held={"F": _held("F")},
        )
        assert weights == {}
        assert [(v.ticker, v.sell) for v in strategy.last_exit_decisions] == [
            ("F", True)
        ]

    def test_a_held_name_outside_the_universe_is_judged_from_its_lookup(
        self, tmp_path: Path
    ) -> None:
        cache, fins, prices = _universe(tmp_path)
        strategy = JohnNeff(edgar_cache=cache, min_market_cap=100)
        strategy.select(
            AS_OF,
            ["A", "B", "D", "E", "F"],
            _lookup(prices),
            _lookup(fins),
            held={"HELD": _held("HELD")},
        )
        assert [(v.ticker, v.sell) for v in strategy.last_exit_decisions] == [
            ("HELD", False)
        ]

    def test_an_empty_universe_still_sells_a_deterioration(
        self, tmp_path: Path
    ) -> None:
        """No market today, so no P/E verdict — but falling sales are a
        sale at any price."""
        cache = _cache(
            tmp_path, X=_facts("X", revenue={2022: 1.4e9, 2023: 1.3e9})
        )
        strategy = JohnNeff(edgar_cache=cache, min_market_cap=100)
        strategy.select(
            AS_OF,
            [],
            _lookup({"X": 15.0}),
            _lookup({"X": _fin("X")}),
            held={"X": _held("X")},
        )
        assert [(v.ticker, v.sell) for v in strategy.last_exit_decisions] == [
            ("X", True)
        ]

    def test_the_previous_days_verdicts_do_not_survive_a_new_select(
        self, tmp_path: Path
    ) -> None:
        cache, fins, prices = _universe(tmp_path)
        strategy = JohnNeff(edgar_cache=cache, min_market_cap=100)
        strategy.select(
            AS_OF,
            list(fins),
            _lookup(prices),
            _lookup(fins),
            held={"D": _held("D")},
        )
        assert strategy.last_exit_decisions
        strategy.select(AS_OF, list(fins), _lookup(prices), _lookup(fins))
        assert strategy.last_exit_decisions == []
