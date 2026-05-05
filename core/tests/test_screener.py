"""Unit tests for the screener engine."""

from __future__ import annotations

import pytest

from core.data.models import Fundamentals, Quote, StockSnapshot
from core.screener import Filter, ScreenerEngine


def _snapshot(ticker: str, *, pe: float | None = None, price: float | None = None) -> StockSnapshot:
    return StockSnapshot(
        ticker=ticker,
        quote=Quote(ticker=ticker, price=price) if price is not None else None,
        fundamentals=(
            Fundamentals(ticker=ticker, pe_ratio=pe) if pe is not None else None
        ),
    )


class TestFilter:
    def test_unknown_op_raises(self) -> None:
        with pytest.raises(ValueError):
            Filter("fundamentals.pe_ratio", "approximately", 10)  # type: ignore[arg-type]

    def test_between_requires_tuple(self) -> None:
        with pytest.raises(ValueError):
            Filter("fundamentals.pe_ratio", "between", 5)

    def test_between_orders_bounds(self) -> None:
        with pytest.raises(ValueError):
            Filter("fundamentals.pe_ratio", "between", (10, 5))


class TestScreener:
    def test_le_filter(self) -> None:
        snaps = [
            _snapshot("CHEAP", pe=10.0),
            _snapshot("EXPENSIVE", pe=40.0),
        ]
        engine = ScreenerEngine()
        result = engine.screen(snaps, [Filter("fundamentals.pe_ratio", "<=", 15)])
        assert [s.ticker for s in result] == ["CHEAP"]

    def test_missing_field_fails_filter(self) -> None:
        snap = _snapshot("NODATA")  # no fundamentals at all
        engine = ScreenerEngine()
        assert not engine.apply(snap, [Filter("fundamentals.pe_ratio", "<=", 15)])

    def test_between(self) -> None:
        snaps = [_snapshot("A", pe=8), _snapshot("B", pe=12), _snapshot("C", pe=20)]
        engine = ScreenerEngine()
        result = engine.screen(snaps, [Filter("fundamentals.pe_ratio", "between", (10, 15))])
        assert [s.ticker for s in result] == ["B"]

    def test_multiple_filters_are_anded(self) -> None:
        snaps = [
            _snapshot("OK", pe=10, price=100),
            _snapshot("CHEAP_BUT_PENNY", pe=5, price=0.5),
            _snapshot("EXPENSIVE", pe=40, price=100),
        ]
        engine = ScreenerEngine()
        result = engine.screen(
            snaps,
            [
                Filter("fundamentals.pe_ratio", "<=", 15),
                Filter("quote.price", ">=", 5.0),
            ],
        )
        assert [s.ticker for s in result] == ["OK"]

    def test_in_op(self) -> None:
        snaps = [_snapshot("AAPL", pe=10), _snapshot("MSFT", pe=10), _snapshot("GOOG", pe=10)]
        engine = ScreenerEngine()
        result = engine.screen(snaps, [Filter("ticker", "in", ["AAPL", "MSFT"])])
        assert {s.ticker for s in result} == {"AAPL", "MSFT"}
