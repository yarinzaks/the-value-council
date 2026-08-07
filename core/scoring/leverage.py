"""Debt-to-equity, with an honest answer when debt is not reported.

Why this module exists
~~~~~~~~~~~~~~~~~~~~~~

Nine agents each carried an identical ``debt_to_equity`` that ended:

    debt = fin.total_debt if fin.total_debt is not None else fin.long_term_debt
    if debt is None:
        return 0.0

Zero is the best possible score on every leverage gate in the project,
so a company whose debt was simply never tagged sailed through all of
them. Measured on 300 sampled tickers at 2026-08-04, 62 of the 169
companies with positive equity had no debt figure at all — 37% of the
judgeable universe scoring as pristine on no evidence.

The obvious correction, returning ``None``, is wrong in the other
direction. XBRL does not require tagging a zero, so a genuinely
debt-free company reports no debt concept either. Rejecting on absence
ejects Texas Pacific Land, Snowflake and Zumiez — exactly the balance
sheets Graham, Schloss and Buffett are looking for.

The discriminator is whether the filer tagged a balance sheet at all.
A company that reports total assets, total liabilities and current
liabilities but no debt concept has told us its liabilities are not
borrowings. A company missing those did not tell us anything.

Against the same sample that splits 62 into 49 genuinely debt-free
(SNOW, XMTR, TPL, ZUMZ) and 13 unknown (BSX, EXPD — both of which
plainly do carry debt and were being scored 0.00).
"""

from __future__ import annotations

from core.backtest.point_in_time import PointInTimeFinancials

# Fields that together constitute "this filer tagged a balance sheet".
_BALANCE_SHEET_FIELDS = ("total_assets", "total_liabilities", "current_liabilities")


def reported_debt(fin: PointInTimeFinancials) -> float | None:
    """Total debt as reported, or ``None`` when nothing was tagged."""
    if fin.total_debt is not None:
        return fin.total_debt
    return fin.long_term_debt


def has_complete_balance_sheet(fin: PointInTimeFinancials) -> bool:
    """True when the filer tagged enough of the balance sheet that an
    absent debt concept means no debt rather than no data."""
    return all(getattr(fin, f, None) is not None for f in _BALANCE_SHEET_FIELDS)


def debt_to_equity(fin: PointInTimeFinancials | None) -> float | None:
    """Debt / equity, or ``None`` when the ratio cannot be established.

    ``None`` means one of two things, and callers reject on both:

    * equity is missing or non-positive — the ratio is undefined;
    * no debt concept was tagged *and* the balance sheet is too sparse
      to read that absence as zero.
    """
    if fin is None or fin.total_equity is None or fin.total_equity <= 0:
        return None
    debt = reported_debt(fin)
    if debt is None:
        if not has_complete_balance_sheet(fin):
            return None
        debt = 0.0
    return debt / fin.total_equity


__all__ = [
    "debt_to_equity",
    "has_complete_balance_sheet",
    "reported_debt",
]
