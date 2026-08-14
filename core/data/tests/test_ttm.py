"""The TTM assembler must be right about periods it was never handed.

Every test builds its facts explicitly. The shapes here are the ones
real filers actually produce — discrete quarters, year-to-date only, a
fiscal year with no Q4 10-Q behind it, 53-week years, restatements — and
each is a case where the naive "sum the last four quarters" returns
either nothing or a wrong number silently.
"""

from __future__ import annotations

from datetime import date, timedelta

from core.data.edgar_facts import XbrlFact
from core.data.ttm import (
    MIN_SEGMENT_DAYS,
    TTM_SPAN_DAYS,
    trailing_twelve_months,
)

REV = "RevenueFromContractWithCustomerExcludingAssessedTax"
REV_ALT = "Revenues"


def fact(
    start: date | None,
    end: date,
    value: float,
    *,
    filed: date | None = None,
    concept: str = REV,
    unit: str = "USD",
    form: str = "10-Q",
    namespace: str = "us-gaap",
) -> XbrlFact:
    return XbrlFact(
        concept=concept,
        namespace=namespace,
        unit=unit,
        value=value,
        period_start=start,
        period_end=end,
        # Filers publish roughly a month after the period closes.
        filed=filed or end + timedelta(days=30),
        form=form,
        fiscal_year=end.year,
        fiscal_period=None,
        accession_number=f"acc-{end}-{value}",
    )


def quarters(year: int, values: list[float], **kw) -> list[XbrlFact]:
    """Four discrete calendar quarters."""
    bounds = [
        (date(year, 1, 1), date(year, 3, 31)),
        (date(year, 4, 1), date(year, 6, 30)),
        (date(year, 7, 1), date(year, 9, 30)),
        (date(year, 10, 1), date(year, 12, 31)),
    ]
    return [fact(s, e, v, **kw) for (s, e), v in zip(bounds, values, strict=True)]


# ------------------------------------------------------------ four quarters


class TestDiscreteQuarters:
    def test_sums_four_quarters(self) -> None:
        facts = quarters(2025, [10, 20, 30, 40])
        r = trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV])
        assert r is not None
        assert r.value == 100
        assert (r.period_start, r.period_end) == (date(2025, 1, 1), date(2025, 12, 31))
        assert len(r.segments) == 4

    def test_the_window_moves_with_as_of(self) -> None:
        facts = quarters(2025, [10, 20, 30, 40]) + quarters(2026, [50, 0, 0, 0])[:1]
        r = trailing_twelve_months(facts, date(2026, 6, 1), concepts=[REV])
        assert r is not None
        # Q1-2026 replaces Q1-2025: 20+30+40+50.
        assert r.value == 140
        assert r.period_end == date(2026, 3, 31)

    def test_three_quarters_is_not_a_year(self) -> None:
        facts = quarters(2025, [10, 20, 30, 40])[:3]
        assert trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV]) is None


# ------------------------------------------------------------- fiscal years


class TestAnnual:
    def test_a_bare_fiscal_year_is_already_twelve_months(self) -> None:
        facts = [fact(date(2025, 1, 1), date(2025, 12, 31), 100.0, form="10-K")]
        r = trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV])
        assert r is not None
        assert r.value == 100.0
        assert len(r.segments) == 1

    def test_a_year_is_preferred_to_the_quarters_that_make_it(self) -> None:
        """Fewer components means fewer boundary assumptions."""
        facts = [
            *quarters(2025, [10, 20, 30, 40]),
            fact(date(2025, 1, 1), date(2025, 12, 31), 100.0, form="10-K"),
        ]
        r = trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV])
        assert r is not None
        assert len(r.segments) == 1
        assert r.value == 100.0

    def test_a_fifty_three_week_year_is_accepted(self) -> None:
        # 371 days — a real filing shape that a strict 365 rejects.
        start, end = date(2025, 1, 1), date(2026, 1, 6)
        facts = [fact(start, end, 100.0, form="10-K")]
        r = trailing_twelve_months(facts, date(2026, 4, 1), concepts=[REV])
        assert r is not None
        assert r.days == 371
        assert TTM_SPAN_DAYS[0] <= r.days <= TTM_SPAN_DAYS[1]

    def test_a_nine_month_period_alone_is_not_a_year(self) -> None:
        facts = [fact(date(2025, 1, 1), date(2025, 9, 30), 75.0)]
        assert trailing_twelve_months(facts, date(2026, 1, 1), concepts=[REV]) is None


# ------------------------------------------------------- year-to-date filers


