"""Tests for the two whole-universe screening measures.

Both are read across every candidate at every rebalance, so both are
one query rather than one per name — and both have a failure mode where
a wrong answer is worse than no answer. A volatility estimated from six
bars ranks as the calmest stock in the market. A dollar volume computed
from a split-adjusted share count is wrong by the split factor, which
is future information.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.backtest.data_loader import PriceDataLoader

AS_OF = date(2020, 12, 31)


def _sessions(n: int, end: str = "2020-12-31") -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=n)


def _write(
    loader: PriceDataLoader,
    ticker: str,
    closes: np.ndarray,
    *,
    volume: float = 1_000_000.0,
    end: str = "2020-12-31",
) -> None:
    idx = _sessions(len(closes), end=end)
    frame = pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Adj Close": closes,
            "Volume": np.full(len(closes), volume),
        },
        index=idx,
    )
    loader._write_cache(ticker, frame)


def _market(n: int = 200, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.01, n))


class TestIdiosyncraticVolatility:
    def test_a_stock_that_only_follows_the_market_has_almost_none(
        self, tmp_path: Path
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        market = _market()
        _write(loader, "SPY", market)

        # Exactly twice the market's daily move, every day. Beta 2, and
        # nothing left over — which is the point: a name can be far more
        # volatile than the index and still have no idiosyncratic risk.
        market_returns = market[1:] / market[:-1] - 1.0
        levered = 100.0 * np.cumprod(np.concatenate([[1.0], 1.0 + 2.0 * market_returns]))
        _write(loader, "BETA2", levered)

        vols = loader.idiosyncratic_volatility(["BETA2"], AS_OF)
        assert vols["BETA2"] < 0.01

    def test_a_stock_with_its_own_noise_has_some(self, tmp_path: Path) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        market = _market()
        _write(loader, "SPY", market)

        rng = np.random.default_rng(7)
        own = 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.02, len(market)))
        _write(loader, "NOISY", own)

        vols = loader.idiosyncratic_volatility(["NOISY"], AS_OF)
        # ~2% daily noise annualises to roughly 32%.
        assert 0.15 < vols["NOISY"] < 0.60

    def test_a_name_with_too_little_history_is_absent(self, tmp_path: Path) -> None:
        # The important one. A ticker with six bars would otherwise be
        # scored off six bars and rank as the calmest company in the
        # market, which is exactly the position a low-volatility screen
        # would then take.
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        _write(loader, "SPY", _market())
        _write(loader, "SHORT", np.full(6, 50.0))

        vols = loader.idiosyncratic_volatility(["SHORT"], AS_OF, min_sessions=63)
        assert "SHORT" not in vols

    def test_the_market_proxy_is_not_returned_as_a_candidate(
        self, tmp_path: Path
    ) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        _write(loader, "SPY", _market())
        assert "SPY" not in loader.idiosyncratic_volatility(["SPY"], AS_OF)

    def test_a_missing_market_proxy_yields_nothing(self, tmp_path: Path) -> None:
        # Without the market there is no residual to measure, and
        # returning total volatility instead would silently answer a
        # different question.
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        _write(loader, "AAA", _market(seed=3))
        assert loader.idiosyncratic_volatility(["AAA"], AS_OF) == {}

    def test_an_empty_cache_yields_nothing(self, tmp_path: Path) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        assert loader.idiosyncratic_volatility(["AAA"], AS_OF) == {}

    def test_bars_after_the_as_of_date_are_not_used(self, tmp_path: Path) -> None:
        # A screen that could see next quarter's volatility would rank
        # on what has not happened yet.
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        market = _market(300, seed=11)
        _write(loader, "SPY", market, end="2021-06-30")

        rng = np.random.default_rng(5)
        calm = 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.002, 200))
        wild = 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.05, 100))
        _write(loader, "TWOFACE", np.concatenate([calm, wild]), end="2021-06-30")

        early = loader.idiosyncratic_volatility(["TWOFACE"], date(2020, 12, 31))
        late = loader.idiosyncratic_volatility(["TWOFACE"], date(2021, 6, 30))
        assert early["TWOFACE"] < late["TWOFACE"]


class TestMedianDollarVolume:
    def test_it_multiplies_price_by_shares(self, tmp_path: Path) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        _write(loader, "AAA", np.full(100, 20.0), volume=500_000.0)
        assert loader.median_dollar_volume(["AAA"], AS_OF)["AAA"] == pytest.approx(
            10_000_000.0
        )

    def test_one_spike_does_not_make_a_name_look_tradeable(
        self, tmp_path: Path
    ) -> None:
        # Median, not mean. A single earnings-day squeeze in an otherwise
        # untraded shell would clear a mean-based floor easily.
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        volumes = np.full(100, 100.0)
        closes = np.full(100, 10.0)
        idx = _sessions(100)
        frame = pd.DataFrame(
            {
                "Open": closes,
                "High": closes,
                "Low": closes,
                "Close": closes,
                "Adj Close": closes,
                "Volume": volumes,
            },
            index=idx,
        )
        frame.iloc[50, frame.columns.get_loc("Volume")] = 100_000_000.0
        loader._write_cache("SPIKY", frame)

        assert loader.median_dollar_volume(["SPIKY"], AS_OF)["SPIKY"] == pytest.approx(
            1_000.0
        )

    def test_bars_after_the_as_of_date_are_not_used(self, tmp_path: Path) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        _write(loader, "AAA", np.full(200, 10.0), volume=1_000.0, end="2021-06-30")
        early = loader.median_dollar_volume(["AAA"], date(2020, 12, 31))
        assert early["AAA"] == pytest.approx(10_000.0)

    def test_an_unknown_ticker_is_absent_rather_than_zero(
        self, tmp_path: Path
    ) -> None:
        # Zero would read as "untradeable", which is a claim. Absent
        # reads as "no answer", which is the truth.
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        _write(loader, "AAA", np.full(100, 10.0))
        result = loader.median_dollar_volume(["AAA", "MISSING"], AS_OF)
        assert "MISSING" not in result

    def test_no_tickers_yields_nothing(self, tmp_path: Path) -> None:
        loader = PriceDataLoader(cache_path=tmp_path / "prices.sqlite")
        assert loader.median_dollar_volume([], AS_OF) == {}
