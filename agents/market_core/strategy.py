"""Hold the largest liquid US companies, weighted by what they are worth.

Read this first
~~~~~~~~~~~~~~~

This is not stock picking, and calling it a strategy oversells it. It
holds the twenty-five biggest companies it can trade, sized by market
capitalisation, and rebalances quarterly. Turnover is 6% a quarter —
the largest companies do not change often — so it is closer to a
concentrated index fund than to anything the other ten agents do.

It is here because it is the only one of twenty-four designs that beat
the index in **both** halves of the history, and the full record of the
other twenty-three is in ``docs/eleventh_agent_search_log.md``.

What it did
~~~~~~~~~~~

Over 2011-2026, quarterly, after 10bp costs: **17.67% a year against
the index's 14.44%**, beating it in twelve of sixteen years, with a
maximum drawdown of 27.9% against the index's 23.9%. The t-statistic on
that excess is 1.82 — the highest of anything measured here, and still
short of the ~2 that would make it unlikely to be luck.

It also has the best consistency profile in the set: it beat the index
in 60.7% of quarters and in 70.4% of rolling two-year windows, with a
median two-year excess of +5.72%.

What it actually is, said plainly
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A bet that the biggest companies keep leading. Its edge over the S&P
500 comes from being *more* concentrated in the largest names than the
index is — the top five are around 59% of the book — and that paid
because mega-caps led for fifteen years. There is no factor anomaly
here and no mispricing being harvested. If market leadership broadens,
this underperforms, and 2022 is the demonstration: -12.36% against the
index's -7.82%.

The academic evidence runs the other way, which is worth stating. The
documented size premium favours *small* over large; this holds the
opposite. What it has instead is fifteen years of data in which the
opposite was true, and a structure that costs almost nothing to run.

Why the eleventh seat is not the design that was chosen first
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Because that one failed. ``agents.quiet`` — hold the twenty-five
lowest-volatility names — won the development window at 12.06% against
10.75%, was frozen, and then returned 3.55% against 16.01% on data it
had not seen: -12.50 points at t = -2.27. Its docstring keeps the
record.

The lesson that produced this agent is in the same log: across the
designs, the rank correlation between the two windows was **-0.440**.
Choosing on eight years of history was worse than choosing at random.
Following the trailing winner across the whole period returned 7.81%
against the index's 14.44%. What survived was the design that needed no
choosing.

Honesty about the search
~~~~~~~~~~~~~~~~~~~~~~~~

Twenty-four designs were scored, and by the time this one was
identified both windows had been read. It is therefore *not* an
out-of-sample result — nothing here can be, short of waiting for data
that does not exist yet. The strongest true statement available is
that it was consistent across two very different regimes and required
no parameter fitted to either.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from agents.market_core.shares import shares_known_at
from core.backtest.data_loader import PriceDataLoader
from core.backtest.decision_logger import Decision, DecisionLogger
from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
    Strategy,
)
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger

logger = get_logger("agents.market_core.strategy")

#: Names held. Twenty-five is where the measurement was taken; the
#: differences against 50 and 100 were inside a point, so read it as a
#: reasonable middle rather than an optimum.
PORTFOLIO_SIZE = 25

#: Median daily dollar volume a name must clear. Below this the flat
#: 10bp cost model is fiction, and the screen starts finding shells
#: whose price series are arithmetic rather than prices.
MIN_DOLLAR_VOLUME = 1_000_000.0

#: Sessions the liquidity test is measured over.
LIQUIDITY_SESSIONS = 63

#: How far down the ranking to keep, beyond what is held.
#:
#: The live dashboard shows a watchlist — the names ranked just below
#: the buy threshold — so a reader can see what would be bought next.
#: For this screen that is simply companies 26 through 55 by size.
RANKING_DEPTH = 55

#: Minimum daily dollar volume as a fraction of market capitalisation,
#: below which the capitalisation is not believed.
#:
#: Load-bearing. ``PKG`` files a share count a thousand times too large,
#: which computes to a $6,276bn company; being the largest thing in the
#: universe it took a 41% weight and turned this design into -0.94% a
#: year with a 42% drawdown. ``JAGX`` computes to $1,649,620bn on a
#: price of $9,627,188, which is what a series looks like after enough
#: reverse splits. The tell is the trading, not the size: across 104,676
#: observations the median company turns over 0.72% of its value a day
#: and the 1st percentile still manages 0.058%. This gate sits six times
#: below that.
MIN_DAILY_TURNOVER = 0.0001


@dataclass(frozen=True)
class CorePick:
    """One holding and the arithmetic behind its weight."""

    ticker: str
    market_cap: float
    weight_pct: float

    @property
    def why_en(self) -> str:
        return (
            f"market capitalisation ${self.market_cap / 1e9:,.0f}bn, "
            f"{self.weight_pct:.1f}% of the book"
        )

    @property
    def why_he(self) -> str:
        return (
            f"שווי שוק {self.market_cap / 1e9:,.0f} מיליארד$, "
            f"{self.weight_pct:.1f}% מהתיק"
        )


class MarketCore(Strategy):
    """The largest liquid companies, capitalisation-weighted."""

    name = "market_core"

    def __init__(
        self,
        *,
        price_loader: PriceDataLoader,
        edgar_cache: EdgarCache | None = None,
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
        self.edgar_cache = edgar_cache or EdgarCache()
        self.portfolio_size = portfolio_size
        self.min_dollar_volume = min_dollar_volume
        self.decision_logger = decision_logger
        self.last_picks: list[CorePick] = []
        #: Held names plus the next few, for the live watchlist. Weights
        #: on the entries beyond the book are what they *would* be, and
        #: are not acted on.
        self.last_ranking: list[CorePick] = []

    # ------------------------------------------------------------------
    def _capitalisations(
        self, as_of: date, universe: list[str], prices: PriceLookup
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Believable market caps, and the volumes that vouch for them."""
        volumes = self.price_loader.median_dollar_volume(
            universe, as_of, sessions=LIQUIDITY_SESSIONS
        )
        liquid = {t: v for t, v in volumes.items() if v >= self.min_dollar_volume}
        if not liquid:
            return {}, {}

        shares = shares_known_at(self.edgar_cache, sorted(liquid), as_of)

        caps: dict[str, float] = {}
        for ticker, count in shares.items():
            price = prices.get(ticker)
            if price is None or price <= 0:
                continue
            cap = price * count
            if cap <= 0:
                continue
            # A company this size does not trade this little. See
            # MIN_DAILY_TURNOVER.
            if liquid[ticker] / cap < MIN_DAILY_TURNOVER:
                logger.debug(
                    f"{as_of}: refusing {ticker} at ${cap / 1e9:,.0f}bn — "
                    f"turns over {liquid[ticker] / cap * 100:.6f}% a day"
                )
                continue
            caps[ticker] = cap
        return caps, liquid

    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
        *,
        held: Mapping[str, HeldPosition] | None = None,
    ) -> dict[str, float]:
        """Capitalisation-weight the ``portfolio_size`` largest names."""
        caps, liquid = self._capitalisations(as_of, universe, prices)
        logger.info(
            f"{as_of}: {len(universe)} in universe, {len(liquid)} liquid, "
            f"{len(caps)} with a believable capitalisation"
        )
        if not caps:
            # An empty dict is the runner's "no trade" signal, and it is
            # the honest answer to a quarter nothing could be measured
            # in. A partial book would sell everything the screen could
            # not see.
            logger.warning(f"{as_of}: nothing measurable; holding")
            return {}

        ordered = sorted(caps.items(), key=lambda kv: -kv[1])
        largest = ordered[: self.portfolio_size]
        total = sum(cap for _, cap in largest)
        if total <= 0:
            return {}

        weights = {ticker: cap / total for ticker, cap in largest}
        self.last_picks = [
            CorePick(
                ticker=ticker,
                market_cap=cap,
                weight_pct=weights[ticker] * 100.0,
            )
            for ticker, cap in largest
        ]
        # Everything the live watchlist might need, held or not.
        self.last_ranking = [
            CorePick(
                ticker=ticker,
                market_cap=cap,
                weight_pct=weights.get(ticker, 0.0) * 100.0,
            )
            for ticker, cap in ordered[:RANKING_DEPTH]
        ]
        self._log_decisions(as_of, prices)
        return weights

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
                    criteria_met=["largest_by_capitalisation", "liquid"],
                    criteria_failed=[],
                    criteria_values={
                        "market_cap_usd": round(pick.market_cap, 0),
                        "weight_pct": round(pick.weight_pct, 3),
                    },
                    entry_price=prices.get(pick.ticker),
                    rationale=pick.why_en,
                )
            )


__all__ = [
    "MIN_DAILY_TURNOVER",
    "MIN_DOLLAR_VOLUME",
    "PORTFOLIO_SIZE",
    "CorePick",
    "MarketCore",
]
