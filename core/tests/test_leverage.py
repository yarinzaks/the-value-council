"""Tests for the shared debt-to-equity rule.

The rule has to distinguish two cases that look identical in the data:
a company with no debt, and a company whose debt was never tagged.
Getting it wrong in either direction is costly — scoring both as zero
leverage passes 37% of the universe on no evidence, and rejecting both
ejects Texas Pacific Land and Snowflake.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import pytest
from loguru import logger as loguru_logger

from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.scoring.leverage import (
    debt_to_equity,
    has_complete_balance_sheet,
    reported_debt,
)


@contextmanager
def _logged(level: str) -> Iterator[list[str]]:
    """What the project's loguru logger wrote at ``level`` or above.

    ``caplog`` cannot see loguru, which does not propagate to stdlib
    logging; a sink can.
    """
    messages: list[str] = []
    sink_id = loguru_logger.add(
        lambda m: messages.append(m.record["message"]), level=level
    )
    try:
        yield messages
    finally:
        loguru_logger.remove(sink_id)


def _fin(
    *,
    total_equity: float | None = 1_000.0,
    total_debt: float | None = None,
    long_term_debt: float | None = None,
    total_assets: float | None = 2_000.0,
    total_liabilities: float | None = 1_000.0,
    current_liabilities: float | None = 400.0,
) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker="TEST",
        as_of=date(2026, 8, 4),
        source_filing=FilingMetadata(
            ticker="TEST",
            cik="1",
            form_type="10-K",
            filing_date=date(2026, 2, 15),
            period_of_report=date(2025, 12, 31),
            accession_number="acc-1",
        ),
        total_equity=total_equity,
        total_debt=total_debt,
        long_term_debt=long_term_debt,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        current_liabilities=current_liabilities,
    )


class TestReportedDebt:
    def test_prefers_total_debt(self) -> None:
        assert reported_debt(_fin(total_debt=300.0, long_term_debt=200.0)) == 300.0

    def test_falls_back_to_long_term(self) -> None:
        assert reported_debt(_fin(total_debt=None, long_term_debt=200.0)) == 200.0

    def test_none_when_neither_present(self) -> None:
        assert reported_debt(_fin()) is None

    def test_a_reported_zero_is_not_absence(self) -> None:
        assert reported_debt(_fin(total_debt=0.0)) == 0.0


class TestBalanceSheetCompleteness:
    def test_complete(self) -> None:
        assert has_complete_balance_sheet(_fin())

    @pytest.mark.parametrize(
        "missing", ["total_assets", "total_liabilities", "current_liabilities"]
    )
    def test_any_missing_field_makes_it_incomplete(self, missing: str) -> None:
        assert not has_complete_balance_sheet(_fin(**{missing: None}))


class TestDebtToEquity:
    def test_basic_ratio(self) -> None:
        assert debt_to_equity(_fin(total_debt=250.0)) == pytest.approx(0.25)

    def test_none_input(self) -> None:
        assert debt_to_equity(None) is None

    def test_missing_equity_is_undefined(self) -> None:
        assert debt_to_equity(_fin(total_equity=None, total_debt=100.0)) is None

    def test_negative_equity_is_undefined(self) -> None:
        assert debt_to_equity(_fin(total_equity=-500.0, total_debt=100.0)) is None

    def test_debt_free_company_scores_zero(self) -> None:
        # The Texas Pacific Land / Snowflake case: a full balance sheet
        # and no debt concept means no debt.
        assert debt_to_equity(_fin()) == 0.0

    def test_sparse_filer_is_undefined_not_zero(self) -> None:
        # The Boston Scientific / Expeditors case: these plainly carry
        # debt, and were being scored 0.00 — the best possible mark.
        assert (
            debt_to_equity(
                _fin(
                    total_assets=None,
                    total_liabilities=None,
                    current_liabilities=None,
                )
            )
            is None
        )

    def test_one_missing_balance_sheet_field_is_enough_to_abstain(self) -> None:
        assert debt_to_equity(_fin(current_liabilities=None)) is None

    def test_explicitly_reported_zero_debt_scores_zero(self) -> None:
        # Distinct from absence: the filer said zero.
        assert debt_to_equity(_fin(total_debt=0.0)) == 0.0

    def test_reported_zero_survives_a_sparse_balance_sheet(self) -> None:
        assert (
            debt_to_equity(
                _fin(
                    total_debt=0.0,
                    total_assets=None,
                    total_liabilities=None,
                    current_liabilities=None,
                )
            )
            == 0.0
        )

    def test_highly_levered_company(self) -> None:
        assert debt_to_equity(_fin(total_debt=3_000.0)) == pytest.approx(3.0)

    def test_high_liab_eq_without_debt_tag_returns_none(self) -> None:
        # Audit FUND-4, HRI at FY2025: a full balance sheet, no debt
        # concept the composer read, and liabilities 6.1x equity. It was
        # scored D/E 0.00 and bought on it; it carries ~$8bn of debt.
        hri = _fin(
            total_equity=1_898.0,
            total_assets=13_564.0,
            total_liabilities=11_666.0,
            current_liabilities=628.0,
        )
        assert debt_to_equity(hri) is None

    def test_liab_eq_exactly_at_the_threshold_abstains(self) -> None:
        assert debt_to_equity(_fin(total_liabilities=1_500.0)) is None

    def test_liab_eq_just_under_the_threshold_reads_as_debt_free(self) -> None:
        assert debt_to_equity(_fin(total_liabilities=1_499.99)) == 0.0

    def test_liab_eq_just_over_the_threshold_abstains(self) -> None:
        assert debt_to_equity(_fin(total_liabilities=1_500.01)) is None

    def test_reported_debt_on_a_levered_sheet_is_still_scored(self) -> None:
        # The guard is only for absence; a tagged figure is used as-is.
        assert debt_to_equity(
            _fin(total_debt=2_000.0, total_liabilities=6_000.0)
        ) == pytest.approx(2.0)


class TestImplausiblyLowDebt:
    """A tagged figure that is a sliver of a heavily levered balance
    sheet may be a fragment of the debt read from the one element the
    composer found (CRTO's $37M of current notes). It may also be a bank
    or insurer whose liabilities are deposits and reserves. The 5% floor
    is uncalibrated, so it only flags: D/E is still computed from the
    reported debt, never ``None`` (which every entry filter rejects)."""

    def test_sliver_of_debt_on_a_levered_sheet_is_scored_and_flagged(
        self,
    ) -> None:
        with _logged("WARNING") as messages:
            ratio = debt_to_equity(
                _fin(total_debt=10.0, total_liabilities=6_000.0)
            )
        assert ratio == pytest.approx(0.01)
        assert len(messages) == 1
        assert "TEST" in messages[0]
        assert "0.17%" in messages[0]  # 10 / 6,000 of liabilities

    def test_reported_zero_on_a_levered_sheet_is_scored_and_flagged(
        self,
    ) -> None:
        with _logged("WARNING") as messages:
            ratio = debt_to_equity(
                _fin(total_debt=0.0, total_liabilities=6_000.0)
            )
        assert ratio == 0.0
        assert len(messages) == 1
        assert "TEST" in messages[0]

    def test_plausible_debt_is_not_flagged(self) -> None:
        with _logged("WARNING") as messages:
            debt_to_equity(_fin(total_debt=300.0, total_liabilities=6_000.0))
        assert messages == []

    def test_small_debt_on_an_unlevered_sheet_is_scored(self) -> None:
        assert debt_to_equity(
            _fin(total_debt=10.0, total_liabilities=1_000.0)
        ) == pytest.approx(0.01)

    def test_debt_at_the_minimum_share_is_scored(self) -> None:
        # 5% of 6,000 = 300: plausible, scored as reported.
        assert debt_to_equity(
            _fin(total_debt=300.0, total_liabilities=6_000.0)
        ) == pytest.approx(0.3)

    def test_low_debt_without_liabilities_is_scored(self) -> None:
        assert debt_to_equity(
            _fin(total_debt=10.0, total_liabilities=None)
        ) == pytest.approx(0.01)


class TestAgentsShareTheRule:
    """All nine agents must resolve to this implementation — the rule is
    a data-quality judgement, not a per-investor doctrine."""

    def test_every_agent_delegates(self) -> None:
        import importlib

        for agent in (
            "buffett", "dreman", "fisher", "graham", "klarman",
            "lynch", "marks", "neff", "schloss",
        ):
            mod = importlib.import_module(f"agents.{agent}.filters")
            sparse = _fin(
                total_assets=None,
                total_liabilities=None,
                current_liabilities=None,
            )
            assert mod.debt_to_equity(sparse) is None, agent
            assert mod.debt_to_equity(_fin()) == 0.0, agent
