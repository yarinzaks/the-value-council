"""Earnings Yield, Return on Capital, and Magic Formula combined ranking.

Implements Section 4.1 of the Greenblatt playbook precisely:

* **Earnings Yield (EY)** = EBIT / Enterprise Value
  where Enterprise Value = Market Cap + Total Debt − Cash & Equivalents.
* **Return on Capital (ROC)** = EBIT / (Net Working Capital + Net Fixed Assets)
  where Net Working Capital = Current Assets − Current Liabilities.

Greenblatt explicitly **excludes goodwill** from Net Fixed Assets and
defines NWC as the un-adjusted current-assets-minus-current-liabilities
figure (excess cash is NOT subtracted in his book — though many
academic re-tests do subtract it; we follow the book).

Combined rank: rank by EY descending (1 = highest yield), rank by ROC
descending (1 = highest ROC), sum the two ranks. Lowest sum = best.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.backtest.point_in_time import PointInTimeFinancials
from core.logger import get_logger

logger = get_logger("agents.greenblatt.ranking")


@dataclass(frozen=True)
class MagicFormulaScore:
    """Computed Magic Formula metrics for one candidate."""

    ticker: str
    earnings_yield: float
    return_on_capital: float
    ey_rank: int  # 1 = highest yield
    roc_rank: int  # 1 = highest ROC
    combined_rank: int  # lower is better
    market_cap: float
    enterprise_value: float
    invested_capital: float


def compute_enterprise_value(
    market_cap: float,
    total_debt: float | None,
    cash: float | None,
) -> float:
    """Enterprise Value = Market Cap + Total Debt − Cash & Equivalents.

    Missing debt or cash are treated as zero (a documented simplification
    — most modern fundamental data sets explicitly report 0 for
    debt-free or cashless companies, so the substitution is benign).
    """
    debt = total_debt or 0.0
    cash_val = cash or 0.0
    ev = market_cap + debt - cash_val
    return ev


def compute_invested_capital(
    current_assets: float | None,
    current_liabilities: float | None,
    ppe_net: float | None,
) -> float | None:
    """Invested Capital = Net Working Capital + Net Fixed Assets.

    Returns ``None`` if any required component is missing — the ROC
    formula is not meaningful without all three. Returns ``None`` if
    the result is non-positive (Greenblatt explicitly excludes such
    companies because the ratio is then meaningless or negative).

    Net Working Capital = Current Assets − Current Liabilities.
    """
    if current_assets is None or current_liabilities is None or ppe_net is None:
        return None
    nwc = current_assets - current_liabilities
    invested_capital = nwc + ppe_net
    if invested_capital <= 0:
        return None
    return invested_capital


def compute_earnings_yield(
    ebit: float,
    enterprise_value: float,
) -> float:
    """Earnings Yield = EBIT / Enterprise Value.

    Both inputs must be positive — that is the caller's responsibility
    (it is enforced by the filter pipeline). For an EV of zero or
    negative we return -inf so the candidate sorts to the bottom.
    """
    if enterprise_value <= 0:
        return float("-inf")
    return ebit / enterprise_value


def compute_return_on_capital(
    ebit: float,
    invested_capital: float,
) -> float:
    """ROC = EBIT / (Net Working Capital + Net Fixed Assets).

    The caller supplies the precomputed invested capital from
    :func:`compute_invested_capital` (which has already validated it
    is positive).
    """
    if invested_capital <= 0:
        return float("-inf")
    return ebit / invested_capital


DEFAULT_MAX_EARNINGS_YIELD: float = 1.0


def score_candidates(
    candidates: list[tuple[PointInTimeFinancials, float]],
    *,
    max_earnings_yield: float = DEFAULT_MAX_EARNINGS_YIELD,
) -> list[MagicFormulaScore]:
    """Score and rank candidates by the Magic Formula.

    Args:
        candidates: List of ``(financials, market_cap)`` tuples that
            have already passed all filters. EBIT (operating income),
            current_assets, current_liabilities, and ppe_net must be
            present and positive — non-conforming candidates are
            silently dropped here as a defensive belt-and-suspenders
            check.

    Returns:
        List of :class:`MagicFormulaScore` sorted by combined rank
        ascending. Empty list if no candidates score.
    """
    if not candidates:
        return []

    raw_scores: list[dict[str, object]] = []
    for fin, market_cap in candidates:
        ebit = fin.operating_income
        if ebit is None or ebit <= 0:
            continue
        ev = compute_enterprise_value(market_cap, fin.total_debt, fin.cash_and_equivalents)
        if ev <= 0:
            continue
        invested_capital = compute_invested_capital(
            fin.current_assets, fin.current_liabilities, fin.ppe_net
        )
        if invested_capital is None:
            continue
        ey = compute_earnings_yield(ebit, ev)
        # Skip implausible EY values (data anomaly guard) — most often
        # a shares-outstanding scaling issue at micro-caps.
        if ey > max_earnings_yield:
            continue
        roc = compute_return_on_capital(ebit, invested_capital)
        raw_scores.append(
            {
                "ticker": fin.ticker,
                "ey": ey,
                "roc": roc,
                "market_cap": market_cap,
                "ev": ev,
                "invested_capital": invested_capital,
            }
        )

    if not raw_scores:
        return []

    # Rank by EY descending — highest EY = rank 1
    ey_sorted = sorted(raw_scores, key=lambda s: s["ey"], reverse=True)
    ey_rank = {s["ticker"]: i + 1 for i, s in enumerate(ey_sorted)}

    # Rank by ROC descending — highest ROC = rank 1
    roc_sorted = sorted(raw_scores, key=lambda s: s["roc"], reverse=True)
    roc_rank = {s["ticker"]: i + 1 for i, s in enumerate(roc_sorted)}

    results = [
        MagicFormulaScore(
            ticker=str(s["ticker"]),
            earnings_yield=float(s["ey"]),
            return_on_capital=float(s["roc"]),
            ey_rank=ey_rank[s["ticker"]],
            roc_rank=roc_rank[s["ticker"]],
            combined_rank=ey_rank[s["ticker"]] + roc_rank[s["ticker"]],
            market_cap=float(s["market_cap"]),
            enterprise_value=float(s["ev"]),
            invested_capital=float(s["invested_capital"]),
        )
        for s in raw_scores
    ]
    # Sort ascending by combined rank — lowest is best
    results.sort(key=lambda r: (r.combined_rank, r.ey_rank))
    return results


def select_top_n(
    scores: list[MagicFormulaScore], n: int = 30
) -> list[MagicFormulaScore]:
    """Return the top ``n`` scores. If fewer are available, return all.

    Per the playbook, the agent buys 20-30 stocks; if fewer pass the
    filters we take what we can rather than forcing artificial picks.
    """
    if n <= 0:
        raise ValueError(f"n must be positive; got {n}")
    return scores[:n]


__all__ = [
    "MagicFormulaScore",
    "compute_earnings_yield",
    "compute_enterprise_value",
    "compute_invested_capital",
    "compute_return_on_capital",
    "score_candidates",
    "select_top_n",
]
