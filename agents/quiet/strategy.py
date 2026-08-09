"""Hold the market's quietest businesses, and change as little as possible.

What this is
~~~~~~~~~~~~

Not a person, and not a doctrine. The other ten agents implement a named
investor's written method and are answerable to a book. This one is
answerable to a measurement: the part of a stock's movement the market
does not explain. It ranks the investable universe on that, holds the
twenty-five lowest, and rebalances quarterly.

Why this and not something more ambitious
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Because it is what survived. Twenty-four designs were scored on
2011-2018 before this one was chosen — momentum at five different
breadths, volatility at six, a trend overlay, a regime switch between an
aggressive and a defensive book, several weighting schemes — and the
record is in ``docs/eleventh_agent_search_log.md``. Most of them lost to
the index. Two of them looked spectacular and turned out to be a
preferred-share leak and a single corrupted price bar.

The three findings that shaped it:

**Concentrated momentum is not momentum.** Buying the top 25 of ~1,400
names is the 98th percentile, which is the lottery-ticket population
Bali, Cakici & Whitelaw (2011) measure underperforming by about 1% a
month. It returned -4.52% a year with a 55.8% drawdown. Dropping just
the top 25 names from the ranking and taking the next hundred added
12.3 points and halved the drawdown — the predicted direction, which is
the strongest sign in the whole exercise that the mechanism was
understood rather than fitted. Even so, momentum never beat the index
here, in any breadth or any market regime.

**Timing a calm book buys nothing.** A ten-month trend overlay fired in
exactly the right quarters — August 2011, August 2015, the fourth
quarter of 2018 — and still cost 2.9 points of annual return, because a
portfolio whose worst drawdown is 8% has nothing to be protected from.

**The selection is real.** Equal-weighting the whole 1,517-name universe
with no signal returns 10.02% against the index's 10.75%. Selecting on
idiosyncratic volatility returns 12.06% with 40% less volatility and a
drawdown of 8.09% instead of 20.18%. That control is the one number in
the log a sceptic should check first.

What this will not do
~~~~~~~~~~~~~~~~~~~~~

Beat the market by a lot. On the development window it beat the index by
1.31 points a year with a t-statistic of 0.45, which is indistinguishable
from luck at this sample size. The low-volatility effect is documented
as a *risk-adjusted* one, and the band tests here agree: bands from the
quietest 25 out to rank 225 all return about the same, and what improves
monotonically toward the quiet end is the Sharpe ratio, not the return.

An agent promising more than this on eight years of survivorship-biased
data would be promising something it cannot have measured.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from core.backtest.data_loader import PriceDataLoader
from core.backtest.decision_logger import Decision, DecisionLogger
from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
    Strategy,
)
from core.logger import get_logger

logger = get_logger("agents.quiet.strategy")

#: Names held. Twenty-five was the best of 25 / 50 / 100 on the
#: development window and is also where a single blow-up costs 4% rather
#: than 12%. The differences between the three were inside a point, so
#: read this as a reasonable middle rather than an optimum.
PORTFOLIO_SIZE = 25

#: Trading days of history the volatility estimate is measured over, and
#: the minimum a name must actually have. Six months is long enough to
#: estimate a second moment and short enough to describe the stock's
#: current regime; the floor stops a name with a gap in its history from
#: being scored off a handful of bars and ranking as the calmest company
#: in the market.
VOL_SESSIONS = 126
MIN_VOL_SESSIONS = 63

#: Median daily dollar volume a name must clear. Below this the flat
#: 10bp cost model is fiction — the spread alone exceeds it — and the
#: screen starts finding shells whose price series are arithmetic rather
#: than prices.
MIN_DOLLAR_VOLUME = 1_000_000.0

#: Sessions the liquidity test is measured over.
LIQUIDITY_SESSIONS = 63


@dataclass(frozen=True)
class QuietPick:
    """One selected name and the measurement that selected it."""

    ticker: str
    idio_vol_pct: float
    dollar_volume: float

    @property
    def why_en(self) -> str:
        return (
            f"idiosyncratic volatility {self.idio_vol_pct:.1f}% "
            f"(6-month residual vs SPY), "
            f"${self.dollar_volume / 1e6:.1f}M median daily volume"
        )

    @property
    def why_he(self) -> str:
        return (
            f"תנודתיות אידיוסינקרטית {self.idio_vol_pct:.1f}% "
            f"(שארית חצי-שנתית מול SPY), "
            f"מחזור יומי חציוני {self.dollar_volume / 1e6:.1f}M$"
        )


class QuietCompounder(Strategy):
    """Rank on idiosyncratic volatility; hold the quietest names."""

    name = "quiet_compounder"

    def __init__(
        self,
        *,
        price_loader: PriceDataLoader,
        portfolio_size: int = PORTFOLIO_SIZE,
        min_dollar_volume: float = MIN_DOLLAR_VOLUME,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        if portfolio_size <= 0:
            raise ValueError(f"portfolio_size must be positive; got {portfolio_size}")
        if min_dollar_volume <= 0:
            raise ValueError(
                f"min_dollar_volume must be positive; got {min_dollar_volume}"
            )
        self.price_loader = price_loader
        self.portfolio_size = portfolio_size
        self.min_dollar_volume = min_dollar_volume
        self.decision_logger = decision_logger
        self.last_picks: list[QuietPick] = []

    # ------------------------------------------------------------------
    def _liquid(self, universe: list[str], as_of: date) -> dict[str, float]:
        """Median daily dollar volume per ticker, for names that clear
        the floor.

        The floor is not housekeeping. Every absurd row in the research
        panel — a momentum of +517,600%, a share price of $3.9e16 —
        belonged to a shell with zero traded dollars. Those are what a
        price series looks like after a company goes to nothing and
        reverse-splits its way back up, and a screen allowed to hold
        them will find enormous returns in names that cannot be bought.
        """
        volumes = self.price_loader.median_dollar_volume(
            universe, as_of, sessions=LIQUIDITY_SESSIONS
        )
        return {t: v for t, v in volumes.items() if v >= self.min_dollar_volume}

    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
        *,
        held: Mapping[str, HeldPosition] | None = None,
    ) -> dict[str, float]:
        """Equal-weight the ``portfolio_size`` quietest liquid names."""
        liquid = self._liquid(universe, as_of)
        if not liquid:
            logger.warning(f"{as_of}: nothing cleared the liquidity floor; holding")
            return {}

        vols = self.price_loader.idiosyncratic_volatility(
            sorted(liquid),
            as_of,
            sessions=VOL_SESSIONS,
            min_sessions=MIN_VOL_SESSIONS,
        )
        # A name is only a candidate if it is both tradeable and
        # measurable. Scoring on whichever half is present would rank a
        # company on the fact that it filed, not on how it behaves.
        candidates = {t: v for t, v in vols.items() if t in liquid}
        logger.info(
            f"{as_of}: {len(universe)} in universe, {len(liquid)} liquid, "
            f"{len(candidates)} with a usable volatility estimate"
        )
        if not candidates:
            # An empty dict is the runner's "no trade" signal, which is
            # the honest response to a quarter nothing could be measured
            # in. Returning a partial book would sell everything the
            # screen could not see.
            logger.warning(f"{as_of}: nothing measurable; holding")
            return {}

        ranked = sorted(candidates.items(), key=lambda kv: kv[1])
        chosen = ranked[: self.portfolio_size]

        self.last_picks = [
            QuietPick(
                ticker=ticker,
                idio_vol_pct=vol * 100.0,
                dollar_volume=liquid[ticker],
            )
            for ticker, vol in chosen
        ]
        self._log_decisions(as_of, prices)

        weight = 1.0 / len(chosen)
        return {ticker: weight for ticker, _ in chosen}

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
                    criteria_met=["low_idiosyncratic_volatility", "liquid"],
                    criteria_failed=[],
                    criteria_values={
                        "idio_vol_pct": round(pick.idio_vol_pct, 3),
                        "median_dollar_volume": round(pick.dollar_volume, 0),
                    },
                    entry_price=prices.get(pick.ticker),
                    rationale=pick.why_en,
                )
            )


__all__ = [
    "MIN_DOLLAR_VOLUME",
    "PORTFOLIO_SIZE",
    "QuietCompounder",
    "QuietPick",
]
