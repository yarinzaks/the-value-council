"""Unit tests for the PeterLynch strategy class."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from agents.lynch.category_classifier import LynchMemo
from agents.lynch.garp import PeterLynch, _category_weights
from agents.lynch.ranking import LynchScore
from core.backtest.strategy_runner import HeldPosition
from core.data.edgar_cache import EdgarCache

from .conftest import make_pit
from .test_category_classifier import SAMPLE_MEMO_JSON


class _StaticPriceLookup:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def get(self, ticker: str) -> float | None:  # noqa: D401
        return self._prices.get(ticker)


class _StaticFundamentalsLookup:
    def __init__(self, fins: dict[str, object]) -> None:
        self._fins = fins

    def get(self, ticker: str) -> object | None:  # noqa: D401
        return self._fins.get(ticker)


class TestStrategyConfig:
    def test_default_name(self, fast_grower_cache: EdgarCache) -> None:
        s = PeterLynch(edgar_cache=fast_grower_cache)
        assert s.name == "peter_lynch"

    def test_invalid_portfolio_size(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        with pytest.raises(ValueError):
            PeterLynch(edgar_cache=fast_grower_cache, portfolio_size=0)

    def test_invalid_market_cap(self, fast_grower_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            PeterLynch(edgar_cache=fast_grower_cache, min_market_cap=0)

    def test_invalid_max_de(self, fast_grower_cache: EdgarCache) -> None:
        with pytest.raises(ValueError):
            PeterLynch(edgar_cache=fast_grower_cache, max_de=0)

    def test_missing_cache_raises(self) -> None:
        with pytest.raises(ValueError):
            PeterLynch(edgar_cache=None)  # type: ignore[arg-type]


class TestSelectEdgeCases:
    def test_empty_universe(self, fast_grower_cache: EdgarCache) -> None:
        s = PeterLynch(edgar_cache=fast_grower_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=[],
            prices=_StaticPriceLookup({}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({}),  # type: ignore[arg-type]
        )
        assert out == {}

    def test_below_market_cap_returns_cash(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        # Tiny mcap → quality fails.
        f = make_pit("TINY", shares=1_000_000, eps_diluted=2.0)
        s = PeterLynch(edgar_cache=fast_grower_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["TINY"],
            prices=_StaticPriceLookup({"TINY": 1.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"TINY": f}),  # type: ignore[arg-type]
        )
        assert out == {}


class TestQuantOnlyHappyPath:
    def test_fast_grower_qualifies(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,
        )
        s = PeterLynch(edgar_cache=fast_grower_cache)
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
        )
        assert "FASTY" in out
        # A one-name book is capped at the Fast Grower's 5%, not given
        # the whole NAV. The cap is the point: a screen that returns a
        # single name has not earned a 100% position. This asserted 1.0
        # when every category was weighted identically.
        assert out["FASTY"] == pytest.approx(0.05)


class TestLlmFilterIntegration:
    def test_llm_reject_drops_candidate(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,
        )
        rejected = dict(SAMPLE_MEMO_JSON)
        rejected["ticker"] = "FASTY"
        rejected["decision"] = "REJECT"
        memo = LynchMemo.model_validate(rejected)

        classifier = MagicMock()
        classifier.classify.return_value = memo

        s = PeterLynch(
            edgar_cache=fast_grower_cache, category_classifier=classifier
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
        )
        assert out == {}
        classifier.classify.assert_called_once()
        assert s.last_memos == [memo]

    def test_llm_buy_keeps(self, fast_grower_cache: EdgarCache) -> None:
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,
        )
        bought = dict(SAMPLE_MEMO_JSON)
        bought["ticker"] = "FASTY"
        memo = LynchMemo.model_validate(bought)
        classifier = MagicMock()
        classifier.classify.return_value = memo

        s = PeterLynch(
            edgar_cache=fast_grower_cache, category_classifier=classifier
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
        )
        assert "FASTY" in out

    def test_llm_exception_falls_back(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,
        )
        classifier = MagicMock()
        classifier.classify.side_effect = RuntimeError("API down")

        s = PeterLynch(
            edgar_cache=fast_grower_cache, category_classifier=classifier
        )
        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
        )
        # LLM failure should NOT silently drop the candidate.
        assert "FASTY" in out


class TestSelectionHistory:
    def test_records(self, fast_grower_cache: EdgarCache) -> None:
        f = make_pit(
            "FASTY",
            eps_diluted=5.96,
            shares=100_000_000,
            dividends_paid=0.0,
        )
        s = PeterLynch(edgar_cache=fast_grower_cache)
        s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
        )
        recs = s.selections_to_records()
        assert len(recs) == 1
        rec = recs[0]
        assert rec["as_of"] == "2024-06-30"
        assert "FASTY" in rec["selected_tickers"]
        assert rec["top_peg"] is not None


class TestCategoryDrivesPositionSize:
    """The six categories exist because Lynch sizes them differently.

    ``ranking._position_size_for`` has always computed his per-category
    caps and stamped them onto every score. ``select`` then assigned
    ``1.0 / len(top)`` and threw them away, so the taxonomy was
    classified, logged, written into the memo, shown on the dashboard —
    and had no effect on a single dollar. The live book is 29 holdings
    at 3.45% each, a turnaround carrying the same capital as a stalwart.
    """

    @staticmethod
    def _scores(pairs: list[tuple[str, str, float]]) -> list[LynchScore]:
        """``(ticker, category, suggested_pct)`` -> minimal scores."""
        return [
            LynchScore(
                ticker=t,
                price=10.0,
                market_cap=1_000_000_000.0,
                pe=15.0,
                growth_rate_5yr_pct=15.0,
                growth_rate_3yr_pct=15.0,
                growth_acceleration_pct=0.0,
                dividend_yield_pct=0.0,
                peg=1.0,
                pegy=1.0,
                debt_to_equity=0.3,
                net_income=1.0,
                lynch_category=cat,  # type: ignore[arg-type]
                peg_zone="buy",
                suggested_position_size_pct=pct,
            )
            for t, cat, pct in pairs
        ]

    def test_a_fast_grower_outweighs_a_turnaround(self) -> None:
        w = _category_weights(
            self._scores([
                ("FAST", "Fast Grower", 5.0),
                ("TURN", "Turnaround", 3.0),
            ])
        )

        assert w["FAST"] > w["TURN"]
        assert w["FAST"] / w["TURN"] == pytest.approx(5.0 / 3.0)

    def test_a_full_book_is_fully_invested(self) -> None:
        # 30 names at Lynch's own target size. The caps multiply out
        # past 100%, so normalisation absorbs everything and no cap
        # binds — the book is fully deployed.
        pairs = [
            (f"T{i}", "Stalwart", 5.0) if i % 2 else (f"T{i}", "Cyclical", 4.0)
            for i in range(30)
        ]
        w = _category_weights(self._scores(pairs))

        assert sum(w.values()) == pytest.approx(1.0)
        assert all(v <= 0.05 + 1e-9 for v in w.values())

    def test_a_thin_book_holds_cash_rather_than_break_a_cap(self) -> None:
        # Four names cannot absorb 100% without 25% positions in a
        # strategy whose own limit is 5%. The cap wins.
        w = _category_weights(
            self._scores([(f"T{i}", "Fast Grower", 5.0) for i in range(4)])
        )

        assert all(v == pytest.approx(0.05) for v in w.values())
        assert sum(w.values()) == pytest.approx(0.20)

    def test_no_position_ever_exceeds_its_category_cap(self) -> None:
        # Mixed book small enough that normalisation would breach caps
        # if they were not enforced afterwards.
        pairs = [
            ("SLOW", "Slow Grower", 3.0),
            ("STAL", "Stalwart", 5.0),
            ("FAST", "Fast Grower", 5.0),
            ("CYC", "Cyclical", 4.0),
        ]
        w = _category_weights(self._scores(pairs))

        caps = {"SLOW": 0.03, "STAL": 0.05, "FAST": 0.05, "CYC": 0.04}
        for t, cap in caps.items():
            assert w[t] <= cap + 1e-9

    def test_an_empty_book_is_empty(self) -> None:
        assert _category_weights([]) == {}


class TestAWinnerKeepsItsSlot:
    """``select`` used to recompute the top-N from scratch every call.

    ``held`` was in the signature and never read, so a position that
    rose — higher P/E, higher PEG, worse rank — fell out and was sold.
    Success was the sell trigger.
    """

    def test_a_held_winner_survives_a_full_slate_of_cheaper_rivals(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        f = make_pit(
            "FASTY", eps_diluted=5.96, shares=100_000_000, dividends_paid=0.0
        )
        s = PeterLynch(edgar_cache=fast_grower_cache, portfolio_size=1)
        held = {
            "FASTY": HeldPosition(
                ticker="FASTY",
                shares=10.0,
                entry_price=30.0,
                entry_date=date(2023, 1, 5),
                current_price=60.0,
            )
        }

        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
            held=held,
        )

        assert "FASTY" in out
        assert s.last_exits
        assert s.last_exits[0].retained

    def test_without_holdings_the_behaviour_is_unchanged(
        self, fast_grower_cache: EdgarCache
    ) -> None:
        # Backward compatibility: no held mapping means the slate is
        # exactly the top-N it always was.
        f = make_pit(
            "FASTY", eps_diluted=5.96, shares=100_000_000, dividends_paid=0.0
        )
        s = PeterLynch(edgar_cache=fast_grower_cache)

        out = s.select(
            as_of=date(2024, 6, 30),
            universe=["FASTY"],
            prices=_StaticPriceLookup({"FASTY": 60.0}),  # type: ignore[arg-type]
            fundamentals=_StaticFundamentalsLookup({"FASTY": f}),  # type: ignore[arg-type]
        )

        assert "FASTY" in out
        assert s.last_exits == []
