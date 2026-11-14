"""Tests for Lynch's exit rules.

The behaviour these pin down is the one Lynch names most often as the
amateur's cardinal error: selling a position because it went up. Under
the previous implementation ``select`` re-ranked the universe by PEG
every rebalance and kept the best N, so a stock that rose got a higher
P/E, a higher PEG, a worse rank, and was disposed of for having worked.
``held`` was accepted by ``select`` and never read.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from agents.lynch.exits import (
    DEFAULT_MAX_HELD_PEG,
    STALWART_PROFIT_TAKE_PCT,
    decide,
    retained,
)
from agents.lynch.garp import PeterLynch
from agents.lynch.ranking import LynchScore
from core.backtest.strategy_runner import HeldPosition
from core.data.edgar_cache import EdgarCache

ENTRY = date(2025, 1, 15)
AS_OF = date(2026, 8, 5)


def _score(
    ticker: str,
    category: str,
    peg: float,
    *,
    pegy: float | None = None,
) -> LynchScore:
    return LynchScore(
        ticker=ticker,
        price=100.0,
        market_cap=5_000_000_000.0,
        pe=20.0,
        growth_rate_5yr_pct=20.0,
        growth_rate_3yr_pct=20.0,
        growth_acceleration_pct=0.0,
        dividend_yield_pct=0.0,
        peg=peg,
        pegy=pegy if pegy is not None else peg,
        debt_to_equity=0.3,
        net_income=1.0,
        lynch_category=category,  # type: ignore[arg-type]
        peg_zone="hold",
        suggested_position_size_pct=5.0,
    )


def _position(ticker: str, *, entry: float, now: float) -> HeldPosition:
    return HeldPosition(
        ticker=ticker,
        shares=100.0,
        entry_price=entry,
        entry_date=ENTRY,
        current_price=now,
    )


class TestWinnersAreNotSoldForWinning:
    def test_a_fast_grower_that_doubled_is_kept(self) -> None:
        # Bought at PEG 0.6. The price doubled, so the multiple doubled
        # and PEG is now 1.2 — no longer a buy, and not a sell either.
        # This is the exact position rank-slippage exits destroyed.
        d = decide(
            _score("FAST", "Fast Grower", 1.2),
            _position("FAST", entry=50.0, now=100.0),
        )

        assert d.retained

    def test_the_hold_ceiling_is_looser_than_the_buy_bar(self) -> None:
        # Entry is PEG <= 1.0 per category; retention runs to 2.0.
        # Buying and holding are different decisions, and Lynch's own
        # zones already said so — nothing used the hold zone.
        assert DEFAULT_MAX_HELD_PEG == 2.0

        d = decide(
            _score("FAST", "Fast Grower", 1.9),
            _position("FAST", entry=40.0, now=100.0),
        )

        assert d.retained

    def test_past_the_hold_ceiling_it_goes(self) -> None:
        # Retention is not unconditional. Above 2.0 the zone function
        # already reads "avoid", and an avoid is a sell for something
        # you own.
        d = decide(
            _score("FAST", "Fast Grower", 2.4),
            _position("FAST", entry=30.0, now=100.0),
        )

        assert not d.retained
        assert "2.0" in d.reason


class TestStalwartsAreSoldOnPurpose:
    def test_a_stalwart_up_thirty_percent_is_rotated(self) -> None:
        # Lynch does not hold Stalwarts for tenbaggers; he takes 30-50%
        # and rotates into one that has not moved. Being sold to a rule
        # is not the same as being sold for slipping in a ranking.
        d = decide(
            _score("STAL", "Stalwart", 0.9),
            _position("STAL", entry=100.0, now=135.0),
        )

        assert not d.retained
        assert "rotate" in d.reason

    def test_a_stalwart_below_the_band_is_kept(self) -> None:
        d = decide(
            _score("STAL", "Stalwart", 0.9),
            _position("STAL", entry=100.0, now=115.0),
        )

        assert d.retained

    def test_the_band_starts_at_thirty(self) -> None:
        assert STALWART_PROFIT_TAKE_PCT == 30.0

    def test_a_fast_grower_up_thirty_percent_is_not_rotated(self) -> None:
        # The profit-take is a Stalwart rule specifically. Applying it
        # to a Fast Grower would reintroduce the original bug wearing a
        # different threshold.
        d = decide(
            _score("FAST", "Fast Grower", 1.1),
            _position("FAST", entry=100.0, now=180.0),
        )

        assert d.retained


class TestThesisBreaks:
    def test_a_name_that_fails_the_gates_is_sold(self) -> None:
        # No score means it did not survive the quality gates this
        # rebalance — earnings consistency, leverage, FCF. That is the
        # story breaking, not the multiple moving.
        d = decide(None, _position("GONE", entry=100.0, now=140.0))

        assert not d.retained
        assert "quality gates" in d.reason


class TestSlowGrowersUsePegy:
    def test_the_yield_adjusted_ratio_decides(self) -> None:
        # score_candidates sorts Slow Growers on PEGY; retention has to
        # use the same number or the two disagree about the same stock.
        keep_me = _score("SLOW", "Slow Grower", peg=2.6, pegy=1.4)

        assert decide(keep_me, _position("SLOW", entry=90.0, now=100.0)).retained


class TestRetained:
    def test_no_holdings_means_nothing_to_decide(self) -> None:
        assert retained([_score("A", "Stalwart", 0.8)], None) == ([], [])
        assert retained([_score("A", "Stalwart", 0.8)], {}) == ([], [])

    def test_it_reports_on_every_held_name(self) -> None:
        scores = [
            _score("KEEP", "Fast Grower", 1.3),
            _score("DROP", "Fast Grower", 2.9),
        ]
        held = {
            "KEEP": _position("KEEP", entry=50.0, now=100.0),
            "DROP": _position("DROP", entry=50.0, now=100.0),
            "GATED": _position("GATED", entry=50.0, now=100.0),
        }

        keep, decisions = retained(scores, held)

        assert [s.ticker for s in keep] == ["KEEP"]
        assert len(decisions) == 3
        assert {d.ticker for d in decisions if not d.retained} == {
            "DROP",
            "GATED",
        }

    def test_a_flat_position_is_kept(self) -> None:
        # Guard against over-correction in the other direction: a name
        # that has not moved and is still cheap has no reason to go.
        keep, _ = retained(
            [_score("FLAT", "Cyclical", 1.0)],
            {"FLAT": _position("FLAT", entry=100.0, now=100.0)},
        )

        assert [s.ticker for s in keep] == ["FLAT"]

    def test_an_unusable_cost_basis_does_not_force_a_stalwart_out(
        self,
    ) -> None:
        # return_pct is None when the basis is zero or negative. The
        # profit-take cannot fire on an unknown gain.
        d = decide(
            _score("STAL", "Stalwart", 0.9),
            HeldPosition(
                ticker="STAL",
                shares=10.0,
                entry_price=0.0,
                entry_date=ENTRY,
                current_price=100.0,
            ),
        )

        assert d.retained


class TestThresholdsAreConfigurable:
    def test_a_tighter_ceiling_sells_sooner(self) -> None:
        score = _score("FAST", "Fast Grower", 1.5)
        pos = _position("FAST", entry=50.0, now=100.0)

        assert decide(score, pos).retained
        assert not decide(score, pos, max_held_peg=1.2).retained

    def test_a_looser_profit_take_holds_longer(self) -> None:
        score = _score("STAL", "Stalwart", 0.9)
        pos = _position("STAL", entry=100.0, now=135.0)

        assert not decide(score, pos).retained
        assert decide(score, pos, stalwart_profit_take_pct=50.0).retained


@pytest.mark.parametrize(
    "category", ["Fast Grower", "Cyclical", "Turnaround", "Asset Play"]
)
def test_every_non_stalwart_category_survives_a_big_gain(
    category: str,
) -> None:
    d = decide(
        _score("X", category, 1.4), _position("X", entry=40.0, now=100.0)
    )

    assert d.retained


# ---------------------------------------------------------------------------
# A rolling window does not sell a name (audit item 18)
# ---------------------------------------------------------------------------
#
# CRS was held, bought as a Fast Grower, and sold on 2026-09-07 as "no
# longer passes the quality gates". Nothing about the company changed on
# the day it dropped out of the scored set (2026-08-19): its newest 10-K
# still said EPS 7.42, up from 3.70 and 1.14. What changed was the
# calendar. The 5-year CAGR anchors its base at ``as_of - 5 years``, and
# on 2021-08-19 Carpenter filed the FY2021 10-K with EPS -4.76. From
# 2026-08-19 the base was a loss year, a CAGR over a negative base is
# undefined, the score was None, and the name was sold. It would roll
# back out every August.
#
# The EPS series below is CRS's annual diluted EPS as filed (fiscal year
# ends June 30), first-filing dates, read from the fundamentals cache
# for FY2018-FY2025. FY2014-FY2017 sit outside every growth window; they
# are illustrative positive years that only complete the ten-year
# earnings-consistency gate (FY2020's 10-K was filed in September, so an
# August lookup never lands on it).

_CRS_EPS: tuple[tuple[int, float, date], ...] = (
    (2014, 3.25, date(2014, 8, 15)),
    (2015, 2.95, date(2015, 8, 14)),
    (2016, 2.38, date(2016, 8, 18)),
    (2017, 1.03, date(2017, 8, 18)),
    (2018, 3.92, date(2018, 8, 14)),
    (2019, 3.43, date(2019, 8, 29)),
    (2020, 0.02, date(2020, 9, 4)),
    (2021, -4.76, date(2021, 8, 19)),
    (2022, -1.01, date(2022, 8, 15)),
    (2023, 1.14, date(2023, 8, 11)),
    (2024, 3.70, date(2024, 8, 13)),
    (2025, 7.42, date(2025, 8, 12)),
)
_CRS_SHARES = 50_000_000.0
_CRS_PRICE = 527.44  # the journal's CRS price on 2026-08-18
ROLL_DAY = date(2026, 8, 19)


def _crs_cache(
    tmp_path: Path,
    *,
    eps: tuple[tuple[int, float, date], ...] = _CRS_EPS,
) -> EdgarCache:
    from .conftest import make_fact

    facts = []
    for fy, value, filed in eps:
        period_end = date(fy, 6, 30)
        acc = f"CRS-{fy}"
        ni = value * _CRS_SHARES
        for concept, v in (
            ("EarningsPerShareDiluted", value),
            ("NetIncomeLoss", ni),
            ("NetCashProvidedByUsedInOperatingActivities", abs(ni) + 4e8),
            ("PaymentsToAcquirePropertyPlantAndEquipment", 1.5e8),
        ):
            facts.append(
                make_fact(
                    concept=concept,
                    value=v,
                    period_end=period_end,
                    filed=filed,
                    accession=acc,
                )
            )
    cache = EdgarCache(cache_dir=tmp_path)
    cache.save_facts("CRS", facts)
    return cache


class _Lookup:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, ticker: str) -> object | None:
        return self._values.get(ticker)


def _select_crs(
    cache: EdgarCache, as_of: date, *, held: bool
) -> tuple[dict[str, float], PeterLynch]:
    from .conftest import make_pit

    fin = make_pit("CRS", eps_diluted=7.42, shares=_CRS_SHARES)
    book = (
        {"CRS": _position("CRS", entry=_CRS_PRICE * 1.16, now=_CRS_PRICE)}
        if held
        else None
    )
    strategy = PeterLynch(edgar_cache=cache)
    weights = strategy.select(
        as_of,
        ["CRS"],
        _Lookup({"CRS": _CRS_PRICE}),  # type: ignore[arg-type]
        _Lookup({"CRS": fin}),  # type: ignore[arg-type]
        held=book,
    )
    return weights, strategy


class TestAWindowRollDoesNotSell:
    def test_crs_2026_08_19_window_roll_does_not_sell(
        self, tmp_path: Path
    ) -> None:
        weights, strategy = _select_crs(
            _crs_cache(tmp_path), ROLL_DAY, held=True
        )

        assert "CRS" in weights
        [verdict] = strategy.last_exits
        assert verdict.retained, verdict.reason
        # The growth it was kept on is named for the window it covers:
        # the 5- and 4-year bases are loss years, so it is the 3-year
        # cross-check the playbook already runs (§5.3), FY2023 -> FY2025.
        [kept] = strategy.selection_history[-1].top_scores
        assert kept.growth_window_years == 3
        assert kept.growth_rate_5yr_pct == pytest.approx(
            ((7.42 / 1.14) ** 0.5 - 1.0) * 100.0
        )

    def test_the_day_before_the_roll_still_uses_five_years(
        self, tmp_path: Path
    ) -> None:
        weights, strategy = _select_crs(
            _crs_cache(tmp_path), date(2026, 8, 18), held=True
        )

        assert "CRS" in weights
        [kept] = strategy.selection_history[-1].top_scores
        assert kept.growth_window_years == 5

    def test_a_new_buy_still_needs_the_full_five_years(
        self, tmp_path: Path
    ) -> None:
        # Entry is unchanged: the fallback measures a name already
        # owned, not a reason to start owning one.
        weights, _ = _select_crs(_crs_cache(tmp_path), ROLL_DAY, held=False)

        assert weights == {}

    def test_losses_on_every_fallback_base_still_sell(
        self, tmp_path: Path
    ) -> None:
        # If FY2023 had also been a loss there is no demonstrated growth
        # over any window the playbook names, and the name goes.
        eps = tuple(
            (fy, -0.50 if fy == 2023 else v, filed)
            for fy, v, filed in _CRS_EPS
        )
        weights, strategy = _select_crs(
            _crs_cache(tmp_path, eps=eps), ROLL_DAY, held=True
        )

        assert "CRS" not in weights
        [verdict] = strategy.last_exits
        assert not verdict.retained

    def test_a_missing_base_is_not_treated_as_a_loss(
        self, tmp_path: Path
    ) -> None:
        # The fallback is for a base that exists and is a loss. A base
        # that was never filed is missing data, not a window artefact,
        # and keeps the old behaviour.
        eps = tuple(e for e in _CRS_EPS if e[0] >= 2022)
        weights, strategy = _select_crs(
            _crs_cache(tmp_path, eps=eps), ROLL_DAY, held=True
        )

        assert "CRS" not in weights
        [verdict] = strategy.last_exits
        assert not verdict.retained

    def test_the_rationale_names_the_window(self) -> None:
        from core.live.why_translator import lynch_why

        score = _score("CRS", "Fast Grower", 0.4)
        three = replace(score, growth_window_years=3)

        english, hebrew = lynch_why(three)
        assert "3y EPS CAGR" in english
        assert "5y" not in english
        assert "3 שנתית" in hebrew
