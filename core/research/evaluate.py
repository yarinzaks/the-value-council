"""Scoring a candidate strategy against a panel, in seconds.

The point of this module is iteration speed.
:class:`~core.backtest.strategy_runner.BacktestRunner` takes about an
hour on the full market because it re-reads EDGAR and prices at every
rebalance; twenty variants would be a day of waiting, so nobody would
try twenty variants and the design would be whatever was guessed first.
With the measurements already in a panel, a variant is a groupby and a
cumulative product.

What this is not
~~~~~~~~~~~~~~~~

Not a backtest. It compounds rebalance-to-rebalance returns on weights
struck at the close, charges a flat cost against turnover, and stops
there. It does not carry positions, price partial fills, handle a name
that stops trading mid-period, or apply any agent's own exit rules.
A number from here is a reason to *try* something in the real engine,
never a result to report.

Costs
~~~~~

Turnover is measured one-way — the sum of absolute weight changes,
halved, so replacing a whole book reads as 100% rather than 200% — and
charged at ``cost_bps`` per side. That matches the flat
:class:`~core.backtest.transaction_costs.PercentageCost` the other
agents are scored under, which is itself optimistic: no spread, no
market impact, and a fill at the close.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np
import pandas as pd

from core.logger import get_logger

logger = get_logger("core.research.evaluate")

#: Rebalances per year, by :attr:`PanelSpec.frequency`. Annualising a
#: quarterly series as though it were monthly overstates volatility by
#: √3 and turns a real Sharpe into a flattering one, so this is passed
#: explicitly rather than assumed.
PERIODS_PER_YEAR: dict[str, int] = {"M": 12, "Q": 4}

#: Used where a caller has not said. Quarterly, matching the frequency
#: every leaderboard agent is actually run at.
DEFAULT_PERIODS_PER_YEAR = PERIODS_PER_YEAR["Q"]

#: Cost charged per side, in basis points. The same 10bp the leaderboard
#: agents pay, so a number here is comparable to theirs.
DEFAULT_COST_BPS = 10.0

Weighting = Literal["equal", "inverse_vol", "cap"]


@dataclass(frozen=True)
class Leg:
    """One factor in a composite score.

    ``higher_is_better`` records the direction the evidence points, and
    it has to be stated per leg rather than assumed: cheapness and
    profitability reward high values, while volatility rewards low
    ones. Getting a sign backwards produces a strategy that looks like
    a discovery and is really a mirror.
    """

    column: str
    weight: float = 1.0
    higher_is_better: bool = True


@dataclass(frozen=True)
class Design:
    """A complete, testable strategy specification."""

    name: str
    legs: tuple[Leg, ...]
    portfolio_size: int = 25
    weighting: Weighting = "equal"
    cost_bps: float = DEFAULT_COST_BPS
    #: Optional per-date exposure, 0.0 to 1.0. The rest sits in cash.
    #: Used by the trend overlay; ``None`` means always fully invested.
    exposure: pd.Series | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("a design needs at least one leg")
        if self.portfolio_size <= 0:
            raise ValueError(f"portfolio_size must be positive; got {self.portfolio_size}")
        if any(leg.weight <= 0 for leg in self.legs):
            raise ValueError("leg weights must be positive")


def percentile_ranks(values: pd.Series) -> pd.Series:
    """Cross-sectional ranks in ``[0, 1]``, ties sharing a position.

    Ranks rather than z-scores throughout. A z-score lets one company
    with a 4,000% trailing return dominate a composite that is supposed
    to express "cheap and profitable and trending"; a rank caps every
    leg's influence at the width of the cross-section, which is what
    combining legs is for.
    """
    return values.rank(pct=True, na_option="keep")


def composite_score(frame: pd.DataFrame, legs: tuple[Leg, ...]) -> pd.Series:
    """Weighted average of each leg's percentile rank, within one date.

    A row missing any leg scores ``NaN`` and drops out. Averaging over
    whatever happens to be present would quietly reward companies with
    sparse filings, and imputing a median would invent a measurement.
    """
    total = sum(leg.weight for leg in legs)
    score = pd.Series(0.0, index=frame.index, dtype="float64")
    for leg in legs:
        ranks = percentile_ranks(frame[leg.column])
        if not leg.higher_is_better:
            ranks = 1.0 - ranks
        score = score + ranks * (leg.weight / total)
    return score


def _weights_for_date(
    frame: pd.DataFrame, design: Design
) -> pd.Series:
    """Book weights for one rebalance date, summing to 1.0 (or 0.0)."""
    needed = [leg.column for leg in design.legs]
    usable = frame.dropna(subset=needed)
    if usable.empty:
        return pd.Series(dtype="float64")

    score = composite_score(usable, design.legs)
    chosen = score.nlargest(min(design.portfolio_size, len(score))).index
    picked = usable.loc[chosen]

    if design.weighting == "inverse_vol":
        # Size by the reciprocal of idiosyncratic volatility, so a name
        # that moves twice as much takes half the room. This equalises
        # each position's contribution to risk rather than its dollar
        # value; equal dollars in a 90%-vol biotech and a 15%-vol
        # utility is not a diversified book.
        inv = 1.0 / picked["ivol_6m"].clip(lower=0.05)
        weights = inv / inv.sum()
    elif design.weighting == "cap":
        # Weight by market capitalisation, the way the index does.
        #
        # This is not a stylistic option. Every design tested here picks
        # 25 names from ~1,500 and weights them equally, and every one
        # of them lost to the index over 2011-2026 — a period whose
        # index return came overwhelmingly from its very largest
        # constituents. An equal-weighted book of 25 cannot express
        # "hold more of the biggest company" at all, so the comparison
        # was never testing the signals alone; it was testing the
        # signals *and* a weighting scheme, against a benchmark that
        # used the other one.
        #
        # Requires `market_cap`, which is only trustworthy because
        # `core.research.splits` restates share counts into the price
        # series' units first.
        caps = picked["market_cap"].clip(lower=0.0)
        total = float(caps.sum())
        # No capitalisations at all means the filings are missing, not
        # that the companies are worthless; fall back to equal weight
        # rather than dividing by zero or holding nothing.
        weights = (
            caps / total
            if total > 0
            else pd.Series(1.0 / len(picked), index=picked.index)
        )
    else:
        weights = pd.Series(1.0 / len(picked), index=picked.index)
    return weights


def build_weights(panel: pd.DataFrame, design: Design) -> pd.DataFrame:
    """Weights per ``(date, ticker)`` for every rebalance in the panel."""
    per_date = []
    for when, frame in panel.groupby(level="date"):
        w = _weights_for_date(frame.droplevel("date"), design)
        if w.empty:
            continue
        if design.exposure is not None:
            w = w * float(design.exposure.get(when, 1.0))
        per_date.append(pd.DataFrame({"weight": w}).assign(date=when))
    if not per_date:
        raise ValueError(f"{design.name}: no date produced a portfolio")
    out = pd.concat(per_date).set_index("date", append=True).swaplevel()
    out.index.names = ["date", "ticker"]
    return out.sort_index()


def period_returns(
    panel: pd.DataFrame, weights: pd.DataFrame, design: Design
) -> pd.DataFrame:
    """Per-rebalance gross return, cost, and net return."""
    joined = weights.join(panel[["fwd_next"]], how="left")
    # A name with no forward return did not trade through the period;
    # holding it flat is the honest assumption, and dropping it would
    # silently reweight the book toward whatever survived.
    joined["fwd_next"] = joined["fwd_next"].fillna(0.0)
    gross = joined.groupby(level="date").apply(
        lambda g: float((g["weight"] * g["fwd_next"]).sum())
    )

    wide = weights["weight"].unstack(fill_value=0.0)
    returns = (
        joined["fwd_next"].unstack(fill_value=0.0).reindex(columns=wide.columns).fillna(0.0)
    )

    # Turnover against the *drifted* book, not against the previous
    # target. A position left at the same target weight two rebalances
    # running still has to be traded, because its share of the book
    # moved with its price in between; differencing the targets calls
    # that zero and quietly makes the strategy cheaper than it is.
    #
    # drifted_i = w_i (1 + r_i) / (1 + r_portfolio), which is the weight
    # the position actually carries into the next decision.
    turnover_values: list[float] = []
    drifted = pd.Series(0.0, index=wide.columns, dtype="float64")
    for when in wide.index:
        target = wide.loc[when]
        turnover_values.append(float((target - drifted).abs().sum()) / 2.0)
        period = returns.loc[when]
        grown = target * (1.0 + period)
        total = float(grown.sum())
        drifted = grown / total if total > 0 else grown
    turnover = pd.Series(turnover_values, index=wide.index)

    # The opening book is bought outright: the first difference above is
    # against an empty portfolio, so halving it would charge for one side
    # of a trade that only has one side.
    if len(turnover):
        turnover.iloc[0] = float(wide.iloc[0].abs().sum())
    cost = turnover * (design.cost_bps / 10_000.0)

    out = pd.DataFrame({"gross": gross, "turnover": turnover, "cost": cost})
    out["net"] = out["gross"] - out["cost"]
    return out


@dataclass(frozen=True)
class Summary:
    """Headline numbers for one design over one window."""

    name: str
    periods: int
    cagr_pct: float
    volatility_pct: float
    sharpe: float
    max_drawdown_pct: float
    turnover_per_period: float
    alpha_pct: float
    t_stat: float

    def as_row(self) -> dict[str, object]:
        return {
            "design": self.name,
            "CAGR%": round(self.cagr_pct, 2),
            "vol%": round(self.volatility_pct, 2),
            "Sharpe": round(self.sharpe, 3),
            "maxDD%": round(self.max_drawdown_pct, 2),
            "turnover": round(self.turnover_per_period, 3),
            "alpha%": round(self.alpha_pct, 2),
            "t": round(self.t_stat, 2),
        }


def summarize(
    net: pd.Series,
    benchmark: pd.Series,
    *,
    name: str,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> Summary:
    """Annualised performance, and how confident one can be in it.

    The t-statistic is the number that decides whether anything here is
    real. It is the mean monthly excess return over its standard error,
    and roughly 2 is the threshold for "unlikely to be luck". Five years
    of monthly data can rarely clear it — which is the finding, not an
    inconvenience.
    """
    net = net.dropna()
    bench = benchmark.reindex(net.index).fillna(0.0)
    if len(net) < 2:
        raise ValueError(f"{name}: need at least two periods, got {len(net)}")

    years = len(net) / periods_per_year
    growth = float((1.0 + net).prod())
    cagr = (growth ** (1.0 / years) - 1.0) * 100.0 if growth > 0 else -100.0
    vol = float(net.std(ddof=1)) * np.sqrt(periods_per_year) * 100.0
    sharpe = (cagr / vol) if vol > 0 else 0.0

    curve = (1.0 + net).cumprod()
    drawdown = float((curve / curve.cummax() - 1.0).min()) * 100.0

    bench_growth = float((1.0 + bench).prod())
    bench_cagr = (
        (bench_growth ** (1.0 / years) - 1.0) * 100.0 if bench_growth > 0 else -100.0
    )

    excess = net - bench
    se = float(excess.std(ddof=1)) / np.sqrt(len(excess))
    t_stat = float(excess.mean() / se) if se > 0 else 0.0

    return Summary(
        name=name,
        periods=len(net),
        cagr_pct=cagr,
        volatility_pct=vol,
        sharpe=sharpe,
        max_drawdown_pct=drawdown,
        turnover_per_period=0.0,
        alpha_pct=cagr - bench_cagr,
        t_stat=t_stat,
    )


def evaluate(
    panel: pd.DataFrame,
    design: Design,
    benchmark: pd.Series,
    *,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> tuple[Summary, pd.DataFrame]:
    """Run one design over the panel. Returns ``(summary, per-period)``."""
    weights = build_weights(panel, design)
    periods = period_returns(panel, weights, design)
    summary = replace(
        summarize(
            periods["net"],
            benchmark,
            name=design.name,
            periods_per_year=periods_per_year,
        ),
        turnover_per_period=float(periods["turnover"].mean()),
    )
    return summary, periods


__all__ = [
    "DEFAULT_COST_BPS",
    "DEFAULT_PERIODS_PER_YEAR",
    "PERIODS_PER_YEAR",
    "Design",
    "Leg",
    "Summary",
    "build_weights",
    "composite_score",
    "evaluate",
    "percentile_ranks",
    "period_returns",
    "summarize",
]
