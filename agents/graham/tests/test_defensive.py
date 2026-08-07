"""Tests for Graham Defensive Investor screen + fallback wiring."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest

from agents.graham.filters import (
    DEFAULT_DEFENSIVE_MAX_PB,
    DEFAULT_DEFENSIVE_MAX_PE,
    DEFAULT_DEFENSIVE_MIN_CURRENT_RATIO,
    current_ratio,
    debt_to_equity,
    filter_defensive_candidates,
    passes_defensive_filters,
    pb_ratio,
    pe_ratio,
)
from agents.graham.net_net import BenjaminGraham
from agents.graham.ranking import score_defensive_candidates
from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials


def _fin(
    *,
    ticker: str = "TEST",
    eps_diluted: float | None = 2.0,
    eps_basic: float | None = 2.0,
    total_equity: float | None = 800_000_000.0,
    current_assets: float | None = 500_000_000.0,
    current_liabilities: float | None = 200_000_000.0,
    total_debt: float | None = 100_000_000.0,
    long_term_debt: float | None = 100_000_000.0,
    net_income: float | None = 50_000_000.0,
    shares_outstanding: float | None = 100_000_000.0,
    total_liabilities: float | None = 300_000_000.0,
    dividends_paid: float | None = -20_000_000.0,
) -> PointInTimeFinancials:
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
        eps_diluted=eps_diluted,
        eps_basic=eps_basic,
        total_equity=total_equity,
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        total_debt=total_debt,
        long_term_debt=long_term_debt,
        net_income=net_income,
        shares_outstanding=shares_outstanding,
        total_liabilities=total_liabilities,
        dividends_paid=dividends_paid,
    )


class _StubPriceLookup:
    def __init__(self, prices: Mapping[str, float]) -> None:
        self._prices = dict(prices)

    def get(self, ticker: str) -> float | None:
        return self._prices.get(ticker.upper())


class _StubFundamentalsLookup:
    def __init__(self, fin: Mapping[str, PointInTimeFinancials | None]) -> None:
        self._fin = dict(fin)

    def get(self, ticker: str) -> PointInTimeFinancials | None:
        return self._fin.get(ticker.upper())


# --------------------------------------------------------------------------
# Helper-level tests
# --------------------------------------------------------------------------
class TestPeRatio:
    def test_basic(self) -> None:
        assert pe_ratio(20.0, _fin()) == pytest.approx(10.0)

    def test_falls_back_to_basic(self) -> None:
        assert pe_ratio(20.0, _fin(eps_diluted=None)) == pytest.approx(10.0)

    def test_negative_eps(self) -> None:
        assert pe_ratio(20.0, _fin(eps_diluted=-1, eps_basic=-1)) is None

    def test_missing_price(self) -> None:
        assert pe_ratio(None, _fin()) is None


class TestPbRatio:
    def test_basic(self) -> None:
        # mcap 1B / equity 800M = 1.25
        assert pb_ratio(1_000_000_000.0, _fin()) == pytest.approx(1.25)

    def test_negative_equity(self) -> None:
        assert pb_ratio(1_000_000_000.0, _fin(total_equity=-1)) is None


class TestCurrentRatio:
    def test_basic(self) -> None:
        # 500M / 200M = 2.5
        assert current_ratio(_fin()) == pytest.approx(2.5)

    def test_zero_current_liabilities(self) -> None:
        assert current_ratio(_fin(current_liabilities=0)) is None

    def test_missing_current_assets(self) -> None:
        assert current_ratio(_fin(current_assets=None)) is None

    def test_none_input(self) -> None:
        assert current_ratio(None) is None


# --------------------------------------------------------------------------
# Defensive Investor screen
# --------------------------------------------------------------------------
class TestPassesDefensiveFilters:
    def _candidate(self, **overrides) -> tuple:
        defaults = dict(
            eps_diluted=2.0,
            eps_basic=2.0,
            total_equity=800_000_000.0,
            current_assets=500_000_000.0,
            current_liabilities=200_000_000.0,
            net_income=50_000_000.0,
            total_debt=100_000_000.0,
            shares_outstanding=100_000_000.0,
        )
        defaults.update({k: v for k, v in overrides.items() if k in defaults})
        fin = _fin(**defaults)
        # Default price 10 gives mcap 1B, P/E = 10/2 = 5 (passes), P/B = 1B/800M = 1.25 (passes)
        price = overrides.get("price", 10.0)
        market_cap = price * defaults["shares_outstanding"]
        return fin, market_cap, price

    def test_clean_candidate_passes(self) -> None:
        fin, mcap, price = self._candidate()
        result = passes_defensive_filters(
            fin, mcap, price, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert result.passed is True

    def test_high_pe_rejected(self) -> None:
        # P/E 20 with default eps=2 → price=40
        fin, mcap, price = self._candidate(price=40.0)
        result = passes_defensive_filters(
            fin, mcap, price, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert result.passed is False
        assert "P/E" in (result.rejection_reason or "")

    def test_high_pb_rejected(self) -> None:
        # equity 100M, mcap 1B → P/B = 10
        fin, mcap, price = self._candidate(total_equity=100_000_000)
        result = passes_defensive_filters(
            fin, mcap, price, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert result.passed is False
        assert "P/B" in (result.rejection_reason or "")

    def test_low_current_ratio_rejected(self) -> None:
        # CA 200M, CL 200M → CR = 1.0 < 2.0
        fin, mcap, price = self._candidate(current_assets=200_000_000)
        result = passes_defensive_filters(
            fin, mcap, price, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert result.passed is False
        assert "current ratio" in (result.rejection_reason or "")

    def test_negative_net_income_rejected(self) -> None:
        fin, mcap, price = self._candidate(net_income=-1)
        result = passes_defensive_filters(
            fin, mcap, price, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert result.passed is False
        assert "net income" in (result.rejection_reason or "")

    def test_high_de_rejected(self) -> None:
        fin, mcap, price = self._candidate(total_debt=2_000_000_000)
        result = passes_defensive_filters(
            fin, mcap, price, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert result.passed is False
        assert "D/E" in (result.rejection_reason or "")

    def test_share_class_rejected(self) -> None:
        result = passes_defensive_filters(
            _fin(ticker="ABC-A"), 1_000_000_000, 10.0, as_of=date(2024, 6, 30)
        )
        assert result.passed is False
        assert "share class" in (result.rejection_reason or "")


class TestFilterDefensiveBatch:
    def test_only_passers_returned(self) -> None:
        ok = _fin(ticker="OK")
        # Make EXP fail P/E by giving it tiny eps
        exp = _fin(ticker="EXP", eps_diluted=0.1, eps_basic=0.1)
        candidates = [
            (ok, 1_000_000_000.0, 10.0),
            (exp, 1_000_000_000.0, 10.0),
        ]
        passed = filter_defensive_candidates(
            candidates, as_of=date(2024, 6, 30), min_market_cap=100_000_000
        )
        assert {f.ticker for f, _, _ in passed} == {"OK"}


class TestScoreDefensive:
    def test_orders_by_pe_times_pb(self) -> None:
        # CHEAP: P/E=5, P/B=1.0 → 5
        # EXPENSIVE: P/E=10, P/B=1.4 → 14
        cheap = _fin(ticker="CHEAP", eps_diluted=4.0, eps_basic=4.0, total_equity=2_000_000_000)
        expensive = _fin(ticker="EXP", eps_diluted=2.0, eps_basic=2.0, total_equity=714_285_714)
        # mcap 1B for both via price=10, shares=100M
        candidates = [(cheap, 1_000_000_000.0, 20.0), (expensive, 1_000_000_000.0, 20.0)]
        scores = score_defensive_candidates(candidates)
        assert scores[0].ticker == "CHEAP"

    def test_skips_undefined_metrics(self) -> None:
        broken = _fin(ticker="BROKEN", current_assets=None)
        scores = score_defensive_candidates(
            [(broken, 1_000_000_000.0, 10.0)]
        )
        assert scores == []


# --------------------------------------------------------------------------
# Strategy-level fallback wiring
# --------------------------------------------------------------------------
def _make_full_fin(
    ticker: str,
    *,
    eps: float = 2.0,
    equity: float = 800_000_000,
    current_assets: float = 500_000_000,
    current_liabilities: float = 200_000_000,
    total_liabilities: float = 300_000_000,
    debt: float = 100_000_000,
    net_income: float = 50_000_000,
    shares: float = 100_000_000,
) -> PointInTimeFinancials:
    return _fin(
        ticker=ticker,
        eps_diluted=eps,
        eps_basic=eps,
        total_equity=equity,
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        total_liabilities=total_liabilities,
        total_debt=debt,
        long_term_debt=debt,
        net_income=net_income,
        shares_outstanding=shares,
    )


class TestFallbackWiring:
    def test_falls_back_when_no_net_nets(self) -> None:
        """Modern reality: no Net-Nets, but Defensive picks fill the slate."""
        strat = BenjaminGraham(
            portfolio_size=3,
            min_market_cap=1_000,
            net_net_fallback_threshold=10,
        )
        # All stocks have NCAV = 500M - 300M = 200M, /shares 100M = $2 NCAV.
        # Price 10 → P/NCAV = 5 (way above ⅔). Net-Net rejects all.
        # But P/E = 10/2 = 5, P/B = 1B/800M = 1.25, CR = 2.5 → Defensive passes.
        fins = {f"T{i}": _make_full_fin(f"T{i}") for i in range(5)}
        prices = {t: 10.0 for t in fins}
        weights = strat.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert len(weights) == 3
        sel = strat.selection_history[0]
        assert sel.net_net_count == 0
        assert sel.defensive_count == 3

    def test_uses_only_net_net_when_threshold_met(self) -> None:
        """When >= threshold Net-Nets exist, no fallback is invoked."""
        strat = BenjaminGraham(
            portfolio_size=3,
            min_market_cap=1_000,
            net_net_fallback_threshold=2,  # only need 2 Net-Nets
        )
        # Net-Net qualifying: NCAV/share=2, price=1 → P/NCAV=0.5 < ⅔
        net_nets = {f"NN{i}": _make_full_fin(f"NN{i}") for i in range(5)}
        prices = {t: 1.0 for t in net_nets}
        weights = strat.select(
            date(2024, 6, 30),
            list(net_nets.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(net_nets),
        )
        assert len(weights) == 3
        sel = strat.selection_history[0]
        assert sel.net_net_count == 3
        assert sel.defensive_count == 0

    def test_partial_fallback_mix(self) -> None:
        """Some Net-Nets but below threshold → mix Net-Net + Defensive."""
        strat = BenjaminGraham(
            portfolio_size=4,
            min_market_cap=1_000,
            net_net_fallback_threshold=10,
        )
        # 1 Net-Net (price 1 → P/NCAV = 0.5)
        nn = _make_full_fin("NN1")
        # 3 Defensive (price 10 → P/NCAV = 5, fail Net-Net; pass Defensive)
        defs = {f"DEF{i}": _make_full_fin(f"DEF{i}") for i in range(3)}
        fins = {"NN1": nn, **defs}
        prices = {"NN1": 1.0, **{t: 10.0 for t in defs}}
        weights = strat.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert len(weights) == 4
        assert "NN1" in weights
        sel = strat.selection_history[0]
        assert sel.net_net_count == 1
        assert sel.defensive_count == 3

    def test_fallback_disabled(self) -> None:
        strat = BenjaminGraham(
            portfolio_size=4,
            min_market_cap=1_000,
            enable_defensive_fallback=False,
        )
        fins = {f"DEF{i}": _make_full_fin(f"DEF{i}") for i in range(3)}
        prices = {t: 10.0 for t in fins}
        weights = strat.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        # No Net-Nets and fallback off → empty
        assert weights == {}

    def test_no_double_count_overlap(self) -> None:
        """A ticker that qualifies for BOTH Net-Net and Defensive must not
        appear twice. Net-Net always wins."""
        strat = BenjaminGraham(
            portfolio_size=5,
            min_market_cap=1_000,
            net_net_fallback_threshold=10,
        )
        # Stock T1 qualifies as Net-Net AND would qualify as Defensive
        # Its appearance in net_net_top must exclude it from Defensive search.
        nn = _make_full_fin("T1")
        fins = {"T1": nn}
        prices = {"T1": 1.0}  # P/NCAV = 0.5 (Net-Net pass)
        weights = strat.select(
            date(2024, 6, 30),
            list(fins.keys()),
            _StubPriceLookup(prices),
            _StubFundamentalsLookup(fins),
        )
        assert weights == {"T1": pytest.approx(1.0)}
        sel = strat.selection_history[0]
        assert sel.net_net_count == 1
        assert sel.defensive_count == 0


class TestStrategyConfigDefensive:
    def test_invalid_threshold(self) -> None:
        with pytest.raises(ValueError):
            BenjaminGraham(net_net_fallback_threshold=-1)

    def test_invalid_pe(self) -> None:
        with pytest.raises(ValueError):
            BenjaminGraham(defensive_max_pe=0)

    def test_invalid_pb(self) -> None:
        with pytest.raises(ValueError):
            BenjaminGraham(defensive_max_pb=0)

    def test_invalid_current_ratio(self) -> None:
        with pytest.raises(ValueError):
            BenjaminGraham(defensive_min_current_ratio=0)

    def test_defaults(self) -> None:
        assert DEFAULT_DEFENSIVE_MAX_PE == 15.0
        assert DEFAULT_DEFENSIVE_MAX_PB == 1.5
        assert DEFAULT_DEFENSIVE_MIN_CURRENT_RATIO == 2.0


class TestWorkingCapitalDebtCover:
    """Ch.14 criterion #2, second half. The D/E <= 1.0 check is Walter
    Schloss's rule, not Graham's, and it does not substitute."""

    def test_debt_within_working_capital_passes(self) -> None:
        # WC = 500M - 200M = 300M; LTD 100M.
        result = passes_defensive_filters(
            _fin(),
            1_000_000_000.0,
            10.0,
            as_of=date(2024, 6, 30),
            min_market_cap=100_000_000,
        )
        assert result.passed is True

    def test_debt_above_working_capital_is_rejected(self) -> None:
        # The case D/E cannot catch: long-term debt at 0.9x equity but
        # 2.4x working capital.
        fin = _fin(long_term_debt=720_000_000.0, total_debt=720_000_000.0)
        result = passes_defensive_filters(
            fin,
            1_000_000_000.0,
            10.0,
            as_of=date(2024, 6, 30),
            min_market_cap=100_000_000,
        )
        assert result.passed is False
        assert "working capital" in (result.rejection_reason or "")

    def test_the_de_gate_alone_would_have_passed_it(self) -> None:
        # Proof the two rules are not interchangeable.
        fin = _fin(long_term_debt=720_000_000.0, total_debt=720_000_000.0)
        assert debt_to_equity(fin) == pytest.approx(0.9)

        lenient = passes_defensive_filters(
            fin,
            1_000_000_000.0,
            10.0,
            as_of=date(2024, 6, 30),
            min_market_cap=100_000_000,
            require_working_capital_cover=False,
        )
        assert lenient.passed is True

    def test_unreported_long_term_debt_is_rejected(self) -> None:
        fin = _fin(long_term_debt=None, total_debt=None)
        result = passes_defensive_filters(
            fin,
            1_000_000_000.0,
            10.0,
            as_of=date(2024, 6, 30),
            min_market_cap=100_000_000,
        )
        assert result.passed is False


class TestDividendRequirement:
    """Ch.14 criterion #4. Graham wanted twenty uninterrupted years; the
    cache cannot see that far, so this is the checkable part."""

    def test_a_payer_passes(self) -> None:
        result = passes_defensive_filters(
            _fin(dividends_paid=-20_000_000.0),
            1_000_000_000.0,
            10.0,
            as_of=date(2024, 6, 30),
            min_market_cap=100_000_000,
        )
        assert result.passed is True

    def test_the_sign_carries_no_information(self) -> None:
        # SEC files PaymentsOfDividends positive in some filings and
        # negative in others; only the magnitude means anything.
        result = passes_defensive_filters(
            _fin(dividends_paid=20_000_000.0),
            1_000_000_000.0,
            10.0,
            as_of=date(2024, 6, 30),
            min_market_cap=100_000_000,
        )
        assert result.passed is True

    def test_a_non_payer_is_rejected(self) -> None:
        result = passes_defensive_filters(
            _fin(dividends_paid=0.0),
            1_000_000_000.0,
            10.0,
            as_of=date(2024, 6, 30),
            min_market_cap=100_000_000,
        )
        assert result.passed is False
        assert "dividend" in (result.rejection_reason or "")

    def test_unreported_dividends_are_rejected(self) -> None:
        result = passes_defensive_filters(
            _fin(dividends_paid=None),
            1_000_000_000.0,
            10.0,
            as_of=date(2024, 6, 30),
            min_market_cap=100_000_000,
        )
        assert result.passed is False
