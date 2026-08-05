"""Business-type routing from SIC codes.

Why this module exists
~~~~~~~~~~~~~~~~~~~~~~

Free cash flow is defined as operating cash flow minus capital
expenditure. That definition assumes operating cash flow measures the
cash a business throws off from selling things. For a bank, an insurer
or a REIT it does not.

A bank's operating cash flow moves with deposit inflows. An insurer's
moves with premiums collected before claims are paid — Buffett's own
float, which is a liability, not earnings. Neither has meaningful
capex. So ``OCF - capex`` for a financial is close to "how much did
customer money arrive this year", and every agent that valued a
financial on it was capitalising other people's money as owner
earnings.

The audit measured six of ten live books at 46-70% financials, so this
is not a corner case: it is most of what four agents held.

The same reasoning kills return on capital for financials — leverage is
the product, not a risk to be penalised — which is exactly why
Greenblatt excludes them outright rather than adjusting for them.

What this module does *not* do
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

It does not attempt a financial-sector valuation model. Book value and
return on equity are the right tools there, and building them is real
work. Until that exists, the honest answer for a financial is "no
estimate", not a number computed from the wrong inputs.
"""

from __future__ import annotations

from core.data.sic_codes import sic_for

# SIC major groups 60-67. The SEC's own division H, "Finance, Insurance
# and Real Estate":
#   60 depository institutions        64 insurance agents and brokers
#   61 non-depository credit          65 real estate
#   62 security and commodity brokers 67 holding and investment offices
#   63 insurance carriers
# 66 is unassigned.
_FINANCIAL_SIC2 = frozenset({60, 61, 62, 63, 64, 65, 67})

# Major group 49 — electric, gas and sanitary services. Rate-regulated,
# so earnings are set by a commission rather than by competition, and
# capex is structurally enormous. Greenblatt excludes these alongside
# financials for the same reason: the ratios do not mean what they mean
# elsewhere.
_UTILITY_SIC2 = frozenset({49})


def _sic2(sic_code: str | int | None, ticker: str | None = None) -> int | None:
    """Major industry group, from an explicit code or a ticker lookup."""
    if sic_code is None and ticker is not None:
        sic_code = sic_for(ticker)
    if sic_code is None:
        return None
    try:
        code = int(str(sic_code).strip()[:4])
    except (TypeError, ValueError):
        return None
    return code // 100


def is_financial(sic_code: str | int | None, ticker: str | None = None) -> bool:
    """True for banks, insurers, brokers, REITs and holding companies.

    Unknown resolves to False: a company we cannot classify is left to
    the other filters rather than silently dropped.
    """
    return _sic2(sic_code, ticker) in _FINANCIAL_SIC2


def is_utility(sic_code: str | int | None, ticker: str | None = None) -> bool:
    """True for rate-regulated electric, gas and sanitary services."""
    return _sic2(sic_code, ticker) in _UTILITY_SIC2


def cash_flow_valuation_is_meaningful(
    sic_code: str | int | None, ticker: str | None = None
) -> bool:
    """False when ``OCF - capex`` does not measure owner earnings.

    Callers should return ``None`` rather than a number when this is
    False. Dropping a candidate for want of a valuation is recoverable;
    buying it on a fabricated one is not.
    """
    return not is_financial(sic_code, ticker)


__all__ = [
    "cash_flow_valuation_is_meaningful",
    "is_financial",
    "is_utility",
]