class TestYearToDate:
    """The shape that makes 'sum four quarters' impossible."""

    @staticmethod
    def _ytd_filer() -> list[XbrlFact]:
        """A filer that tags year-to-date only, never a discrete quarter.

        There are no four quarters here to add. Every quarter in the
        window has to be derived by subtracting one YTD from the next.
        """
        return [
            fact(date(2025, 1, 1), date(2025, 3, 31), 20.0),
            fact(date(2025, 1, 1), date(2025, 6, 30), 45.0),
            fact(date(2025, 1, 1), date(2025, 9, 30), 72.0),
            fact(date(2025, 1, 1), date(2025, 12, 31), 100.0, form="10-K"),
            fact(date(2026, 1, 1), date(2026, 3, 31), 30.0),
            fact(date(2026, 1, 1), date(2026, 6, 30), 65.0),
            fact(date(2026, 1, 1), date(2026, 9, 30), 96.0),
        ]

    def test_derives_every_quarter_it_needs(self) -> None:
        r = trailing_twelve_months(
            self._ytd_filer(), date(2026, 11, 1), concepts=[REV]
        )
        assert r is not None
        assert r.period_end == date(2026, 9, 30)
        # Twelve months to 2026-09-30 = Q4-2025 (100−72=28) + 9M-2026 (96).
        assert r.value == 124.0
        assert r.uses_derived

    def test_it_falls_back_when_the_newest_end_cannot_be_reached(self) -> None:
        """A readable earlier window beats returning nothing.

        Drop the 2025 interim filings and Q4-2025 can no longer be
        derived, so no window ends at 2026-09-30. The twelve months to
        2025-12-31 are still perfectly readable and must be returned —
        a screen that answers None for a company it can price is a bug
        wearing a null.
        """
        facts = [f for f in self._ytd_filer() if f.form == "10-K" or f.period_end.year == 2026]
        r = trailing_twelve_months(facts, date(2026, 11, 1), concepts=[REV])
        assert r is not None
        assert r.period_end == date(2025, 12, 31)
        assert r.value == 100.0
        assert TTM_SPAN_DAYS[0] <= r.days <= TTM_SPAN_DAYS[1]

    def test_q4_is_derived_from_the_year_minus_nine_months(self) -> None:
        """The canonical case: almost nobody files a Q4 10-Q."""
        facts = [
            fact(date(2025, 1, 1), date(2025, 3, 31), 10.0),
            fact(date(2025, 1, 1), date(2025, 6, 30), 25.0),
            fact(date(2025, 1, 1), date(2025, 9, 30), 45.0),
            fact(date(2025, 1, 1), date(2025, 12, 31), 70.0, form="10-K"),
            fact(date(2026, 1, 1), date(2026, 3, 31), 15.0),
        ]
        r = trailing_twelve_months(facts, date(2026, 6, 1), concepts=[REV])
        assert r is not None
        assert r.period_end == date(2026, 3, 31)
        # Twelve months to 2026-03-31 = FY2025 (70) − Q1-2025 (10) + Q1-2026 (15).
        assert r.value == 75.0
        assert r.uses_derived

    def test_a_derived_segment_records_the_later_filing_date(self) -> None:
        """A derived quarter is knowable only once both halves are filed."""
        facts = [
            fact(
                date(2025, 1, 1), date(2025, 9, 30), 45.0, filed=date(2025, 11, 1)
            ),
            fact(
                date(2025, 1, 1),
                date(2025, 12, 31),
                70.0,
                filed=date(2026, 2, 20),
                form="10-K",
            ),
        ]
        r = trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV])
        assert r is not None
        assert r.filed == date(2026, 2, 20)

    def test_a_stub_subtraction_is_discarded(self) -> None:
        """Boundary drift must not become a two-day 'quarter'."""
        facts = [
            fact(date(2025, 1, 1), date(2025, 12, 29), 99.0, form="10-K"),
            fact(date(2025, 1, 1), date(2025, 12, 31), 100.0, form="10-K"),
        ]
        r = trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV])
        assert r is not None
        # It picks one of the years whole; it does not build a window
        # out of a year plus a 2-day sliver.
        assert len(r.segments) == 1
        assert all(s.days >= MIN_SEGMENT_DAYS for s in r.segments)


# ------------------------------------------------------------- point in time


