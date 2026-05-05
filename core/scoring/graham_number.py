"""Graham Number — Benjamin Graham's intrinsic-value sanity check.

From *The Intelligent Investor* (1949). The Graham Number estimates
the maximum price a defensive investor should pay for a stock based
on earnings per share and book value per share:

    GN = sqrt(22.5 * EPS * BVPS)

The constant 22.5 comes from Graham's preference for P/E ≤ 15 and P/B ≤ 1.5
(15 * 1.5 = 22.5). It only makes sense for companies with positive earnings
and positive book value.
"""

from __future__ import annotations

import math


def graham_number(eps: float, book_value_per_share: float) -> float | None:
    """Return the Graham Number, or ``None`` when inputs are non-positive.

    Returns ``None`` (not zero) for unprofitable or negative-equity firms
    because the formula is meaningless there — the absence of a value is
    informationally distinct from a value of zero.
    """
    if eps is None or book_value_per_share is None:
        return None
    if eps <= 0 or book_value_per_share <= 0:
        return None
    return math.sqrt(22.5 * eps * book_value_per_share)


__all__ = ["graham_number"]
