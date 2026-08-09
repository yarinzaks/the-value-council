"""Tests for the eleventh agent.

The screen is simple — biggest first, weighted by size — so almost
everything here guards the two ways a market capitalisation goes wrong.
A filed share count off by three orders of magnitude produced a $6,276bn
company that took a 41% weight and turned this design into -0.94% a
year; a reverse-split artefact produced one at $1,649,620bn. Neither
raised anything.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents.market_core.strategy import MIN_DAILY_TURNOVER, MarketCore

AS_OF = date(2024, 12, 31)


class _Prices:
    def __init__(self, prices: dict[str, float | None]) -> None:
        self._p = prices

    def get(self, ticker: str) -> float | None:
        return self._p.get(ticker)


class _Loader:
    """Stands in for PriceDataLoader.median_dollar_volume."""

    def __init__(self, volumes: dict[str, float]) -> None:
        self._v = volumes
        self.asked: list[str] = []

    def median_dollar_volume(
        self, tickers: list[str], as_of: date, *, sessions: int = 63
    ) -> dict[str, float]:
        self.asked = list(tickers)
        return dict(self._v)


class _Cache:
    """Stands in for the EDGAR cache, via the shares helper's interface."""

    def __init__(self, shares: dict[str, float]) -> None:
        self._s = shares


def _strategy(
    volumes: dict[str, float],
    shares: dict[str, float],
    monkeypatch: pytest.MonkeyPatch,
    **kwargs: object,
) -> MarketCore:
    import agents.market_core.strategy as module

    monkeypatch.setattr(
        module, "shares_known_at", lambda cache, tickers, as_of: dict(shares)
    )
    kwargs.setdefault("min_dollar_volume", 1.0)
    return MarketCore(
        price_loader=_Loader(volumes),  # type: ignore[arg-type]
        edgar_cache=_Cache(shares),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


class TestSelection:
    def test_it_holds_the_largest_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _strategy(
            {"BIG": 1e8, "MID": 1e8, "SMALL": 1e8},
            {"BIG": 1e9, "MID": 1e8, "SMALL": 1e7},
            monkeypatch,
            portfolio_size=2,
        )
        w = s.select(AS_OF, ["BIG", "MID", "SMALL"], _Prices({"BIG": 10.0, "MID": 10.0, "SMALL": 10.0}), None)  # type: ignore[arg-type]
        assert set(w) == {"BIG", "MID"}

    def test_weights_are_proportional_to_capitalisation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _strategy(
            {"A": 1e8, "B": 1e8},
            {"A": 3e8, "B": 1e8},
            monkeypatch,
        )
        w = s.select(AS_OF, ["A", "B"], _Prices({"A": 10.0, "B": 10.0}), None)  # type: ignore[arg-type]
        assert w["A"] == pytest.approx(0.75)
        assert w["B"] == pytest.approx(0.25)

    def test_weights_sum_to_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s = _strategy(
            {t: 1e8 for t in "ABCDE"},
            {t: (i + 1) * 1e8 for i, t in enumerate("ABCDE")},
            monkeypatch,
            portfolio_size=3,
        )
        w = s.select(AS_OF, list("ABCDE"), _Prices({t: 10.0 for t in "ABCDE"}), None)  # type: ignore[arg-type]
        assert sum(w.values()) == pytest.approx(1.0)

    def test_a_thin_universe_holds_what_it_has(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _strategy({"A": 1e8}, {"A": 1e8}, monkeypatch, portfolio_size=25)
        w = s.select(AS_OF, ["A"], _Prices({"A": 10.0}), None)  # type: ignore[arg-type]
        assert w == {"A": pytest.approx(1.0)}


class TestCapitalisationsThatCannotBeTrue:
    def test_a_share_count_off_by_a_thousand_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The PKG shape: a plausible price, a filed count three orders
        # of magnitude too large, and volume belonging to the real
        # company. Left in, it is the biggest thing in the universe and
        # takes most of the book.
        s = _strategy(
            {"PKG": 2e8, "REAL": 5e8},
            {"PKG": 94.1e9, "REAL": 1e9},
            monkeypatch,
        )
        w = s.select(AS_OF, ["PKG", "REAL"], _Prices({"PKG": 66.68, "REAL": 100.0}), None)  # type: ignore[arg-type]
        assert "PKG" not in w
        assert w["REAL"] == pytest.approx(1.0)

    def test_a_reverse_split_artefact_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _strategy(
            {"JAGX": 2.4e6, "REAL": 5e8},
            {"JAGX": 171_000.0, "REAL": 1e9},
            monkeypatch,
        )
        w = s.select(AS_OF, ["JAGX", "REAL"], _Prices({"JAGX": 9_627_188.0, "REAL": 100.0}), None)  # type: ignore[arg-type]
        assert "JAGX" not in w

    def test_a_genuinely_quiet_large_company_is_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The gate has to leave real companies alone. 0.05% a day is the
        # 1st percentile of actual turnover, not an error.
        cap = 1e10
        s = _strategy({"QUIET": cap * 0.0005}, {"QUIET": 1e9}, monkeypatch)
        w = s.select(AS_OF, ["QUIET"], _Prices({"QUIET": 10.0}), None)  # type: ignore[arg-type]
        assert w["QUIET"] == pytest.approx(1.0)

    def test_the_gate_sits_below_the_first_percentile(self) -> None:
        # Median turnover is 0.72% a day and the 1st percentile 0.058%.
        assert MIN_DAILY_TURNOVER < 0.00058


class TestRefusingToTrade:
    def test_nothing_liquid_is_a_hold_not_a_liquidation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty dict is the runner's "no trade" signal. A partial
        # book would sell everything the screen could not see.
        s = _strategy({}, {"A": 1e9}, monkeypatch)
        assert s.select(AS_OF, ["A"], _Prices({"A": 10.0}), None) == {}  # type: ignore[arg-type]

    def test_no_share_counts_is_a_hold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _strategy({"A": 1e8}, {}, monkeypatch)
        assert s.select(AS_OF, ["A"], _Prices({"A": 10.0}), None) == {}  # type: ignore[arg-type]

    def test_a_name_with_no_price_is_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _strategy({"A": 1e8, "B": 1e8}, {"A": 1e9, "B": 1e9}, monkeypatch)
        w = s.select(AS_OF, ["A", "B"], _Prices({"A": 10.0, "B": None}), None)  # type: ignore[arg-type]
        assert set(w) == {"A"}


class TestConstruction:
    def test_a_zero_portfolio_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ValueError):
            _strategy({}, {}, monkeypatch, portfolio_size=0)

    def test_a_zero_volume_floor_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ValueError):
            _strategy({}, {}, monkeypatch, min_dollar_volume=0.0)


class TestPicksRecordTheArithmetic:
    def test_each_pick_carries_its_capitalisation_and_weight(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _strategy({"A": 1e8, "B": 1e8}, {"A": 3e8, "B": 1e8}, monkeypatch)
        s.select(AS_OF, ["A", "B"], _Prices({"A": 10.0, "B": 10.0}), None)  # type: ignore[arg-type]
        top = s.last_picks[0]
        assert top.ticker == "A"
        assert top.weight_pct == pytest.approx(75.0)
        assert "bn" in top.why_en
        assert "מיליארד" in top.why_he