class TestPointInTime:
    def test_a_fact_filed_after_as_of_is_invisible(self) -> None:
        """Apple's FY2025 ended 2025-09-27 and was filed 2025-10-31."""
        facts = [
            fact(
                date(2024, 9, 29),
                date(2025, 9, 27),
                100.0,
                filed=date(2025, 10, 31),
                form="10-K",
            )
        ]
        assert (
            trailing_twelve_months(facts, date(2025, 10, 1), concepts=[REV]) is None
        )
        assert trailing_twelve_months(facts, date(2025, 11, 1), concepts=[REV])

    def test_a_restatement_wins_once_it_is_filed(self) -> None:
        original = fact(
            date(2025, 1, 1), date(2025, 12, 31), 100.0,
            filed=date(2026, 2, 1), form="10-K",
        )
        restated = fact(
            date(2025, 1, 1), date(2025, 12, 31), 80.0,
            filed=date(2026, 8, 1), form="10-K",
        )
        facts = [original, restated]
        before = trailing_twelve_months(facts, date(2026, 5, 1), concepts=[REV])
        after = trailing_twelve_months(facts, date(2026, 9, 1), concepts=[REV])
        assert before is not None and before.value == 100.0
        assert after is not None and after.value == 80.0


# ------------------------------------------------------------ concept chains


class TestConceptChains:
    def test_the_preferred_concept_wins_for_the_same_period(self) -> None:
        facts = [
            fact(date(2025, 1, 1), date(2025, 12, 31), 100.0, form="10-K"),
            fact(
                date(2025, 1, 1), date(2025, 12, 31), 111.0,
                concept=REV_ALT, form="10-K",
            ),
        ]
        r = trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV, REV_ALT])
        assert r is not None and r.value == 100.0

    def test_a_period_only_the_fallback_covers_is_still_used(self) -> None:
        """Per-period resolution: NVIDIA's ASC 606 tag stops in 2022."""
        facts = [
            *quarters(2025, [10, 20, 30, 0])[:3],
            fact(date(2025, 10, 1), date(2025, 12, 31), 40.0, concept=REV_ALT),
        ]
        r = trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV, REV_ALT])
        assert r is not None
        assert r.value == 100.0
        assert set(r.concepts) == {REV, REV_ALT}

    def test_subtraction_never_crosses_concepts(self) -> None:
        """Revenues minus RevenueFromContract is not a quarter."""
        facts = [
            fact(date(2025, 1, 1), date(2025, 9, 30), 45.0, concept=REV_ALT),
            fact(date(2025, 1, 1), date(2025, 12, 31), 70.0, form="10-K"),
            fact(date(2026, 1, 1), date(2026, 3, 31), 15.0),
        ]
        # A cross-concept Q4 of 70-45=25 would let a 12-month window to
        # 2026-03-31 be built. It must not be.
        r = trailing_twelve_months(facts, date(2026, 6, 1), concepts=[REV, REV_ALT])
        assert r is None or r.period_end != date(2026, 3, 31)

    def test_an_unlisted_concept_is_ignored(self) -> None:
        facts = quarters(2025, [10, 20, 30, 40], concept="SomethingElse")
        assert trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV]) is None


# ---------------------------------------------------------------- rejections


class TestRejections:
    def test_a_foreign_currency_is_rejected_not_mixed(self) -> None:
        """A CAD filer lands ~25% cheap on every USD multiple."""
        facts = quarters(2025, [10, 20, 30, 40], unit="CAD")
        assert trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV]) is None

    def test_an_instant_fact_is_not_a_flow(self) -> None:
        facts = [fact(None, date(2025, 12, 31), 100.0, form="10-K")]
        assert trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV]) is None

    def test_a_form_outside_the_list_is_ignored(self) -> None:
        facts = quarters(2025, [10, 20, 30, 40], form="8-K")
        assert trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV]) is None

    def test_no_facts_is_none_not_zero(self) -> None:
        assert trailing_twelve_months([], date(2026, 3, 1), concepts=[REV]) is None

    def test_a_gap_year_does_not_stitch(self) -> None:
        """Q1-2025 and Q2-2026 are not consecutive."""
        facts = [
            *quarters(2025, [10, 20, 0, 0])[:2],
            *quarters(2026, [0, 0, 30, 40])[2:],
        ]
        assert trailing_twelve_months(facts, date(2027, 3, 1), concepts=[REV]) is None


# ------------------------------------------------------------------- results


class TestResultShape:
    def test_negative_values_are_carried_not_dropped(self) -> None:
        """A loss-making quarter is data, not an error."""
        facts = quarters(2025, [10, -30, 20, 15])
        r = trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV])
        assert r is not None and r.value == 15.0

    def test_segments_are_returned_in_time_order(self) -> None:
        r = trailing_twelve_months(
            quarters(2025, [10, 20, 30, 40]), date(2026, 3, 1), concepts=[REV]
        )
        assert r is not None
        ends = [s.end for s in r.segments]
        assert ends == sorted(ends)

    def test_a_clean_annual_is_not_marked_derived(self) -> None:
        facts = [fact(date(2025, 1, 1), date(2025, 12, 31), 100.0, form="10-K")]
        r = trailing_twelve_months(facts, date(2026, 3, 1), concepts=[REV])
        assert r is not None and not r.uses_derived
