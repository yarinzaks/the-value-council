"""Tests for the soft-scoring rubric.

Each Neff criterion produces a 0-10 score; total over 7 criteria is
0-70. We test the per-criterion ramps directly (pure functions) plus
one integration case showing a partially-passing stock qualifies.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agents.neff.ranking import (
    DEFAULT_MIN_TOTAL_SCORE,
    MAX_TOTAL_SCORE,
    PERSISTENCE_NEUTRAL_SCORE,
    _ramp,
    _ramp_down,
    _score_growth,
    _score_pe,
    _score_roe,
    _score_sales,
    _score_tr_pe,
    _score_yield,
    score_candidates,
)
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact


# ---- Pure-function ramps ---------------------------------------------------
class TestRamp:
    def test_in_band(self) -> None:
        assert _ramp(5.0, 0.0, 10.0) == pytest.approx(5.0)

    def test_below_lo(self) -> None:
        assert _ramp(-1.0, 0.0, 10.0) == 0.0

    def test_above_hi(self) -> None:
        assert _ramp(11.0, 0.0, 10.0) == 10.0

    def test_degenerate(self) -> None:
        assert _ramp(5.0, 5.0, 5.0) == 0.0


class TestRampDown:
    def test_in_band(self) -> None:
        assert _ramp_down(5.0, 0.0, 10.0) == pytest.approx(5.0)

    def test_below_lo(self) -> None:
        assert _ramp_down(-1.0, 0.0, 10.0) == 10.0

    def test_above_hi(self) -> None:
        assert _ramp_down(11.0, 0.0, 10.0) == 0.0


# ---- Per-criterion scoring -------------------------------------------------
class TestScorePe:
    """Sweet spot 40-60% of industry median = 10 pts, ramps either side."""

    def test_in_sweet_spot(self) -> None:
        # median 20; sweet spot [8, 12]. PE=10 → 10 pts.
        assert _score_pe(10.0, 20.0, pe_min_frac=0.4, pe_max_frac=0.6) == 10.0

    def test_above_sweet_spot_ramps_down(self) -> None:
        # median 20, hi=12, ramp to 0 at median (20). PE=16 → halfway → 5.
        s = _score_pe(16.0, 20.0, pe_min_frac=0.4, pe_max_frac=0.6)
        assert 4.0 < s < 6.0

    def test_at_median_zero(self) -> None:
        assert _score_pe(20.0, 20.0, pe_min_frac=0.4, pe_max_frac=0.6) == 0.0

    def test_above_median_zero(self) -> None:
        assert _score_pe(25.0, 20.0, pe_min_frac=0.4, pe_max_frac=0.6) == 0.0

    def test_very_low_pe_low_score(self) -> None:
        # PE = 1 on median 20 → 5% of median, below the 10% cliff → 0.
        assert _score_pe(1.0, 20.0, pe_min_frac=0.4, pe_max_frac=0.6) == 0.0

    def test_zero_pe_zero_score(self) -> None:
        assert _score_pe(0.0, 20.0, pe_min_frac=0.4, pe_max_frac=0.6) == 0.0


class TestScoreYield:
    def test_at_premium(self) -> None:
        # median 2%, premium 2pp; yield ≥ 4% → 10.
        assert _score_yield(4.0, 2.0, premium_pp=2.0) == 10.0

    def test_at_median(self) -> None:
        assert _score_yield(2.0, 2.0, premium_pp=2.0) == 0.0

    def test_halfway(self) -> None:
        assert _score_yield(3.0, 2.0, premium_pp=2.0) == pytest.approx(5.0)


class TestScoreRoe:
    def test_at_target(self) -> None:
        # median 10, target = 1.5 * 10 = 15. ROE 15 → 10 pts.
        assert _score_roe(15.0, 10.0, abs_floor=5.0) == 10.0

    def test_at_median_zero(self) -> None:
        assert _score_roe(10.0, 10.0, abs_floor=5.0) == 0.0

    def test_below_floor_zero(self) -> None:
        # ROE 3 < abs_floor 5 → 0.
        assert _score_roe(3.0, 10.0, abs_floor=5.0) == 0.0


class TestScoreTrPe:
    def test_at_target(self) -> None:
        # median 1.0, multiple 2 → 10 pts at TR/PE ≥ 2.0.
        assert _score_tr_pe(2.0, 1.0, multiple=2.0) == 10.0

    def test_at_median_zero(self) -> None:
        assert _score_tr_pe(1.0, 1.0, multiple=2.0) == 0.0

    def test_negative_median_zero(self) -> None:
        assert _score_tr_pe(2.0, -1.0, multiple=2.0) == 0.0


class TestScoreGrowth:
    def test_in_sweet_spot(self) -> None:
        for g in (7.0, 12.0, 20.0):
            assert _score_growth(g, lo=7.0, hi=20.0) == 10.0

    def test_below_sweet_spot(self) -> None:
        # 0 → 0, 7 → 10, 3.5 → 5
        assert _score_growth(3.5, lo=7.0, hi=20.0) == pytest.approx(5.0)

    def test_above_sweet_spot(self) -> None:
        # 20 → 10, 30 → 0, 25 → 5
        assert _score_growth(25.0, lo=7.0, hi=20.0) == pytest.approx(5.0)

    def test_negative_growth_zero(self) -> None:
        assert _score_growth(-5.0, lo=7.0, hi=20.0) == 0.0


class TestScoreSales:
    def test_sales_drives_eps(self) -> None:
        # sales_growth 10, eps 8 → ratio > 1 → 10
        assert _score_sales(10.0, 8.0) == 10.0

    def test_partial(self) -> None:
        assert _score_sales(4.0, 8.0) == pytest.approx(5.0)

    def test_eps_negative_neutral(self) -> None:
        assert _score_sales(5.0, -3.0) == 5.0

    def test_sales_none_neutral(self) -> None:
        assert _score_sales(None, 5.0) == 5.0


# ---- Integration: a partially-passing stock now qualifies ------------------
def _fact(
    *,
    concept: str,
    value: float,
    period_end: date,
    filed: date,
    accession: str,
) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace="us-gaap",
        unit="USD",
        value=value,
        period_start=date(period_end.year, 1, 1),
        period_end=period_end,
        filed=filed,
        form="10-K",
        fiscal_year=period_end.year,
        fiscal_period="FY",
        accession_number=accession,
    )


def _fin(ticker: str, *, eps: float, dividends: float) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=date(2024, 6, 30),
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
        total_debt=100_000_000.0,
        long_term_debt=100_000_000.0,
        net_income=130_000_000.0,
        shares_outstanding=100_000_000.0,
    )


def test_partially_passing_stock_qualifies(tmp_path: Path) -> None:
    """A stock that misses ONE criterion (yield premium) but excels
    on the others should qualify under the soft scoring system —
    that's the whole point of the refactor."""
    cache = EdgarCache(cache_dir=tmp_path)
    # 6 peers — establishes industry medians.
    candidates = []
    for i in range(6):
        t = f"PEER{i}"
        # Peers: PE=20 (high), yield=1% (low), EPS growth ~5%
        f = _fin(t, eps=1.0, dividends=-10_000_000)
        candidates.append((f, 1_000_000_000.0, 20.0))
        # Plant 4Y EPS history: 1.0 → ~1.22 ≈ 5% CAGR (below sweet spot)
        cache.save_facts(t, [
            _fact(concept="EarningsPerShareDiluted", value=1.0,
                  period_end=date(2019, 12, 31), filed=date(2020, 2, 15),
                  accession=f"{t}-2020"),
            _fact(concept="EarningsPerShareDiluted", value=1.22,
                  period_end=date(2023, 12, 31), filed=date(2024, 2, 15),
                  accession=f"{t}-2024"),
            _fact(concept="Revenues", value=1_000_000_000.0,
                  period_end=date(2019, 12, 31), filed=date(2020, 2, 15),
                  accession=f"{t}-2020"),
            _fact(concept="Revenues", value=1_220_000_000.0,
                  period_end=date(2023, 12, 31), filed=date(2024, 2, 15),
                  accession=f"{t}-2024"),
        ])
    # Candidate: PE=10 (50% of peer median 20 — sweet spot),
    # yield=1% (≈ peer median, NOT a premium — fails yield),
    # 12% EPS growth (sweet spot), strong ROE.
    cand = _fin("CAND", eps=2.0, dividends=-10_000_000)
    candidates.append((cand, 2_000_000_000.0, 20.0))  # PE=10
    cache.save_facts("CAND", [
        _fact(concept="EarningsPerShareDiluted", value=1.0,
              period_end=date(2019, 12, 31), filed=date(2020, 2, 15),
              accession="CAND-2020"),
        _fact(concept="EarningsPerShareDiluted", value=1.575,
              period_end=date(2023, 12, 31), filed=date(2024, 2, 15),
              accession="CAND-2024"),
        _fact(concept="Revenues", value=1_000_000_000.0,
              period_end=date(2019, 12, 31), filed=date(2020, 2, 15),
              accession="CAND-2020"),
        _fact(concept="Revenues", value=1_575_000_000.0,
              period_end=date(2023, 12, 31), filed=date(2024, 2, 15),
              accession="CAND-2024"),
    ])

    scores = score_candidates(
        candidates, as_of=date(2024, 6, 30), edgar_cache=cache
    )
    tickers = [s.ticker for s in scores]
    assert "CAND" in tickers, (
        f"expected CAND to qualify on partial pass; got {tickers}"
    )
    cand_score = next(s for s in scores if s.ticker == "CAND")
    # Must clear the floor.
    assert cand_score.total_score >= DEFAULT_MIN_TOTAL_SCORE
    # Persistence neutral-fill in place.
    assert cand_score.score_persistence == PERSISTENCE_NEUTRAL_SCORE
    # Sanity: total_score doesn't exceed the theoretical max.
    assert cand_score.total_score <= MAX_TOTAL_SCORE


