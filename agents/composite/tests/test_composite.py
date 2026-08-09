"""Tests for the selection itself.

The scoring maths is covered next door. What matters here is what the
strategy does with a candidate it cannot fully measure, because that is
where a screen quietly starts rewarding companies with sparse filings.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest

from agents.composite.composite import FactorComposite
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials

AS_OF = date(2024, 12, 31)


def _fin(ticker: str, **kw: float | None) -> PointInTimeFinancials:
    base: dict[str, float | None] = {
        "operating_income": 150.0,
        "total_assets": 1_000.0,
        "total_debt": 200.0,
        "cash_and_equivalents": 50.0,
        "shares_outstanding": 100.0,
    }
    base.update(kw)
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=AS_OF,
        source_filing=FilingMetadata(
            ticker=ticker,
            cik=None,
            form_type="10-K",
            filing_date=date(2024, 3, 1),
            period_of_report=date(2023, 12, 31),
            accession_number="0000000000-00-000000",
        ),
        **base,  # type: ignore[arg-type]
    )


class _Prices:
    def __init__(self, prices: dict[str, float | None]) -> None:
        self._p = prices

    def get(self, ticker: str) -> float | None:
        return self._p.get(ticker)


class _Fundamentals:
    def __init__(self, fins: dict[str, PointInTimeFinancials | None]) -> None:
        self._f = fins

    def get(self, ticker: str) -> PointInTimeFinancials | None:
        return self._f.get(ticker)


class _Momentum:
    """Stands in for PriceDataLoader.trailing_return."""

    def __init__(self, momentum: Mapping[str, float | None]) -> None:
        self._m = momentum
        self.asked: list[str] = []

    def trailing_return(
        self, ticker: str, as_of: date, *, lookback_months: int, skip_months: int
    ) -> float | None:
        self.asked.append(ticker)
        return self._m.get(ticker)


def _strategy(momentum: Mapping[str, float | None], **kw: object) -> FactorComposite:
    """The fixture company is worth $1,000, so the floor defaults out of
    the way; TestMarketCapFloor sets its own."""
    kw.setdefault("min_market_cap", 1.0)
    return FactorComposite(
        price_loader=_Momentum(momentum),  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


class TestFailClosed:
    """A name that cannot be measured on all three legs is dropped.

    Averaging whatever happens to be present rewards sparse filers, and
    imputing a median invents a measurement.
    """

    def test_a_name_with_no_momentum_is_dropped(self) -> None:
        s = _strategy({"A": 10.0, "B": None})

        w = s.select(
            AS_OF,
            ["A", "B"],
            _Prices({"A": 10.0, "B": 10.0}),  # type: ignore[arg-type]
            _Fundamentals({"A": _fin("A"), "B": _fin("B")}),  # type: ignore[arg-type]
        )

        assert set(w) == {"A"}

    def test_a_name_with_no_filing_is_dropped(self) -> None:
        s = _strategy({"A": 10.0, "B": 10.0})

        w = s.select(
            AS_OF,
            ["A", "B"],
            _Prices({"A": 10.0, "B": 10.0}),  # type: ignore[arg-type]
            _Fundamentals({"A": _fin("A"), "B": None}),  # type: ignore[arg-type]
        )

        assert set(w) == {"A"}

    def test_a_name_with_no_price_is_dropped(self) -> None:
        s = _strategy({"A": 10.0, "B": 10.0})

        w = s.select(
            AS_OF,
            ["A", "B"],
            _Prices({"A": 10.0, "B": None}),  # type: ignore[arg-type]
            _Fundamentals({"A": _fin("A"), "B": _fin("B")}),  # type: ignore[arg-type]
        )

        assert set(w) == {"A"}

    def test_a_name_with_unusable_enterprise_value_is_dropped(self) -> None:
        # Net cash above the equity value flips the yield's sign.
        s = _strategy({"A": 10.0, "B": 10.0})

        w = s.select(
            AS_OF,
            ["A", "B"],
            _Prices({"A": 10.0, "B": 10.0}),  # type: ignore[arg-type]
            _Fundamentals(  # type: ignore[arg-type]
                {"A": _fin("A"), "B": _fin("B", cash_and_equivalents=99_999.0)}
            ),
        )

        assert set(w) == {"A"}

    def test_momentum_is_never_asked_for_an_already_dropped_name(self) -> None:
        # The lookup is cache-only but not free, and it runs across the
        # whole universe at every rebalance.
        loader = _Momentum({"A": 10.0, "B": 10.0})
        s = FactorComposite(
            price_loader=loader,  # type: ignore[arg-type]
            min_market_cap=1.0,
        )

        s.select(
            AS_OF,
            ["A", "B"],
            _Prices({"A": 10.0, "B": None}),  # type: ignore[arg-type]
            _Fundamentals({"A": _fin("A"), "B": _fin("B")}),  # type: ignore[arg-type]
        )

        assert loader.asked == ["A"]


class TestMarketCapFloor:
    def test_a_name_below_the_floor_is_dropped(self) -> None:
        # 100 shares at $10 is a $1,000 company.
        s = _strategy({"A": 10.0}, min_market_cap=1_000_000.0)

        w = s.select(
            AS_OF,
            ["A"],
            _Prices({"A": 10.0}),  # type: ignore[arg-type]
            _Fundamentals({"A": _fin("A")}),  # type: ignore[arg-type]
        )

        assert w == {}

    def test_shares_outstanding_is_required(self) -> None:
        s = _strategy({"A": 10.0}, min_market_cap=1.0)

        w = s.select(
            AS_OF,
            ["A"],
            _Prices({"A": 10.0}),  # type: ignore[arg-type]
            _Fundamentals({"A": _fin("A", shares_outstanding=None)}),  # type: ignore[arg-type]
        )

        assert w == {}


class TestSelection:
    @staticmethod
    def _field(
        n: int,
    ) -> tuple[list[str], dict[str, float], dict[str, PointInTimeFinancials | None]]:
        tickers = [f"T{i:02d}" for i in range(n)]
        momentum = {t: float(i) for i, t in enumerate(tickers)}
        fins: dict[str, PointInTimeFinancials | None] = {
            t: _fin(t, operating_income=100.0 + i, total_assets=1_000.0)
            for i, t in enumerate(tickers)
        }
        return tickers, momentum, fins

    def test_it_holds_exactly_the_portfolio_size(self) -> None:
        tickers, momentum, fins = self._field(40)
        s = _strategy(momentum, portfolio_size=25, min_market_cap=1.0)

        w = s.select(
            AS_OF,
            tickers,
            _Prices(dict.fromkeys(tickers, 10.0)),  # type: ignore[arg-type]
            _Fundamentals(fins),  # type: ignore[arg-type]
        )

        assert len(w) == 25

    def test_the_weights_are_equal_and_sum_to_one(self) -> None:
        tickers, momentum, fins = self._field(40)
        s = _strategy(momentum, portfolio_size=25, min_market_cap=1.0)

        w = s.select(
            AS_OF,
            tickers,
            _Prices(dict.fromkeys(tickers, 10.0)),  # type: ignore[arg-type]
            _Fundamentals(fins),  # type: ignore[arg-type]
        )

        assert sum(w.values()) == pytest.approx(1.0)
        assert len(set(round(v, 9) for v in w.values())) == 1

    def test_a_thin_field_holds_what_it_has(self) -> None:
        tickers, momentum, fins = self._field(5)
        s = _strategy(momentum, portfolio_size=25, min_market_cap=1.0)

        w = s.select(
            AS_OF,
            tickers,
            _Prices(dict.fromkeys(tickers, 10.0)),  # type: ignore[arg-type]
            _Fundamentals(fins),  # type: ignore[arg-type]
        )

        assert len(w) <= 5
        assert sum(w.values()) == pytest.approx(1.0)

    def test_nothing_measurable_is_a_hold_not_a_liquidation(self) -> None:
        # An empty dict is the runner's "no trade" signal. Returning a
        # partial book because the data failed would sell everything the
        # screen could not see this quarter.
        s = _strategy({})

        w = s.select(
            AS_OF,
            ["A", "B"],
            _Prices({}),  # type: ignore[arg-type]
            _Fundamentals({}),  # type: ignore[arg-type]
        )

        assert w == {}

    def test_the_picks_record_the_arithmetic(self) -> None:
        tickers, momentum, fins = self._field(30)
        s = _strategy(momentum, portfolio_size=5, min_market_cap=1.0)

        s.select(
            AS_OF,
            tickers,
            _Prices(dict.fromkeys(tickers, 10.0)),  # type: ignore[arg-type]
            _Fundamentals(fins),  # type: ignore[arg-type]
        )

        assert len(s.last_picks) == 5
        pick = s.last_picks[0]
        assert "EBIT/EV" in pick.why_en
        assert "momentum" in pick.why_en
        assert "מומנטום" in pick.why_he


class TestConstruction:
    def test_a_zero_portfolio_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            _strategy({}, portfolio_size=0)

    def test_a_zero_market_cap_floor_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            _strategy({}, min_market_cap=0.0)
