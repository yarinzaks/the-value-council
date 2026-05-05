"""Altman Z-Score — bankruptcy risk for manufacturing firms.

Edward Altman, *Financial Ratios, Discriminant Analysis and the Prediction
of Corporate Bankruptcy* (Journal of Finance, 1968).

Interpretation (manufacturing variant):
    Z >  2.99: "safe" zone
    1.81 ≤ Z ≤ 2.99: "grey" zone
    Z <  1.81: "distress" zone — high bankruptcy risk
"""

from __future__ import annotations


def altman_z_score(
    *,
    working_capital: float,
    retained_earnings: float,
    ebit: float,
    market_cap: float,
    sales: float,
    total_assets: float,
    total_liabilities: float,
) -> float:
    """Compute the Altman Z-Score (manufacturing).

    All monetary inputs should be in the same currency unit.

    Raises:
        ValueError: If ``total_assets`` or ``total_liabilities`` is non-positive.
    """
    if total_assets <= 0:
        raise ValueError("total_assets must be positive")
    if total_liabilities <= 0:
        raise ValueError("total_liabilities must be positive")

    a = working_capital / total_assets
    b = retained_earnings / total_assets
    c = ebit / total_assets
    d = market_cap / total_liabilities
    e = sales / total_assets

    return 1.2 * a + 1.4 * b + 3.3 * c + 0.6 * d + 1.0 * e


__all__ = ["altman_z_score"]