# ---------------------------------------------------------------------------
# Hard gates
# ---------------------------------------------------------------------------
_GATE_TICKERS = [f"PEER{i}" for i in range(1, 6)] + ["NOPAY", "PRICEY"]


@pytest.fixture
def gate_cache(tmp_path: Path) -> EdgarCache:
    """Trailing growth facts for the hard-gate fixtures."""
    cache = EdgarCache(cache_dir=tmp_path / "gate-cache")
    for t in _GATE_TICKERS:
        cache.save_facts(
            t,
            [
                _fact(
                    concept="Revenues",
                    value=1_000_000_000,
                    period_end=date(2019, 12, 31),
                    filed=date(2020, 2, 15),
                    accession=f"{t}-2019",
                ),
                _fact(
                    concept="Revenues",
                    value=1_575_000_000,
                    period_end=date(2023, 12, 31),
                    filed=date(2024, 2, 15),
                    accession=f"{t}-2023",
                ),
                _fact(
                    concept="EarningsPerShareDiluted",
                    value=1.0,
                    period_end=date(2019, 12, 31),
                    filed=date(2020, 2, 15),
                    accession=f"{t}-2019e",
                ),
                _fact(
                    concept="EarningsPerShareDiluted",
                    value=1.575,
                    period_end=date(2023, 12, 31),
                    filed=date(2024, 2, 15),
                    accession=f"{t}-2023e",
                ),
            ],
        )
    return cache


