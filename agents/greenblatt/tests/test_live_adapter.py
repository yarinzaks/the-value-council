"""The live adapter keeps every holding the one-year rule protects.

Audit item 17. ``MagicFormula.select`` already gives an immature holding
its slot even when the name no longer scores — it dropped out of the
filtered universe, its filing went missing, its price did. The live
adapter then built its targets from ``top_scores`` alone, so that slot
vanished on the way to the runner and the runner sold the name as having
"left today's target list": CRMD, 2026-10-30, 177 days into a 365-day
hold.

Audit RK-08 is the same path taken to its end. On a day where nothing
scores and every holding is still protected, ``select`` reached for
``top[0]`` to log the best rank and raised ``list index out of range``,
which cost Greenblatt the whole run.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta

from agents.greenblatt.magic_formula import MagicFormula
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
)
from core.live.agent_adapter import GreenblattLive, ScanResult

AS_OF = date(2026, 10, 30)


class _Prices(PriceLookup):
    # The real lookups are concrete classes over loaders; these stand in
    # for them over a dict, without a loader behind them.
    def __init__(self, data: Mapping[str, float]) -> None:
        self._d = dict(data)

    def get(self, ticker: str) -> float | None:
        return self._d.get(ticker)


class _Fundamentals(FundamentalsLookup):
    def __init__(self, data: Mapping[str, PointInTimeFinancials]) -> None:
        self._d = dict(data)

    def get(self, ticker: str) -> PointInTimeFinancials | None:
        return self._d.get(ticker)


def _fin(ticker: str, operating_income: float) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=AS_OF,
        source_filing=FilingMetadata(
            ticker=ticker,
            cik="1",
            form_type="10-K",
            filing_date=date(2026, 2, 15),
            period_of_report=date(2025, 12, 31),
            accession_number=f"a-{ticker}",
        ),
        revenue=1_000_000_000.0,
        net_income=100_000_000.0,
        eps_diluted=2.0,
        eps_basic=2.0,
        operating_income=operating_income,
        operating_cash_flow=100_000_000.0,
        total_equity=800_000_000.0,
        current_assets=500_000_000.0,
        current_liabilities=200_000_000.0,
        total_debt=100_000_000.0,
        long_term_debt=100_000_000.0,
        total_assets=900_000_000.0,
        cash_and_equivalents=50_000_000.0,
        ppe_net=200_000_000.0,
        shares_outstanding=100_000_000.0,
        dividends_paid=-10_000_000.0,
        total_liabilities=300_000_000.0,
    )


def _held(ticker: str, days: int, price: float = 10.0) -> HeldPosition:
    return HeldPosition(
        ticker=ticker,
        shares=10,
        entry_price=price,
        entry_date=AS_OF - timedelta(days=days),
        current_price=price,
    )


def _scored_universe(n: int) -> tuple[dict[str, PointInTimeFinancials], dict[str, float]]:
    # Same EV, rising EBIT: T{n-1} has the best earnings yield, T0 the worst.
    fins = {f"T{i}": _fin(f"T{i}", 50_000_000.0 + i * 50_000_000.0) for i in range(n)}
    return fins, {t: 10.0 for t in fins}


def _scan(
    *,
    fins: Mapping[str, PointInTimeFinancials],
    prices: Mapping[str, float],
    universe: list[str],
    held: Mapping[str, HeldPosition],
    portfolio_size: int,
) -> ScanResult:
    adapter = GreenblattLive(
        MagicFormula(portfolio_size=portfolio_size, min_market_cap=100),
        watchlist_size=5,
    )
    return adapter.run_scan(
        AS_OF, universe, _Prices(prices), _Fundamentals(fins), held=held
    )


def test_unscored_protected_holding_is_emitted_as_target() -> None:
    # CRMD is held 177 days and has no fundamentals today, so it cannot
    # score. The strategy keeps its slot; the adapter must pass it on.
    fins, prices = _scored_universe(5)
    prices["CRMD"] = 4.0
    result = _scan(
        fins=fins,
        prices=prices,
        universe=[*fins, "CRMD"],
        held={"CRMD": _held("CRMD", 177, price=4.0)},
        portfolio_size=3,
    )

    by_ticker = {t.ticker: t for t in result.targets}
    assert set(by_ticker) == {"CRMD", "T4", "T3"}
    crmd = by_ticker["CRMD"]
    # Its slot is a full slot, not a remainder.
    assert crmd.weight == by_ticker["T4"].weight == 1.0 / 3
    # No score to cite, and the why says why it is still here.
    assert crmd.score is None
    assert "holding period" in crmd.why_en
    assert "תקופת ההחזקה" in crmd.why_he
    # Ranks stay a contiguous 1..N with the scored names first.
    assert sorted(t.rank for t in result.targets) == [1, 2, 3]
    assert crmd.rank == 3
    # A name that is a target is never on the watchlist.
    assert "CRMD" not in {w.ticker for w in result.watchlist}


def test_scan_where_nothing_scores_keeps_every_protected_holding() -> None:
    # RK-08: no fundamentals at all (the 2026-11-12 cache priced 0/3490),
    # every holding inside the year. select() used to raise IndexError.
    held = {t: _held(t, 120 + i) for i, t in enumerate(("AAA", "BBB", "CCC"))}
    result = _scan(
        fins={},
        prices={},
        universe=["AAA", "BBB", "CCC", "DDD"],
        held=held,
        portfolio_size=3,
    )

    assert {t.ticker for t in result.targets} == {"AAA", "BBB", "CCC"}
    assert all(t.score is None for t in result.targets)
    assert sorted(t.rank for t in result.targets) == [1, 2, 3]


def test_matured_unscored_holding_is_not_kept() -> None:
    # Past the anniversary the rule no longer protects it, and with no
    # score it has no claim on a slot: the old behaviour stands.
    fins, prices = _scored_universe(5)
    result = _scan(
        fins=fins,
        prices=prices,
        universe=[*fins, "OLD"],
        held={"OLD": _held("OLD", 400)},
        portfolio_size=3,
    )

    assert {t.ticker for t in result.targets} == {"T4", "T3", "T2"}
    assert all(t.score is not None for t in result.targets)


def test_scored_protected_holding_keeps_its_score_why() -> None:
    # A protected name that still scores is cited by its score, not by
    # the holding-period fallback.
    fins, prices = _scored_universe(5)
    result = _scan(
        fins=fins,
        prices=prices,
        universe=list(fins),
        held={"T0": _held("T0", 30)},
        portfolio_size=3,
    )

    t0 = next(t for t in result.targets if t.ticker == "T0")
    assert t0.score is not None
    assert "Magic Formula combined rank" in t0.why_en
