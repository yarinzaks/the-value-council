"""When each company is next expected to file, and who to refresh first.

The cache refresh re-pulls every active filer once a week regardless of
whether anything was filed. That is both wasteful and late: a company
that files on Tuesday is read from a cache up to six days stale, and the
thousands that filed nothing are re-fetched anyway.

A company's reporting rhythm is knowable from its own filing history —
the fiscal year end sits in the period covered by its last 10-K, and the
gap between a period ending and the filing that reports it is stable
enough per company to predict the next one within days.

What is *not* knowable is the exact date. Companies do not publish next
year's 10-Q dates in advance, so every function here returns a window
and a deadline rather than a day, and the actual filing is confirmed by
observation. Anything that claimed a precise date a year out would be
inventing it.
"""

from __future__ import annotations

from datetime import date

from core.data.fiscal_calendar import (
    QUARTERLY_DEADLINE_DAYS,
    Filing,
    FiscalProfile,
    build_profile,
    due_for_refresh,
    next_expected,
)


def _quarterly_history(
    *, year_end_month: int = 12, year_end_day: int = 31, lag_days: int = 35
) -> list[Filing]:
    """Four quarters and a year, each filed ``lag_days`` after it ended."""
    from datetime import timedelta

    out: list[Filing] = []
    ends = [
        date(2025, year_end_month, year_end_day),
        date(2026, 3, 31),
        date(2026, 6, 30),
    ]
    for i, end in enumerate(ends):
        form = "10-K" if i == 0 else "10-Q"
        out.append(
            Filing(form=form, period_end=end, filed=end + timedelta(days=lag_days))
        )
    return out


class TestBuildProfile:
    def test_the_fiscal_year_end_comes_from_the_annual_report(self) -> None:
        profile = build_profile("AAA", _quarterly_history())
        assert profile is not None
        assert profile.fiscal_year_end == (12, 31)

    def test_a_non_calendar_year_end_is_read_correctly(self) -> None:
        """Retailers ending in January are the case this exists for."""
        profile = build_profile(
            "BBB", _quarterly_history(year_end_month=1, year_end_day=31)
        )
        assert profile is not None
        assert profile.fiscal_year_end == (1, 31)

    def test_the_lag_is_the_median_of_what_the_company_actually_does(self) -> None:
        profile = build_profile("AAA", _quarterly_history(lag_days=42))
        assert profile is not None
        assert profile.median_lag_days["10-Q"] == 42

    def test_one_late_quarter_does_not_move_the_median(self) -> None:
        """A median, not a mean, because one restatement is not a rhythm."""
        from datetime import timedelta

        history = _quarterly_history(lag_days=35)
        history.append(
            Filing(
                form="10-Q",
                period_end=date(2025, 9, 30),
                filed=date(2025, 9, 30) + timedelta(days=200),
            )
        )
        history.append(
            Filing(
                form="10-Q",
                period_end=date(2025, 6, 30),
                filed=date(2025, 6, 30) + timedelta(days=35),
            )
        )
        profile = build_profile("AAA", history)
        assert profile is not None
        assert profile.median_lag_days["10-Q"] == 35

    def test_no_history_is_none_rather_than_a_guess(self) -> None:
        assert build_profile("EMPTY", []) is None

    def test_quarterly_history_without_an_annual_is_none(self) -> None:
        """Without a 10-K there is no fiscal year end to anchor on."""
        history = [
            f for f in _quarterly_history() if f.form != "10-K"
        ]
        assert build_profile("AAA", history) is None

    def test_the_latest_filing_of_each_form_is_recorded(self) -> None:
        profile = build_profile("AAA", _quarterly_history())
        assert profile is not None
        assert profile.last_period_end["10-Q"] == date(2026, 6, 30)
        assert profile.last_period_end["10-K"] == date(2025, 12, 31)