class TestHardGates:
    """The soft-scoring refactor dissolved all seven criteria into one
    35/70 threshold, so no individual criterion could reject any more. A
    candidate strong on growth and ROE carried a zero yield and a
    market-multiple P/E through on the strength of the others — which is
    how the live book ended up with three zero-dividend holdings and a
    name bought at a P/E of 39.6."""

    AS_OF = date(2024, 6, 30)

    @staticmethod
    def _with(entry: tuple) -> list[tuple]:
        """Five cheap dividend-paying peers at P/E 10, plus the entry."""
        out = [
            (_fin(f"PEER{i}", eps=1.0, dividends=-10_000_000), 1_000_000_000.0, 10.0)
            for i in range(1, 6)
        ]
        out.append(entry)
        return out

    def test_a_non_payer_is_rejected(self, gate_cache: EdgarCache) -> None:
        # Neff called the dividend a free part of total return. A stock
        # paying none is not a Neff stock, however it scores elsewhere.
        entry = (_fin("NOPAY", eps=2.0, dividends=0.0), 1_000_000_000.0, 10.0)

        scores = score_candidates(
            self._with(entry), as_of=self.AS_OF, edgar_cache=gate_cache
        )

        assert "NOPAY" not in {s.ticker for s in scores}

    def test_the_dividend_gate_can_be_disabled(self, gate_cache: EdgarCache) -> None:
        entry = (_fin("NOPAY", eps=2.0, dividends=0.0), 1_000_000_000.0, 10.0)

        scores = score_candidates(
            self._with(entry),
            as_of=self.AS_OF,
            edgar_cache=gate_cache,
            min_total_score=0.0,
            require_dividend=False,
        )

        assert "NOPAY" in {s.ticker for s in scores}

    def test_a_high_pe_name_is_rejected(self, gate_cache: EdgarCache) -> None:
        # The live book held one at 39.6 against a market median near 10.
        entry = (_fin("PRICEY", eps=1.0, dividends=-40_000_000), 4_000_000_000.0, 40.0)

        scores = score_candidates(
            self._with(entry), as_of=self.AS_OF, edgar_cache=gate_cache
        )

        assert "PRICEY" not in {s.ticker for s in scores}

    def test_the_pe_ceiling_is_configurable(self, gate_cache: EdgarCache) -> None:
        # min_total_score=0 disables the soft threshold so only the hard
        # gate can be responsible for the difference.
        entry = (_fin("PRICEY", eps=1.0, dividends=-40_000_000), 4_000_000_000.0, 40.0)

        strict = score_candidates(
            self._with(entry),
            as_of=self.AS_OF,
            edgar_cache=gate_cache,
            min_total_score=0.0,
        )
        loose = score_candidates(
            self._with(entry),
            as_of=self.AS_OF,
            edgar_cache=gate_cache,
            min_total_score=0.0,
            max_pe_frac_of_market=10.0,
        )

        assert "PRICEY" not in {s.ticker for s in strict}
        assert "PRICEY" in {s.ticker for s in loose}
