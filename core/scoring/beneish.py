"""Beneish M-Score — earnings manipulation likelihood.

Messod Beneish, *The Detection of Earnings Manipulation* (Financial
Analysts Journal, 1999).

Interpretation:
    M > -1.78: company is likely manipulating earnings
    M ≤ -1.78: company is unlikely to be manipulating earnings

The model uses 8 financial ratios that compare year-over-year changes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BeneishInputs:
    """Year-over-year inputs for the Beneish M-Score."""

    receivables_current: float
    receivables_prior: float
    sales_current: float
    sales_prior: float
    cogs_current: float
    cogs_prior: float
    current_assets_current: float
    current_assets_prior: float
    ppe_current: float
    ppe_prior: float
    total_assets_current: float
    total_assets_prior: float
    depreciation_current: float
    depreciation_prior: float
    sga_current: float
    sga_prior: float
    long_term_debt_current: float
    long_term_debt_prior: float
    net_income_current: float
    operating_cash_flow_current: float


def beneish_m_score(inputs: BeneishInputs) -> tuple[float, dict[str, float]]:
    """Compute the Beneish M-Score and the eight component ratios.

    Returns:
        Tuple of ``(m_score, ratios)`` where ``ratios`` maps each
        component name to its value.
    """
    # Days Sales in Receivables Index
    dsri = (
        (inputs.receivables_current / inputs.sales_current)
        / (inputs.receivables_prior / inputs.sales_prior)
        if inputs.sales_current and inputs.sales_prior and inputs.receivables_prior
        else 1.0
    )
    # Gross Margin Index
    gm_current = (inputs.sales_current - inputs.cogs_current) / inputs.sales_current
    gm_prior = (inputs.sales_prior - inputs.cogs_prior) / inputs.sales_prior
    gmi = gm_prior / gm_current if gm_current else 1.0

    # Asset Quality Index
    aq_current = 1 - (
        (inputs.current_assets_current + inputs.ppe_current) / inputs.total_assets_current
    )
    aq_prior = 1 - (
        (inputs.current_assets_prior + inputs.ppe_prior) / inputs.total_assets_prior
    )
    aqi = aq_current / aq_prior if aq_prior else 1.0

    # Sales Growth Index
    sgi = inputs.sales_current / inputs.sales_prior if inputs.sales_prior else 1.0

    # Depreciation Index
    dep_rate_current = inputs.depreciation_current / (
        inputs.depreciation_current + inputs.ppe_current
    ) if (inputs.depreciation_current + inputs.ppe_current) else 0.0
    dep_rate_prior = inputs.depreciation_prior / (
        inputs.depreciation_prior + inputs.ppe_prior
    ) if (inputs.depreciation_prior + inputs.ppe_prior) else 0.0
    depi = dep_rate_prior / dep_rate_current if dep_rate_current else 1.0

    # SG&A Index
    sgai = (
        (inputs.sga_current / inputs.sales_current)
        / (inputs.sga_prior / inputs.sales_prior)
        if inputs.sales_current and inputs.sales_prior and inputs.sga_prior
        else 1.0
    )

    # Leverage Index
    lvgi = (
        (inputs.long_term_debt_current / inputs.total_assets_current)
        / (inputs.long_term_debt_prior / inputs.total_assets_prior)
        if inputs.long_term_debt_prior and inputs.total_assets_prior
        else 1.0
    )

    # Total Accruals to Total Assets
    tata = (
        (inputs.net_income_current - inputs.operating_cash_flow_current)
        / inputs.total_assets_current
        if inputs.total_assets_current
        else 0.0
    )

    m_score = (
        -4.84
        + 0.92 * dsri
        + 0.528 * gmi
        + 0.404 * aqi
        + 0.892 * sgi
        + 0.115 * depi
        - 0.172 * sgai
        + 4.679 * tata
        - 0.327 * lvgi
    )
    return m_score, {
        "DSRI": dsri,
        "GMI": gmi,
        "AQI": aqi,
        "SGI": sgi,
        "DEPI": depi,
        "SGAI": sgai,
        "LVGI": lvgi,
        "TATA": tata,
    }


__all__ = ["BeneishInputs", "beneish_m_score"]
