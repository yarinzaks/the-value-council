"""Testing the *selection procedure*, not the selected design.

Why this exists
~~~~~~~~~~~~~~~

A single train/test split answers one question — did this design work
on data it had not seen — and it answered it badly: the chosen design
returned 3.55% against a 16.01% benchmark, and across thirteen designs
the rank correlation between the two windows was **-0.440**. Picking
the best performer on eight years was worse than picking at random for
the next seven.

That result condemns the *method*, not just the design. So the question
worth asking next is not "which design is best" — the split already
showed that question has no stable answer — but "does choosing on past
performance work at all, and if not, what does?"

Walk-forward answers the first half directly. At each date, rank every
design on the trailing window, adopt the winner, and record what it
earns over the following period. Repeat. The result is the track record
of *the procedure a person would actually follow*, including its
mistakes, rather than the track record of a design chosen with
hindsight.

If that track record is poor, the honest conclusion is that factor
selection does not work on this data, and the alternative is to stop
selecting: hold every leg at once and accept the average rather than
betting on which regime is coming.

What this cannot fix
~~~~~~~~~~~~~~~~~~~~

Every window here has now been seen. Walk-forward re-uses the same
history it is measured on, so it cannot manufacture a fresh
out-of-sample test — nothing can, short of waiting for new data. What
it *can* do is distinguish between two claims that a single split
cannot: "this design happened to work in one period" and "this way of
choosing designs works across periods". The second is a much weaker
thing to select on, and it is the strongest thing left.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.logger import get_logger
from core.research.evaluate import Design, build_weights, period_returns

logger = get_logger("core.research.walk_forward")

#: Rebalances of history a choice is made on, and how long the choice
#: is then held before being revisited. Eight quarters trains on two
#: years and four holds for one — short enough that a regime change is
#: visible in the record rather than averaged away, long enough that
#: the trailing estimate is not pure noise.
TRAIN_PERIODS = 8
TEST_PERIODS = 4


@dataclass(frozen=True)
class WalkForwardResult:
    """What each design earned, and what following the procedure earned."""

    #: Per-design, per-period net returns. Index is the rebalance date.
    returns: pd.DataFrame
    #: The design chosen at each decision point, by trailing performance.
    chosen: pd.Series
    #: Net return of following those choices.
    procedure: pd.Series
    #: Net return of holding every design in equal weight instead.
    diversified: pd.Series
    #: Benchmark over the same periods.
    benchmark: pd.Series


def design_returns(
    panel: pd.DataFrame, designs: tuple[Design, ...]
) -> pd.DataFrame:
    """Per-period net return for every design, on one shared index."""
    series: dict[str, pd.Series] = {}
    for design in designs:
        missing = [
            leg.column for leg in design.legs if leg.column not in panel.columns
        ]
        if missing:
            logger.info(f"skipping '{design.name}': panel has no {missing}")
            continue
        try:
            weights = build_weights(panel, design)
        except ValueError as exc:
            logger.warning(f"'{design.name}' produced no portfolio: {exc}")
            continue
        series[design.name] = period_returns(panel, weights, design)["net"]
    if not series:
        raise ValueError("no design could be scored")
    return pd.DataFrame(series).sort_index()


def run(
    returns: pd.DataFrame,
    benchmark: pd.Series,
    *,
    train_periods: int = TRAIN_PERIODS,
    test_periods: int = TEST_PERIODS,
) -> WalkForwardResult:
    """Follow the trailing winner forward, and compare against holding all.

    The choice at each decision point uses only periods strictly before
    it, and is then held for ``test_periods`` without revision. Nothing
    in the held stretch feeds back into the choice that produced it.
    """
    if train_periods < 2:
        raise ValueError(f"train_periods must be at least 2; got {train_periods}")
    if test_periods < 1:
        raise ValueError(f"test_periods must be at least 1; got {test_periods}")

    aligned = returns.dropna(how="all").sort_index()
    bench = benchmark.reindex(aligned.index)

    chosen: dict[pd.Timestamp, str] = {}
    followed: dict[pd.Timestamp, float] = {}

    start = train_periods
    while start < len(aligned):
        history = aligned.iloc[start - train_periods : start]
        # Compound rather than average: a design that returns +50% then
        # -50% has a positive mean and has lost money.
        trailing = (1.0 + history).prod() - 1.0
        pick = str(trailing.idxmax())

        for offset in range(test_periods):
            row = start + offset
            if row >= len(aligned):
                break
            when = aligned.index[row]
            chosen[when] = pick
            followed[when] = float(aligned.iloc[row][pick])
        start += test_periods

    procedure = pd.Series(followed).sort_index()
    # Equal weight across every design, rebalanced each period — the
    # "stop choosing" alternative.
    diversified = aligned.mean(axis=1).reindex(procedure.index)

    return WalkForwardResult(
        returns=aligned,
        chosen=pd.Series(chosen).sort_index(),
        procedure=procedure,
        diversified=diversified,
        benchmark=bench.reindex(procedure.index),
    )


def consistency(
    returns: pd.DataFrame, benchmark: pd.Series, *, window: int = 8
) -> pd.DataFrame:
    """How often, and how reliably, each design beats the market.

    Ranked on the *worst* rolling window rather than the average one.
    A design with a good mean and one catastrophic stretch is a design
    that will be abandoned in the middle of that stretch, so the mean is
    not the number a person actually experiences.
    """
    bench = benchmark.reindex(returns.index).fillna(0.0)
    rows: list[dict[str, object]] = []
    for name in returns.columns:
        excess = returns[name] - bench
        rolling = excess.rolling(window).sum().dropna()
        if rolling.empty:
            continue
        rows.append(
            {
                "design": name,
                "periods beaten %": round(float((excess > 0).mean()) * 100, 1),
                "worst window %": round(float(rolling.min()) * 100, 2),
                "best window %": round(float(rolling.max()) * 100, 2),
                "median window %": round(float(rolling.median()) * 100, 2),
                "windows won %": round(float((rolling > 0).mean()) * 100, 1),
            }
        )
    frame = pd.DataFrame(rows)
    return frame.sort_values("worst window %", ascending=False).reset_index(drop=True)


def annualise(series: pd.Series, *, periods_per_year: int = 4) -> float:
    """CAGR of a per-period return series, in percent."""
    clean = series.dropna()
    if clean.empty:
        return float("nan")
    growth = float((1.0 + clean).prod())
    if growth <= 0:
        return -100.0
    years = len(clean) / periods_per_year
    return (growth ** (1.0 / years) - 1.0) * 100.0


def max_drawdown(series: pd.Series, *, periods_per_year: int = 4) -> float:
    """Worst peak-to-trough decline of the compounded series, in percent.

    Measured on the rebalance grid, so it cannot see an intra-period
    fall — the real engine put this strategy's drawdown at -14.22%
    where the quarterly series said -8.09%. Read every figure from here
    as a floor.
    """
    clean = series.dropna()
    if clean.empty:
        return float("nan")
    curve = (1.0 + clean).cumprod()
    return float((curve / curve.cummax() - 1.0).min()) * 100.0


__all__ = [
    "TEST_PERIODS",
    "TRAIN_PERIODS",
    "WalkForwardResult",
    "annualise",
    "consistency",
    "design_returns",
    "max_drawdown",
    "run",
]