class TestNextExpected:
    def test_the_next_quarter_follows_the_last_one_reported(self) -> None:
        profile = build_profile("AAA", _quarterly_history())
        assert profile is not None
        expected = next_expected(profile, "10-Q", after=date(2026, 8, 1))
        assert expected is not None
        assert expected.period_end == date(2026, 9, 30)

    def test_the_expected_date_uses_the_company_s_own_lag(self) -> None:
        profile = build_profile("AAA", _quarterly_history(lag_days=40))
        assert profile is not None
        expected = next_expected(profile, "10-Q", after=date(2026, 8, 1))
        assert expected is not None
        assert expected.expected_filing == date(2026, 9, 30) + __import__(
            "datetime"
        ).timedelta(days=40)

    def test_the_deadline_is_statutory_not_habitual(self) -> None:
        """A company that usually files early may still file on the last day.

        The deadline is what bounds "overdue"; the habit only bounds
        "expected". Confusing the two would mark a merely-slower-than-
        usual filer as late.
        """
        profile = build_profile("AAA", _quarterly_history(lag_days=25))
        assert profile is not None
        expected = next_expected(profile, "10-Q", after=date(2026, 8, 1))
        assert expected is not None
        assert (expected.deadline - expected.period_end).days == QUARTERLY_DEADLINE_DAYS

    def test_a_non_calendar_year_end_produces_its_own_quarters(self) -> None:
        """January year end means quarters ending Apr / Jul / Oct / Jan."""
        profile = build_profile(
            "BBB", _quarterly_history(year_end_month=1, year_end_day=31)
        )
        assert profile is not None
        expected = next_expected(profile, "10-Q", after=date(2026, 8, 1))
        assert expected is not None
        assert expected.period_end.month in (7, 10)

    def test_an_unknown_form_is_none(self) -> None:
        profile = build_profile("AAA", _quarterly_history())
        assert profile is not None
        assert next_expected(profile, "8-K", after=date(2026, 8, 1)) is None


class TestDueForRefresh:
    """Who to re-pull, and in what order."""

    def test_a_company_whose_window_has_opened_is_due(self) -> None:
        profile = build_profile("AAA", _quarterly_history(lag_days=35))
        assert profile is not None
        # Q3 ends 2026-09-30, expected 2026-11-04.
        assert due_for_refresh(profile, as_of=date(2026, 11, 5))

    def test_a_company_still_inside_its_quarter_is_not(self) -> None:
        profile = build_profile("AAA", _quarterly_history(lag_days=35))
        assert profile is not None
        assert not due_for_refresh(profile, as_of=date(2026, 8, 15))

    def test_the_cache_already_holding_the_period_settles_it(self) -> None:
        """Once the filing is in the cache, the company is not due again.

        This is the half that stops the calendar from re-fetching the
        same company every day for the rest of the quarter once its
        expected date has passed.
        """
        profile = build_profile("AAA", _quarterly_history(lag_days=35))
        assert profile is not None
        # Its last 10-Q covers 2026-06-30; ask before Q3 was due.
        assert not due_for_refresh(profile, as_of=date(2026, 7, 15))

    def test_an_annual_report_is_not_expected_every_quarter(self) -> None:
        """The bug this test was written for.

        The next-period search used quarter ends for both forms, so a
        December filer looked due for a 10-K every March. That made
        every company permanently due and turned the calendar into a
        no-op wearing a schedule — the failure mode where the code runs,
        the tests are green, and nothing is actually being decided.
        """
        profile = build_profile("AAA", _quarterly_history(lag_days=35))
        assert profile is not None
        expected = next_expected(profile, "10-K", after=date(2026, 7, 15))
        assert expected is not None
        assert expected.period_end == date(2026, 12, 31)

    def test_an_overdue_filing_keeps_the_company_due(self) -> None:
        """Late is exactly when refreshing matters most.

        An earlier version skipped any period whose expected date had
        passed and pointed at the one after it, so a company three weeks
        late read as "nothing due for three months".
        """
        profile = build_profile("AAA", _quarterly_history(lag_days=35))
        assert profile is not None
        expected = next_expected(profile, "10-Q", after=date(2026, 12, 1))
        assert expected is not None
        assert expected.period_end == date(2026, 9, 30)
        assert due_for_refresh(profile, as_of=date(2026, 12, 1))

    def test_a_profile_that_could_not_be_built_is_always_due(self) -> None:
        """Unknown rhythm means fall back to refreshing it.

        The calendar is an optimisation. A company it cannot model must
        keep being fetched on the old schedule, never dropped.
        """
        assert due_for_refresh(None, as_of=date(2026, 8, 15))


class TestFiscalProfileValue:
    def test_a_profile_is_serialisable(self) -> None:
        profile = build_profile("AAA", _quarterly_history())
        assert profile is not None
        restored = FiscalProfile.from_dict(profile.to_dict())
        assert restored == profile
