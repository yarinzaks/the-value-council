"""Research harness — for designing a strategy, not for running one.

Everything under :mod:`core.backtest` answers "how did this strategy
do?" one strategy at a time, reading EDGAR and prices per rebalance.
That is the right shape for a leaderboard run and the wrong shape for
design work: a single full-market backtest takes about an hour, so
comparing twenty variants would take a day and nobody would compare
twenty variants.

This package inverts it. Measure every candidate once into a panel
indexed by ``(rebalance_date, ticker)``, save it, and then a variant is
a few pandas operations over that panel — seconds, not an hour. The
expensive part happens once.

The panel is a research artefact, not a trading record. A strategy that
looks good here still has to survive
:class:`~core.backtest.strategy_runner.BacktestRunner`, which prices
fills, charges costs and carries positions properly.
"""

from __future__ import annotations

__all__: list[str] = []
