"""Unit tests for the playbook-as-written Graham audit."""

from __future__ import annotations

import math
from dataclasses import replace

import pandas as pd
import pytest

from agents.graham.playbook_audit import (
    MarketRates,
    Snapshot,
    annual_values,
    audit,
    build_thesis,
    dividend_streak,
    evaluate,
    exit_triggers,
    extract_snapshot,
    fix_scale_errors,
    is_near_miss,
    sell_target,
)

RATES = MarketRates(aaa_corporate_pct=6.0, treasury_20y_pct=5.0)
GN = math.sqrt(22.5 * (3400e6 / 9 / 100e6) * 40)
INDUSTRY = "Industrials/Metal Fabrications"


def _snap(**overrides: object) -> Snapshot:
    """A company that passes every Defensive criterion.

    100M shares; net income grows evenly from 200M to 400M over
    2016-2025, so the three-year average is ≈377.8M (EPS ≈3.778) against
    ≈222.2M ten years ago (+70%). Book value 4,000M (BVPS 40). Graham
    Number = √(22.5 × 3.778 × 40) ≈ 58.31.
    """
    base = Snapshot(
        net_income={2016 + i: 200e6 + i * 200e6 / 9 for i in range(10)},
        dividends={y: 20e6 for y in range(2009, 2026)},
        current_assets=3_000e6,
        current_liabilities=1_000e6,
        equity=4_000e6,
        total_liabilities=2_000e6,
        long_term_debt=800e6,
        short_term_debt=100e6,
        goodwill=0.0,
        intangibles=0.0,
        preferred=0.0,
        shares=100e6,
        ttm_net_income=400e6,
        balance_sheet_date=pd.Timestamp("2026-06-30"),
        ttm_basis="TTM2026-06-30",
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


class TestMarketRates:
    def test_coefficient_unchanged_at_or_below_seven_percent(self) -> None:
        assert MarketRates(7.0, 4.0).graham_number_coeff == 22.5

    def test_coefficient_cut_above_seven_percent(self) -> None:
        # playbook 11.6: 22.5 × 4.4 / 8.0
        assert MarketRates(8.0, 4.0).graham_number_coeff == pytest.approx(12.375)

    def test_epv_rate_rises_only_with_treasury_above_five(self) -> None:
        assert MarketRates(6.0, 4.2).epv_rate == pytest.approx(0.10)
        assert MarketRates(6.0, 5.34).epv_rate == pytest.approx(0.1034)


class TestEvaluateDefensive:
    def test_cheap_qualifying_company_is_a_buy(self) -> None:
        # price 30 ($3B cap) vs Graham Number ≈ 58.31 → MoS ≈ 48.6%
        r = evaluate("OK", _snap(), 30.0, INDUSTRY, RATES)
        assert r.status == "BUY"
        assert r.defensive_failures == ()
        assert r.valuation is not None
        assert r.valuation.category == "Defensive"
        assert r.valuation.intrinsic_value == pytest.approx(GN, abs=0.01)
        assert r.valuation.max_entry_price == pytest.approx(
            r.valuation.intrinsic_value * 0.67, abs=0.01
        )

    def test_qualifying_company_without_margin_of_safety_is_watch(self) -> None:
        # price 50 vs ≈ 58.31 → MoS ≈ 14.3% < 33%
        r = evaluate("OK", _snap(), 50.0, INDUSTRY, RATES)
        assert r.status == "WATCH"
        assert r.valuation is not None
        assert r.valuation.margin_of_safety == pytest.approx(1 - 50 / GN, abs=1e-4)

    def test_interrupted_dividend_fails_defensive(self) -> None:
        # Seaboard pattern: dividends paused 2013-2016, resumed 2017.
        divs = {y: 20e6 for y in range(2009, 2026) if not 2013 <= y <= 2016}
        r = evaluate("SEB", _snap(dividends=divs), 15.0, INDUSTRY, RATES)
        assert "dividend streak 9y" in r.defensive_failures
        assert "Defensive" not in r.qualifying

    def test_one_loss_in_ten_years_fails_defensive_and_anti_pattern(self) -> None:
        ni = _snap().net_income | {2019: -5e6}
        r = evaluate("LOSS", _snap(net_income=ni), 15.0, INDUSTRY, RATES)
        assert "not 10 years of positive earnings" in r.defensive_failures
        assert "#1 no 10 years of positive earnings" in r.anti_patterns

    def test_missing_year_is_not_treated_as_profitable(self) -> None:
        ni = {y: v for y, v in _snap().net_income.items() if y != 2020}
        r = evaluate("GAP", _snap(net_income=ni), 15.0, INDUSTRY, RATES)
        assert "not 10 years of positive earnings" in r.defensive_failures

    def test_small_company_fails_size_only(self) -> None:
        r = evaluate("SMALL", _snap(shares=10e6), 15.0, INDUSTRY, RATES)
        # 10M shares × $15 = $150M
        assert "size < $2B" in r.defensive_failures


class TestEvaluateOtherCategories:
    def test_enterprising_value_is_lower_of_earnings_and_tangible_book(self) -> None:
        # 10M shares × $150 = $1.5B fails Defensive on size. Per share
        # EPS_ttm = 40, NTA = 400; min(10 × 40, 1.2 × 400) = 400.
        r = evaluate("ENT", _snap(shares=10e6), 150.0, INDUSTRY, RATES)
        assert "size < $2B" in r.defensive_failures
        assert r.valuation is not None
        assert r.valuation.category == "Enterprising"
        assert r.valuation.intrinsic_value == pytest.approx(400.0)
        assert r.status == "BUY"  # MoS 62.5%

    def test_net_net_qualifies_below_two_thirds_of_ncav(self) -> None:
        # NCAV = 3,000 − 1,500 = 1,500M → $15/share; price 9 ≤ 10.
        snap = _snap(total_liabilities=1_500e6)
        r = evaluate("NN", snap, 9.0, INDUSTRY, RATES)
        assert "Net-Net" in r.qualifying
        assert r.net_net_failures == ()

    def test_net_net_requires_positive_ttm_earnings(self) -> None:
        r = evaluate("NN", _snap(ttm_net_income=-1e6), 3.0, INDUSTRY, RATES)
        assert "TTM EPS <= 0" in r.net_net_failures


class TestAntiPatterns:
    def test_debt_above_book_rejects_even_a_qualifying_company(self) -> None:
        snap = _snap(equity=300e6, long_term_debt=280e6, short_term_debt=50e6)
        r = evaluate("DEBT", snap, 1.0, INDUSTRY, RATES)
        assert "#8 debt exceeds book value" in r.anti_patterns
        assert r.status == "REJECT_ANTI_PATTERN"

    def test_excluded_sector_is_rejected(self) -> None:
        r = evaluate("BIO", _snap(), 30.0, "Health Care/Biotechnology: Pharmaceuticals", RATES)
        assert "#10 excluded sector" in r.anti_patterns
        assert r.status == "REJECT_ANTI_PATTERN"

    def test_never_paid_dividend(self) -> None:
        r = evaluate("NODIV", _snap(dividends={}), 15.0, INDUSTRY, RATES)
        assert "#2 never paid a dividend" in r.anti_patterns


def test_non_positive_price_raises() -> None:
    with pytest.raises(ValueError, match="price must be positive"):
        evaluate("X", _snap(), 0.0, INDUSTRY, RATES)


def test_dividend_streak_counts_back_from_last_year() -> None:
    assert dividend_streak({2023: 1.0, 2024: 1.0, 2025: 1.0}, 2025) == 3
    assert dividend_streak({2023: 1.0, 2025: 1.0}, 2025) == 1
    assert dividend_streak({}, 2025) == 0


class TestFixScaleErrors:
    def test_value_tagged_in_thousands_is_scaled(self) -> None:
        # UVV pattern: 95,047 reported where 95,047,000 was meant.
        series = {2017: 105e6, 2018: 104e6, 2019: 72e6, 2020: 87e6, 2021: 86_577.0}
        assert fix_scale_errors(series)[2021] == pytest.approx(86_577_000.0)

    def test_genuine_drop_is_left_alone(self) -> None:
        series = {2017: 105e6, 2018: 104e6, 2019: 72e6, 2020: 87e6, 2021: 1e6}
        assert fix_scale_errors(series)[2021] == 1e6

    def test_short_series_is_untouched(self) -> None:
        series = {2024: 100e6, 2025: 100.0}
        assert fix_scale_errors(series) == series


def test_is_near_miss() -> None:
    watch = evaluate("W", _snap(), 50.0, INDUSTRY, RATES)
    assert is_near_miss(watch)
    rejected = evaluate("R", _snap(), 15.0, "Technology/Computer Software", RATES)
    assert not is_near_miss(rejected)


# ---------------------------------------------------------------------------
# Extraction from XBRL facts
# ---------------------------------------------------------------------------
def _fact(
    concept: str,
    value: float,
    end: str,
    *,
    start: str | None = None,
    filed: str | None = None,
    unit: str = "USD",
) -> dict[str, object]:
    return {
        "concept": concept,
        "unit": unit,
        "value": value,
        "period_start": pd.Timestamp(start) if start else pd.NaT,
        "period_end": pd.Timestamp(end),
        "filed": pd.Timestamp(filed or end) + pd.Timedelta(days=40),
        "form": "10-K",
    }


def _facts(extra: list[dict[str, object]] | None = None, *, drop: str = "") -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year in range(2016, 2026):
        rows.append(_fact("NetIncomeLoss", 100e6, f"{year}-12-31", start=f"{year}-01-01"))
        rows.append(
            _fact("PaymentsOfDividendsCommonStock", 20e6, f"{year}-12-31", start=f"{year}-01-01")
        )
        rows.append(
            _fact(
                "WeightedAverageNumberOfDilutedSharesOutstanding",
                100e6,
                f"{year}-12-31",
                start=f"{year}-01-01",
                unit="shares",
            )
        )
    for concept, value in (
        ("AssetsCurrent", 1_500e6),
        ("LiabilitiesCurrent", 500e6),
        ("StockholdersEquity", 2_000e6),
        ("Liabilities", 1_000e6),
        ("LongTermDebtNoncurrent", 400e6),
    ):
        if concept != drop:
            rows.append(_fact(concept, value, "2025-12-31"))
    rows.extend(extra or [])
    return pd.DataFrame(rows)


class TestExtractSnapshot:
    def test_happy_path(self) -> None:
        snap = extract_snapshot(_facts())
        assert isinstance(snap, Snapshot)
        assert snap.net_income[2025] == 100e6
        assert snap.shares == 100e6
        assert snap.current_assets == 1_500e6
        assert snap.short_term_debt == 0.0
        assert snap.ttm_basis == "FY2025-12-31"

    def test_restated_year_takes_latest_filing(self) -> None:
        restated = _fact(
            "NetIncomeLoss", 90e6, "2025-12-31", start="2025-01-01", filed="2026-06-01"
        )
        assert annual_values(_facts([restated]), ("NetIncomeLoss",))[2025] == 90e6

    def test_quarterly_rows_are_not_mistaken_for_annual(self) -> None:
        quarter = _fact("NetIncomeLoss", 30e6, "2025-09-30", start="2025-07-01")
        assert annual_values(_facts([quarter]), ("NetIncomeLoss",))[2025] == 100e6

    def test_ttm_uses_ytd_against_prior_ytd(self) -> None:
        extra = [
            _fact("NetIncomeLoss", 60e6, "2026-06-30", start="2026-01-01"),
            _fact("NetIncomeLoss", 50e6, "2025-06-30", start="2025-01-01"),
        ]
        snap = extract_snapshot(_facts(extra))
        assert isinstance(snap, Snapshot)
        assert snap.ttm_net_income == pytest.approx(110e6)
        assert snap.ttm_basis == "TTM2026-06-30"

    def test_missing_current_assets_is_insufficient(self) -> None:
        assert "no current assets" in str(extract_snapshot(_facts(drop="AssetsCurrent")))

    def test_stale_balance_sheet_is_insufficient(self) -> None:
        late = _fact("NetIncomeLoss", 1.0, "2027-12-31", start="2027-01-01")
        assert "stale balance sheet" in str(extract_snapshot(_facts([late])))

    def test_equity_falls_back_to_total_including_nci(self) -> None:
        nci = _fact(
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            2_100e6,
            "2025-12-31",
        )
        snap = extract_snapshot(_facts([nci], drop="StockholdersEquity"))
        assert isinstance(snap, Snapshot)
        assert snap.equity == 2_100e6

    def test_empty_facts(self) -> None:
        assert extract_snapshot(pd.DataFrame()) == "no XBRL facts"

    def test_audit_wraps_bad_data_as_insufficient(self) -> None:
        r = audit("X", _facts(drop="AssetsCurrent"), 10.0, INDUSTRY, RATES)
        assert r.status == "INSUFFICIENT_DATA"
        assert r.valuation is None


class TestSellTarget:
    def test_defensive_sells_at_graham_number(self) -> None:
        assert sell_target("Defensive", 58.31, 40.0) == (58.31, "Graham Number")

    def test_net_net_sells_at_ncav(self) -> None:
        assert sell_target("Net-Net", 15.0, None) == (15.0, "NCAV")

    def test_enterprising_sells_at_epv_not_the_buy_bound(self) -> None:
        assert sell_target("Enterprising", 34.0, 40.0) == (40.0, "EPV")

    def test_enterprising_without_epv_falls_back_to_buy_bound(self) -> None:
        assert sell_target("Enterprising", 34.0, None)[0] == 34.0

    def test_evaluate_reports_target_and_upside(self) -> None:
        r = evaluate("OK", _snap(), 30.0, INDUSTRY, RATES)
        assert r.valuation is not None
        assert r.valuation.sell_target == pytest.approx(GN, abs=0.01)
        assert r.valuation.upside_to_target == pytest.approx(GN / 30 - 1, abs=1e-3)

    def test_enterprising_target_differs_from_buy_value(self) -> None:
        r = evaluate("ENT", _snap(shares=10e6), 150.0, INDUSTRY, RATES)
        assert r.valuation is not None
        assert r.valuation.sell_target_method == "EPV"
        assert r.valuation.sell_target == pytest.approx(r.metrics["epv"])
        assert r.valuation.sell_target != r.valuation.intrinsic_value

    def test_price_above_target_gives_negative_upside(self) -> None:
        # A short dividend record fails Defensive, leaving Enterprising, whose
        # target is EPV ≈ 355.6 here. Defensive itself cannot exceed its target:
        # P/E ≤ 15 and P/B ≤ 1.5 already cap the price at the Graham Number.
        snap = _snap(shares=10e6, dividends={2025: 20e6})
        r = evaluate("HIGH", snap, 400.0, INDUSTRY, RATES)
        assert r.valuation is not None
        assert r.valuation.category == "Enterprising"
        assert r.valuation.upside_to_target < 0
        assert "already above" in r.thesis_en

    def test_negative_tangible_book_does_not_qualify_as_enterprising(self) -> None:
        # Intangibles exceed equity, so min(10 x EPS, 1.2 x NTA) is negative;
        # the margin-of-safety formula would otherwise report a huge "safety".
        r = evaluate("NEG", _snap(goodwill=5_000e6), 5.0, INDUSTRY, RATES)
        assert "Enterprising" not in r.qualifying


class TestExitTriggers:
    def test_net_net_adds_the_two_year_rule(self) -> None:
        en, he = exit_triggers("Net-Net", 15.0)
        assert any("Two years" in t for t in en)
        assert len(en) == len(he) == 3

    def test_other_categories_have_no_time_rule(self) -> None:
        en, he = exit_triggers("Defensive", 58.31)
        assert not any("Two years" in t for t in en)
        assert en[0] == "Price is at or above 58.31"
        assert len(he) == 2


class TestThesis:
    def test_buy_thesis_states_the_figures_in_both_languages(self) -> None:
        r = evaluate("OK", _snap(), 30.0, INDUSTRY, RATES)
        assert r.status == "BUY"
        assert "OK qualifies as Defensive" in r.thesis_en
        assert "passes every quantitative gate" in r.thesis_en
        assert "OK עומדת בקטגוריית Defensive" in r.thesis_he
        assert r.valuation is not None
        for text in (r.thesis_en, r.thesis_he):
            assert f"{r.valuation.sell_target:.2f}" in text

    def test_watch_thesis_gives_the_entry_price(self) -> None:
        r = evaluate("OK", _snap(), 50.0, INDUSTRY, RATES)
        assert r.status == "WATCH"
        assert r.valuation is not None
        assert f"below {r.valuation.max_entry_price:.2f}" in r.thesis_en

    def test_rejected_thesis_names_the_anti_pattern(self) -> None:
        r = evaluate("BIO", _snap(), 30.0, "Health Care/Biotechnology", RATES)
        assert r.status == "REJECT_ANTI_PATTERN"
        assert "#10 excluded sector" in r.thesis_en

    def test_epv_below_price_is_flagged_for_defensive(self) -> None:
        # EPS 5y avg ≈ 3.44 at 10% → EPV ≈ 34.4, below a price of 50.
        r = evaluate("OK", _snap(), 50.0, INDUSTRY, RATES)
        assert "Caution: earnings power value" in r.thesis_en
        assert "זהירות" in r.thesis_he

    def test_thesis_always_discloses_it_is_quantitative_only(self) -> None:
        r = evaluate("OK", _snap(), 30.0, INDUSTRY, RATES)
        assert "Quantitative only" in r.thesis_en
        assert "כמותי בלבד" in r.thesis_he

    def test_infinite_ratios_render_as_not_available(self) -> None:
        r = evaluate("OK", _snap(), 30.0, INDUSTRY, RATES)
        assert r.valuation is not None
        en, _ = build_thesis(
            ticker="X",
            price=9.0,
            valuation=r.valuation,
            status="WATCH",
            pe3=math.inf,
            pb=1.0,
            current_ratio=2.0,
            dividend_years=0,
            epv=None,
            anti_patterns=(),
        )
        assert "P/E n/a" in en

    def test_no_category_has_no_thesis(self) -> None:
        r = evaluate("NONE", _snap(current_liabilities=3_000e6), 500.0, INDUSTRY, RATES)
        assert r.valuation is None
        assert r.thesis_en == ""
        assert r.exit_triggers_en == ()
