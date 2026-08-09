"""The candidate designs for the eleventh agent, registered up front.

Why the list is fixed before anything runs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Because the count is part of the result. Testing seventeen designs and
reporting the best one is not the same as testing one design and having
it work, even when the winning number is identical — with seventeen
draws from noise, the best of them looks good by construction. The only
way that number stays interpretable is if the denominator is written
down first and reported alongside it.

This is the failure that produced the 1,365% and 112% CAGR figures this
codebase spent two days removing. Those were not fabricated; they were
selected.

How the search is bounded
~~~~~~~~~~~~~~~~~~~~~~~~~

Every design here is scored on the **development window only**
(2011-2018). The holdout — 2019 onward — is not read during the search,
which is what makes its eventual number worth reporting. Each design is
also a combination of factors with published cross-sectional evidence
behind it, at equal or near-equal weights, rather than a free parameter
sweep. Nothing here is fitted; the search is over which documented
effects to combine, not over what values to give them.

The evidence each leg rests on
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Momentum** — Jegadeesh & Titman (1993), reproduced back to 1927 and
  across forty-odd markets. In Gu, Kelly & Xiu's (2020) machine-learning
  study of ~900 predictors, momentum is one of three that dominate every
  model they fit.
* **Volatility** — the other two dominant families in that study are
  liquidity and volatility. Within the low-risk group the effect is
  carried by idiosyncratic volatility; beta-sorted portfolios are the
  weakest version of it.
* **Value and profitability** — Fama & French (2015). Real, and heavily
  decayed post-publication: McLean & Pontiff (2016) measure anomaly
  returns falling 58% after they are written up.
* **Trend** — time-series momentum has been positive in every decade
  since 1880 and worked in eight of the ten largest drawdowns of the
  past century. It is also the only leg here that survivorship bias
  cannot inflate, because the index's own history has no hole in it.
"""

from __future__ import annotations

from core.research.evaluate import Design, Leg

#: Portfolio size for every design unless it is the thing being varied.
#: Twenty-five is enough that one blow-up costs 4% rather than 12%, and
#: few enough that the ranking still expresses a view.
STANDARD_SIZE = 25

MOMENTUM = Leg("mom_12_1")
MOMENTUM_6 = Leg("mom_6_1")
LOW_IVOL = Leg("ivol_6m", higher_is_better=False)
LOW_VOL = Leg("vol_6m", higher_is_better=False)
NOT_EXTENDED = Leg("reversal_1m", higher_is_better=False)
VALUE = Leg("earnings_yield")
QUALITY = Leg("op_profitability")


#: Designs needing nothing but the price series. These can be scored the
#: moment the price panel is built.
PRICE_ONLY: tuple[Design, ...] = (
    Design(name="momentum 12-1", legs=(MOMENTUM,), portfolio_size=STANDARD_SIZE),
    Design(name="momentum 6-1", legs=(MOMENTUM_6,), portfolio_size=STANDARD_SIZE),
    Design(name="low idio vol", legs=(LOW_IVOL,), portfolio_size=STANDARD_SIZE),
    Design(name="low total vol", legs=(LOW_VOL,), portfolio_size=STANDARD_SIZE),
    Design(name="not extended", legs=(NOT_EXTENDED,), portfolio_size=STANDARD_SIZE),
    Design(name="mom + low ivol", legs=(MOMENTUM, LOW_IVOL), portfolio_size=STANDARD_SIZE),
    Design(
        name="mom + low ivol + not extended",
        legs=(MOMENTUM, LOW_IVOL, NOT_EXTENDED),
        portfolio_size=STANDARD_SIZE,
    ),
)

#: Designs that also need the fundamentals panel joined in.
WITH_FUNDAMENTALS: tuple[Design, ...] = (
    Design(name="value", legs=(VALUE,), portfolio_size=STANDARD_SIZE),
    Design(name="quality", legs=(QUALITY,), portfolio_size=STANDARD_SIZE),
    Design(name="value + quality", legs=(VALUE, QUALITY), portfolio_size=STANDARD_SIZE),
    Design(name="value + mom", legs=(VALUE, MOMENTUM), portfolio_size=STANDARD_SIZE),
    Design(
        name="value + quality + mom",
        legs=(VALUE, QUALITY, MOMENTUM),
        portfolio_size=STANDARD_SIZE,
    ),
    Design(
        name="value + quality + mom + low ivol",
        legs=(VALUE, QUALITY, MOMENTUM, LOW_IVOL),
        portfolio_size=STANDARD_SIZE,
    ),
    Design(
        name="quality + mom + low ivol",
        legs=(QUALITY, MOMENTUM, LOW_IVOL),
        portfolio_size=STANDARD_SIZE,
    ),
)

#: Variations applied to whichever of the above wins, not searched over
#: independently. Listed here so the count stays honest.
VARIATIONS: tuple[str, ...] = (
    "portfolio size 50 instead of 25",
    "inverse-volatility weighting instead of equal",
    "trend overlay: cash when the index is below its ten-month average",
)


def total_registered() -> int:
    """How many distinct things get tried, for the multiple-testing note."""
    return len(PRICE_ONLY) + len(WITH_FUNDAMENTALS) + len(VARIATIONS)


__all__ = [
    "PRICE_ONLY",
    "STANDARD_SIZE",
    "VARIATIONS",
    "WITH_FUNDAMENTALS",
    "total_registered",
]
