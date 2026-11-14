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

A complete balance sheet is not enough on its own, though. Audit FUND-4
found ten holdings with a full balance sheet whose debt sat in elements
the composer did not read, all scored 0.00 — HRI with liabilities 6.1x
equity, WH 7.9x, RPD 10.2x. A debt-free company does not carry
liabilities well beyond its equity on operating items alone, so at or
above :data:`MAX_DEBT_FREE_LIABILITIES_TO_EQUITY` absence stays unknown.
A tagged figure below :data:`MIN_DEBT_SHARE_OF_LIABILITIES` of such a
balance sheet is the same evidence — a fragment of the debt read from
the one element the composer found (CRTO's $37M of current notes) — and
is treated the same way.
"""

from __future__ import annotations

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger

logger = get_logger("core.scoring.leverage")

# Fields that together constitute "this filer tagged a balance sheet".
_BALANCE_SHEET_FIELDS = ("total_assets", "total_liabilities", "current_liabilities")

# Above this liabilities/equity, an absent debt concept is not read as
# zero debt. Against the FUND-4 holdings: CRTO 0.88, SIRI 1.36 and ALRM
# 1.47 sit below it and are caught only by the composer reading their
# tags; APH 1.69, FISV 2.11, GOLD 2.33, COLL 4.49, HRI 6.07, WH 7.94 and
# RPD 10.16 sit above.
MAX_DEBT_FREE_LIABILITIES_TO_EQUITY = 1.5

# On a balance sheet at or above that leverage, reported debt below this
# share of total liabilities is implausibly low and the ratio abstains.
# Uncalibrated against the universe: a conservative floor chosen so that
# only a sliver of debt against heavy liabilities trips it.
MIN_DEBT_SHARE_OF_LIABILITIES = 0.05


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
      to read that absence as zero;
    * no debt concept was tagged *and* liabilities are at least
      :data:`MAX_DEBT_FREE_LIABILITIES_TO_EQUITY` times equity — too
      leveraged for absence to mean no borrowings;
    * debt was tagged but is below :data:`MIN_DEBT_SHARE_OF_LIABILITIES`
      of liabilities on such a balance sheet — implausibly low.
    """
    if fin is None or fin.total_equity is None or fin.total_equity <= 0:
        return None
    levered = (
        fin.total_liabilities is not None
        and fin.total_liabilities / fin.total_equity
        >= MAX_DEBT_FREE_LIABILITIES_TO_EQUITY
    )
    debt = reported_debt(fin)
    if debt is None:
        if not has_complete_balance_sheet(fin) or levered:
            return None
        debt = 0.0
    elif (
        levered
        and fin.total_liabilities is not None
        and debt < MIN_DEBT_SHARE_OF_LIABILITIES * fin.total_liabilities
    ):
        logger.info(
            f"{fin.ticker}: reported debt {debt:,.0f} is under "
            f"{MIN_DEBT_SHARE_OF_LIABILITIES:.0%} of liabilities "
            f"{fin.total_liabilities:,.0f}; D/E treated as unknown"
        )
        return None
    return debt / fin.total_equity


__all__ = [
    "MAX_DEBT_FREE_LIABILITIES_TO_EQUITY",
    "MIN_DEBT_SHARE_OF_LIABILITIES",
    "debt_to_equity",
    "has_complete_balance_sheet",
    "reported_debt",
]
