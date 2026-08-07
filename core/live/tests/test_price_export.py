"""Tests for the per-ticker price export.

The dashboard reads JSON from the data root and has no reader for the
SQLite price cache, so a position page could show entry and current
mark with nothing in between — the shape of the holding was invisible.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.live.portfolio import LivePortfolio, Position
from core.live.price_export import export_prices


class _StubLoader:
    """Serves a fixed frame, and records what it was asked for."""

    def __init__(self, series: dict[str, list[tuple[str, float]]]) -> None:
        self._series = series
        self.asked: list[str] = []

    def get_history(
        self, ticker: str, *, start: date, end: date
    ) -> pd.DataFrame:
        self.asked.append(ticker)
        rows = self._series.get(ticker)
        if rows is None:
            return pd.DataFrame()
        return pd.DataFrame(
            {"adj_close": [c for _, c in rows]},
            index=pd.DatetimeIndex([d for d, _ in rows]),
        )


def _portfolio(agent: str, tickers: list[str]) -> LivePortfolio:
    return LivePortfolio(
        agent=agent,
        positions=[
            Position(
                ticker=t,
                shares=10.0,
                entry_price=100.0,
                entry_date="2026-05-06",
                current_price=110.0,
            )
            for t in tickers
        ],
    )


AS_OF = date(2026, 8, 7)


class TestExport:
    def test_it_writes_one_file_per_held_ticker(self, tmp_path: Path) -> None:
        loader = _StubLoader(
            {
                "AAPL": [("2026-08-05", 249.0), ("2026-08-06", 251.5)],
                "MSFT": [("2026-08-06", 410.0)],
            }
        )

        n = export_prices(
            [_portfolio("graham", ["AAPL", "MSFT"])],
            as_of=AS_OF,
            loader=loader,  # type: ignore[arg-type]
            root=tmp_path,
        )

        assert n == 2
        stored = json.loads((tmp_path / "AAPL.json").read_text())
        assert stored["ticker"] == "AAPL"
        assert stored["as_of"] == "2026-08-07"
        assert stored["points"] == [
            {"d": "2026-08-05", "c": 249.0},
            {"d": "2026-08-06", "c": 251.5},
        ]

    def test_a_ticker_held_by_two_agents_is_fetched_once(
        self, tmp_path: Path
    ) -> None:
        # 210 positions across ten agents overlap heavily; asking the
        # cache once per name rather than once per holding matters.
        loader = _StubLoader({"AAPL": [("2026-08-06", 249.0)]})

        export_prices(
            [_portfolio("graham", ["AAPL"]), _portfolio("buffett", ["AAPL"])],
            as_of=AS_OF,
            loader=loader,  # type: ignore[arg-type]
            root=tmp_path,
        )

        assert loader.asked == ["AAPL"]

    def test_an_uncached_ticker_is_skipped_not_fetched(
        self, tmp_path: Path
    ) -> None:
        # No network from here. An empty frame means no chart, which the
        # UI reads as "not available" rather than drawing a flat line.
        loader = _StubLoader({})

        n = export_prices(
            [_portfolio("graham", ["NOPE"])],
            as_of=AS_OF,
            loader=loader,  # type: ignore[arg-type]
            root=tmp_path,
        )

        assert n == 0
        assert not (tmp_path / "NOPE.json").exists()

    def test_nothing_held_writes_nothing(self, tmp_path: Path) -> None:
        loader = _StubLoader({"AAPL": [("2026-08-06", 249.0)]})

        assert (
            export_prices(
                [_portfolio("graham", [])],
                as_of=AS_OF,
                loader=loader,  # type: ignore[arg-type]
                root=tmp_path,
            )
            == 0
        )
        assert loader.asked == []

    def test_one_bad_ticker_does_not_cost_the_others(
        self, tmp_path: Path
    ) -> None:
        class _Exploding(_StubLoader):
            def get_history(
                self, ticker: str, *, start: date, end: date
            ) -> pd.DataFrame:
                if ticker == "BOOM":
                    raise RuntimeError("cache corrupt")
                return super().get_history(ticker, start=start, end=end)

        loader = _Exploding({"AAPL": [("2026-08-06", 249.0)]})

        n = export_prices(
            [_portfolio("graham", ["AAPL", "BOOM"])],
            as_of=AS_OF,
            loader=loader,  # type: ignore[arg-type]
            root=tmp_path,
        )

        assert n == 1
        assert (tmp_path / "AAPL.json").exists()

    def test_nan_points_are_dropped(self, tmp_path: Path) -> None:
        loader = _StubLoader(
            {"AAPL": [("2026-08-05", float("nan")), ("2026-08-06", 249.0)]}
        )

        export_prices(
            [_portfolio("graham", ["AAPL"])],
            as_of=AS_OF,
            loader=loader,  # type: ignore[arg-type]
            root=tmp_path,
        )

        stored = json.loads((tmp_path / "AAPL.json").read_text())
        assert stored["points"] == [{"d": "2026-08-06", "c": 249.0}]

    def test_the_window_is_a_year(self, tmp_path: Path) -> None:
        # Schloss's entry condition speaks in 52-week terms, so the
        # published series has to cover one.
        captured: dict[str, date] = {}

        class _Recording(_StubLoader):
            def get_history(
                self, ticker: str, *, start: date, end: date
            ) -> pd.DataFrame:
                captured["start"] = start
                captured["end"] = end
                return super().get_history(ticker, start=start, end=end)

        loader = _Recording({"AAPL": [("2026-08-06", 249.0)]})
        export_prices(
            [_portfolio("graham", ["AAPL"])],
            as_of=AS_OF,
            loader=loader,  # type: ignore[arg-type]
            root=tmp_path,
        )

        assert captured["end"] == AS_OF
        assert (AS_OF - captured["start"]).days == 365


@pytest.mark.parametrize("agents", [1, 3])
def test_it_scales_over_agents_without_duplicating_work(
    tmp_path: Path, agents: int
) -> None:
    loader = _StubLoader({"AAPL": [("2026-08-06", 249.0)]})

    export_prices(
        [_portfolio(f"a{i}", ["AAPL"]) for i in range(agents)],
        as_of=AS_OF,
        loader=loader,  # type: ignore[arg-type]
        root=tmp_path,
    )

    assert loader.asked == ["AAPL"]
