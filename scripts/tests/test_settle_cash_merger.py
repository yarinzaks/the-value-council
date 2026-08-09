"""What a cash merger settlement must and must not do."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from core.live.portfolio import LivePortfolio
from scripts.settle_cash_merger import (
    MERGER_COST_BPS,
    StillTradingError,
    settle,
    verify_delisted,
)


class _StubLoader:
    """Stands in for PriceDataLoader's cached_range."""

    def __init__(self, ranges: dict[str, tuple[str, str] | None]) -> None:
        self._ranges = ranges

    def cached_range(self, ticker: str):
        return self._ranges.get(ticker.upper())


def _book(tmp_path: Path, agent: str, positions: list[dict], cash: float = 100.0) -> Path:
    path = tmp_path / f"{agent}.json"
    path.write_text(
        json.dumps(
            {
                "agent": agent,
                "cash": cash,
                "initial_cash": 10_000.0,
                "cumulative_costs": 0.0,
                "cumulative_dividends": 0.0,
                "positions": positions,
                "watchlist": [],
                "last_updated": "2026-06-01T00:00:00+00:00",
            }
        )
    )
    return path


def _position(ticker: str, shares: float, entry: float) -> dict:
    return {
        "ticker": ticker,
        "shares": shares,
        "entry_price": entry,
        "entry_date": "2026-05-27",
        "current_price": entry,
        "pnl_usd": 0.0,
        "pnl_pct": 0.0,
        "weight_pct": 0.0,
        "why_en": "",
        "why_he": "",
    }


class TestVerifyDelisted:
    def test_accepts_a_symbol_that_went_quiet_before_the_date(self) -> None:
        loader = _StubLoader({"THR": ("2026-01-02", "2026-05-29")})
        evidence = verify_delisted("THR", date(2026, 6, 1), loader=loader)
        assert any("2026-05-29" in line for line in evidence)
        assert any("silence" in line for line in evidence)

    def test_refuses_a_symbol_still_trading(self) -> None:
        """The guard that matters: never cash out a live holding."""
        loader = _StubLoader({"TJX": ("2020-01-02", "2026-08-07")})
        with pytest.raises(StillTradingError, match="still trading"):
            verify_delisted("TJX", date(2026, 6, 1), loader=loader)

    def test_refuses_a_bar_exactly_on_the_effective_date(self) -> None:
        """Trading is suspended *before* the open on the effective date,
        so a bar dated that day contradicts the premise."""
        loader = _StubLoader({"X": ("2020-01-02", "2026-06-01")})
        with pytest.raises(StillTradingError):
            verify_delisted("X", date(2026, 6, 1), loader=loader)

    def test_refuses_a_symbol_with_no_history(self) -> None:
        loader = _StubLoader({"NOPE": None})
        with pytest.raises(StillTradingError, match="no cached price history"):
            verify_delisted("NOPE", date(2026, 6, 1), loader=loader)


class TestSettle:
    def test_converts_the_line_to_cash_at_the_stated_price(self, tmp_path: Path) -> None:
        _book(tmp_path, "lynch", [_position("THR", 4.0, 69.29)], cash=100.0)

        results = settle("THR", 63.89, apply=True, directory=tmp_path)

        assert len(results) == 1
        assert results[0].shares == 4.0
        assert results[0].proceeds == pytest.approx(4.0 * 63.89)
        assert results[0].realized_pnl == pytest.approx((63.89 - 69.29) * 4.0)

        after = LivePortfolio.load_or_seed("lynch", directory=tmp_path)
        assert not after.has("THR")
        assert after.cash == pytest.approx(100.0 + 4.0 * 63.89)

    def test_charges_no_commission(self, tmp_path: Path) -> None:
        """A merger conversion is not a trade. 10bp here would be a cost
        nobody paid."""
        _book(tmp_path, "lynch", [_position("THR", 4.0, 69.29)], cash=0.0)

        settle("THR", 63.89, apply=True, directory=tmp_path)

        after = LivePortfolio.load_or_seed("lynch", directory=tmp_path)
        assert MERGER_COST_BPS == 0.0
        assert after.cumulative_costs == pytest.approx(0.0)
        # Every cent of the consideration reached the book.
        assert after.cash == pytest.approx(4.0 * 63.89)

    def test_settles_every_book_that_holds_it(self, tmp_path: Path) -> None:
        _book(tmp_path, "lynch", [_position("THR", 4.0, 69.29)])
        _book(tmp_path, "neff", [_position("THR", 10.0, 50.00)])
        _book(tmp_path, "graham", [_position("TJX", 3.0, 100.0)])

        results = settle("THR", 63.89, apply=True, directory=tmp_path)

        assert {r.agent for r in results} == {"lynch", "neff"}
        # A book that never held it is untouched.
        assert LivePortfolio.load_or_seed("graham", directory=tmp_path).has("TJX")

    def test_reports_without_writing_unless_applied(self, tmp_path: Path) -> None:
        _book(tmp_path, "lynch", [_position("THR", 4.0, 69.29)])

        results = settle("THR", 63.89, apply=False, directory=tmp_path)

        assert len(results) == 1
        # The dry run must leave the file exactly as it was.
        after = LivePortfolio.load_or_seed("lynch", directory=tmp_path)
        assert after.has("THR")

    def test_refuses_a_non_positive_price(self, tmp_path: Path) -> None:
        _book(tmp_path, "lynch", [_position("THR", 4.0, 69.29)])
        for bad in (0.0, -1.0):
            with pytest.raises(ValueError, match="non-positive"):
                settle("THR", bad, apply=True, directory=tmp_path)

    def test_no_holder_is_not_an_error(self, tmp_path: Path) -> None:
        _book(tmp_path, "graham", [_position("TJX", 3.0, 100.0)])
        assert settle("THR", 63.89, apply=True, directory=tmp_path) == []

    def test_realized_pct_is_against_cost_basis(self, tmp_path: Path) -> None:
        _book(tmp_path, "lynch", [_position("THR", 4.0, 69.29)])
        result = settle("THR", 63.89, apply=False, directory=tmp_path)[0]
        expected = ((63.89 - 69.29) / 69.29) * 100.0
        assert result.realized_pct == pytest.approx(expected)
