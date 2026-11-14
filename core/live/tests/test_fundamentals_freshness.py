"""How old are the filings the live run is reading? (audit FUND-2)

Every decision since about May 2026 was taken on fundamentals frozen at
2026-04-28: the newest filing any of 3,726 universe members was read
from was 199 days old on the audit date, and nothing anywhere said so.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.backtest.point_in_time import FilingMetadata, PointInTimeFinancials
from core.live.fundamentals_freshness import (
    MAX_FUNDAMENTALS_AGE_DAYS,
    assess_freshness,
)

AS_OF = date(2026, 11, 13)


def _fin(ticker: str, filed: date) -> PointInTimeFinancials:
    return PointInTimeFinancials(
        ticker=ticker,
        as_of=AS_OF,
        source_filing=FilingMetadata(
            ticker=ticker,
            cik=None,
            form_type="10-Q",
            filing_date=filed,
            period_of_report=None,
            accession_number=f"ACC-{ticker}",
        ),
        net_income=1.0,
    )


def test_the_limit_is_120_days() -> None:
    assert MAX_FUNDAMENTALS_AGE_DAYS == 120


def test_a_recent_filing_anywhere_in_the_universe_is_fresh() -> None:
    f = assess_freshness(
        {
            "OLD": _fin("OLD", AS_OF - timedelta(days=400)),
            "NEW": _fin("NEW", AS_OF - timedelta(days=9)),
            "NONE": None,
        },
        AS_OF,
    )

    assert f.newest_filed == AS_OF - timedelta(days=9)
    assert f.age_days == 9
    assert f.stale is False


def test_the_audit_date_corpus_is_stale() -> None:
    """199 days: the youngest filing any member was read from on 2026-11-13."""
    f = assess_freshness({"A": _fin("A", AS_OF - timedelta(days=199))}, AS_OF)

    assert f.stale is True
    assert f.age_days == 199


def test_no_fundamentals_at_all_is_stale_not_fresh() -> None:
    """Nothing read is the worst case, and must not pass as 'no evidence'."""
    f = assess_freshness({"A": None, "B": None}, AS_OF)

    assert f.newest_filed is None
    assert f.age_days is None
    assert f.stale is True


def test_an_empty_universe_is_stale() -> None:
    assert assess_freshness({}, AS_OF).stale is True


@pytest.mark.parametrize(("age", "stale"), [(120, False), (121, True)])
def test_the_boundary_is_inclusive(age: int, stale: bool) -> None:
    f = assess_freshness({"A": _fin("A", AS_OF - timedelta(days=age))}, AS_OF)

    assert f.stale is stale


def test_a_custom_limit_is_honoured() -> None:
    f = assess_freshness(
        {"A": _fin("A", AS_OF - timedelta(days=31))}, AS_OF, max_age_days=30
    )

    assert f.stale is True
    assert f.max_age_days == 30
