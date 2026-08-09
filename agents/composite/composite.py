"""A value-profitability-momentum composite. Not a council member.

Status: superseded, kept for the record
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This does **not** hold the eleventh seat. ``agents.market_core`` does.
Nothing here is wired into ``core.live.runner``, into the dashboard, or
into any backtest the site publishes, and it should not be read as a
strategy this project stands behind.

It was written first, before the research harness in ``core.research``
existed, and its claim below that "every parameter was fixed before the
first run" was made in good faith about a run that was never scored.
Its name appears in none of the tables in
``docs/eleventh_agent_search_log.md`` — the twenty-one designs measured
there were built and evaluated afterwards, on a train/test split with a
sealed holdout, and the seat went to the one design that beat the index
in both windows.

It stays in the tree because the factor-scoring code and its tests are
working and tested, and because the reasoning below — why an eleventh
value investor adds a correlated opinion rather than information — is
the argument that started the search. Read it as the opening position,
not the conclusion. The conclusion is in the log, and it is that
nothing tested beat holding the index.

What this is
~~~~~~~~~~~~

Not a person. The other ten agents implement a named investor's written
method, and each is answerable to a book. This one has no book to be
answerable to, so it is answerable to published cross-sectional
evidence instead, and it says so rather than inventing a character to
put in front of the arithmetic.

The thesis, in one sentence: rank the market on cheapness, on
profitability and on twelve-month momentum, and hold the names that
score well on all three at once.

Why it is not an eleventh value investor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The council is ten value investors. Adding a eleventh adds a correlated
opinion, not information — the sector donut already shows the books
converging, and on the 2020-2024 runs every agent's alpha sat inside
the noise. Momentum is the one large, repeatedly-reproduced effect the
council does not express at all: Jegadeesh & Titman (1993), confirmed
back to 1927 and across forty-odd markets.

It matters more that value and momentum are *negatively* correlated.
They do not merely add; they cover each other's drawdowns, which is why
the combination has historically carried a materially better
risk-adjusted return than either leg alone (Asness, Moskowitz &
Pedersen, 2013). Profitability is the third documented leg and
diversifies both.

Every parameter was fixed before the first run
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Three legs, equally weighted, top 25 names, quarterly. None of it was
chosen by trying alternatives against the scoring window, and none of
it will be changed because the result disappoints. That rule is the
whole reason to trust the number: "highest return" is exactly the
objective that produced the 1,365% and the 112% CAGR this codebase
spent two days removing, and the only defence against reproducing them
is refusing to tune on the sample being reported.

What would make this look better than it is
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Survivorship, and it hits this strategy harder than the others.
:class:`FullMarketUniverse` builds its roster from currently-registered
issuers, so companies that failed are absent at every historical date —
and a company on its way to failing has catastrophic momentum. The leg
that should be shorting them instead never sees them. Read the momentum
contribution here as an upper bound even by the standards of the rest
of this dashboard.

Transaction costs are the second overstatement. Quarterly turnover on a
momentum book is high, the cost model is a flat 10bp with no slippage
and no market impact, and fills are struck at the close.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from agents.composite.factor_scores import (
    FactorScores,
    composite_ranks,
    earnings_yield,
    operating_profitability,
)
from agents.dreman.diversification import diversify
from core.backtest.data_loader import PriceDataLoader
from core.backtest.decision_logger import Decision, DecisionLogger
from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
    Strategy,
)
from core.logger import get_logger

logger = get_logger("agents.composite.composite")

#: Names held. Twenty-five is enough for the industry cap to bind and
#: for a single blow-up to cost 4% rather than 12%, and few enough that
#: the ranking still expresses a view. It is a middle, not an optimum —
#: no sweep was run to find one.
DEFAULT_PORTFOLIO_SIZE: int = 25

#: Below this, a flat 10bp cost model is fiction: the spread alone on a
#: sub-$300M name exceeds it, and momentum's turnover would pay that
#: spread four times a year.
DEFAULT_MIN_MARKET_CAP_USD: float = 300_000_000.0

#: Momentum window, in months, and the month skipped at the near end.
#: The standard 12-and-1 construction.
MOMENTUM_LOOKBACK_MONTHS: int = 12
MOMENTUM_SKIP_MONTHS: int = 1

#: At most this share of the book in any one SIC major group. Borrowed
#: from Dreman's Rule 18 implementation, which already solves this.
MAX_INDUSTRY_WEIGHT_PCT: float = 20.0
MIN_INDUSTRIES: int = 8


@dataclass(frozen=True)
class CompositePick:
    """One selected name, with the arithmetic that selected it."""

    ticker: str
    composite: float
    value: float
    quality: float
    momentum: float

    @property
    def why_en(self) -> str:
        return (
            f"composite {self.composite:.2f} "
            f"(EBIT/EV={self.value:.1f}%, "
            f"op.profit/assets={self.quality:.1f}%, "
            f"12-1 momentum={self.momentum:+.1f}%)"
        )

    @property
    def why_he(self) -> str:
        return (
            f"ציון משולב {self.composite:.2f} "
            f"(EBIT/EV={self.value:.1f}%, "
            f"רווח תפעולי/נכסים={self.quality:.1f}%, "
            f"מומנטום 12-1={self.momentum:+.1f}%)"
        )


@dataclass(frozen=True)
class _Dropped:
    """Why a candidate never reached the ranking."""

    no_price: int = 0
    no_fundamentals: int = 0
    below_market_cap: int = 0
    no_value: int = 0
    no_quality: int = 0
    no_momentum: int = 0

    def total(self) -> int:
        return (
            self.no_price
            + self.no_fundamentals
            + self.below_market_cap
            + self.no_value
            + self.no_quality
            + self.no_momentum
        )


class FactorComposite(Strategy):
    """Rank on value, quality and momentum; hold the top of all three."""

    name = "factor_composite"

    def __init__(
        self,
        *,
        price_loader: PriceDataLoader,
        portfolio_size: int = DEFAULT_PORTFOLIO_SIZE,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP_USD,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if portfolio_size <= 0:
            raise ValueError(f"portfolio_size must be positive; got {portfolio_size}")
        if min_market_cap <= 0:
            raise ValueError(f"min_market_cap must be positive; got {min_market_cap}")
        self.price_loader = price_loader
        self.portfolio_size = portfolio_size
        self.min_market_cap = min_market_cap
        self.decision_logger = decision_logger
        self.last_picks: list[CompositePick] = []

    # ------------------------------------------------------------------
    def _market_cap(
        self, fin: PointInTimeFinancials, price: float
    ) -> float | None:
        if fin.shares_outstanding is None or fin.shares_outstanding <= 0:
            return None
        return price * fin.shares_outstanding

    def _measure(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
    ) -> tuple[list[FactorScores], _Dropped]:
        """Take the three measurements for every candidate that has them."""
        scores: list[FactorScores] = []
        no_price = no_fund = below_cap = no_val = no_qual = no_mom = 0

        for ticker in universe:
            price = prices.get(ticker)
            if price is None or price <= 0:
                no_price += 1
                continue
            fin = fundamentals.get(ticker)
            if fin is None:
                no_fund += 1
                continue
            cap = self._market_cap(fin, price)
            if cap is None or cap < self.min_market_cap:
                below_cap += 1
                continue

            value = earnings_yield(fin, cap)
            if value is None:
                no_val += 1
                continue
            quality = operating_profitability(fin)
            if quality is None:
                no_qual += 1
                continue
            momentum = self.price_loader.trailing_return(
                ticker,
                as_of,
                lookback_months=MOMENTUM_LOOKBACK_MONTHS,
                skip_months=MOMENTUM_SKIP_MONTHS,
            )
            if momentum is None:
                no_mom += 1
                continue

            scores.append(
                FactorScores(
                    ticker=ticker,
                    value=value,
                    quality=quality,
                    momentum=momentum,
                )
            )

        return scores, _Dropped(
            no_price=no_price,
            no_fundamentals=no_fund,
            below_market_cap=below_cap,
            no_value=no_val,
            no_quality=no_qual,
            no_momentum=no_mom,
        )

    # ------------------------------------------------------------------
    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
        *,
        held: Mapping[str, HeldPosition] | None = None,
    ) -> dict[str, float]:
        """Equal-weight the top ``portfolio_size`` composite ranks."""
        scores, dropped = self._measure(as_of, universe, prices, fundamentals)
        logger.info(
            f"{as_of}: {len(scores)} of {len(universe)} candidates measurable "
            f"({dropped.total()} dropped: {dropped.no_price} no price, "
            f"{dropped.no_fundamentals} no filing, {dropped.below_market_cap} "
            f"below cap, {dropped.no_value} no value, {dropped.no_quality} "
            f"no quality, {dropped.no_momentum} no momentum)"
        )
        if not scores:
            # No trade rather than a guess. The runner reads an empty
            # dict as "hold what you have", which is the honest response
            # to a screen that could not measure anything.
            logger.warning(f"{as_of}: nothing measurable; holding")
            return {}

        composite = composite_ranks(scores)
        ranked = sorted(scores, key=lambda s: -composite[s.ticker])

        chosen, report = diversify(
            ranked,
            portfolio_size=self.portfolio_size,
            min_industries=MIN_INDUSTRIES,
            max_industry_weight_pct=MAX_INDUSTRY_WEIGHT_PCT,
        )
        logger.info(
            f"{as_of}: holding {len(chosen)} names across "
            f"{report.industries} industries; largest {report.largest_industry} "
            f"at {report.max_industry_weight_pct:.1f}%"
        )

        self.last_picks = [
            CompositePick(
                ticker=s.ticker,
                composite=composite[s.ticker],
                value=s.value,
                quality=s.quality,
                momentum=s.momentum,
            )
            for s in chosen
        ]
        self._log_decisions(as_of, prices)

        if not chosen:
            return {}
        weight = 1.0 / len(chosen)
        return {s.ticker: weight for s in chosen}

    def _log_decisions(self, as_of: date, prices: PriceLookup) -> None:
        if self.decision_logger is None:
            return
        for pick in self.last_picks:
            self.decision_logger.log(
                Decision(
                    ticker=pick.ticker,
                    decision="BUY",
                    agent=self.name,
                    timestamp=f"{as_of.isoformat()}T00:00:00+00:00",
                    criteria_met=["value", "quality", "momentum"],
                    criteria_failed=[],
                    criteria_values={
                        "composite_rank": round(pick.composite, 4),
                        "ebit_ev_pct": round(pick.value, 4),
                        "op_profit_assets_pct": round(pick.quality, 4),
                        "momentum_12_1_pct": round(pick.momentum, 4),
                    },
                    entry_price=prices.get(pick.ticker),
                    rationale=pick.why_en,
                )
            )


__all__ = [
    "DEFAULT_MIN_MARKET_CAP_USD",
    "DEFAULT_PORTFOLIO_SIZE",
    "CompositePick",
    "FactorComposite",
]
