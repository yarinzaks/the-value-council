"""Piotroski F-Score (0-9).

Joseph Piotroski's 9-criteria fundamental screen, originally published
in *Value Investing: The Use of Historical Financial Statement Information
to Separate Winners from Losers from Losers* (2000).

A score of 8-9 indicates strong financial health; 0-2 is a red flag.
The function takes raw figures so it stays decoupled from our
:class:`Fundamentals` model and is easy to unit test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PiotroskiInputs:
    """All inputs needed to compute the F-Score across two years."""

    net_income: float
    operating_cash_flow: float
    total_assets_current: float
    total_assets_prior: float
    long_term_debt_current: float
    long_term_debt_prior: float
    current_ratio_current: float
    current_ratio_prior: float
    shares_outstanding_current: float
    shares_outstanding_prior: float
    gross_margin_current: float
    gross_margin_prior: float
    asset_turnover_current: float
    asset_turnover_prior: float
    return_on_assets_prior: float


def piotroski_f_score(inputs: PiotroskiInputs) -> tuple[int, dict[str, bool]]:
    """Compute the Piotroski F-Score and per-criterion breakdown.

    Returns:
        Tuple of ``(score, breakdown)`` where ``score`` is 0-9 and
        ``breakdown`` maps each criterion name to a boolean (passed/failed).
    """
    avg_assets = (inputs.total_assets_current + inputs.total_assets_prior) / 2.0
    return_on_assets = (
        inputs.net_income / avg_assets if avg_assets > 0 else 0.0
    )

    criteria: dict[str, bool] = {
        # Profitability (4)
        "positive_net_income": inputs.net_income > 0,
        "positive_operating_cash_flow": inputs.operating_cash_flow > 0,
        "increasing_roa": return_on_assets > inputs.return_on_assets_prior,
        "ocf_exceeds_net_income": inputs.operating_cash_flow > inputs.net_income,
        # Leverage / liquidity (3)
        "decreasing_long_term_debt": (
            inputs.long_term_debt_current < inputs.long_term_debt_prior
        ),
        "increasing_current_ratio": (
            inputs.current_ratio_current > inputs.current_ratio_prior
        ),
        "no_share_dilution": (
            inputs.shares_outstanding_current <= inputs.shares_outstanding_prior
        ),
        # Operating efficiency (2)
        "increasing_gross_margin": (
            inputs.gross_margin_current > inputs.gross_margin_prior
        ),
        "increasing_asset_turnover": (
            inputs.asset_turnover_current > inputs.asset_turnover_prior
        ),
    }

    score = sum(1 for passed in criteria.values() if passed)
    return score, criteria


__all__ = ["PiotroskiInputs", "piotroski_f_score"]
