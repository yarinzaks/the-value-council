"""The one window every agent's full-market validation is measured over.

Why
~~~

The leaderboard ranks ten agents against each other. That ranking only
means something if every agent ran over the same dates: a strategy
measured from 2022 skipped the 2022 drawdown, and one measured from
2019 did not. Both can be honest runs and still not be comparable.

This is not hypothetical. Before this module existed, eight scripts
used 2019-12-30 → 2024-12-31, Schloss used 2022-01-04, and Greenblatt
used 2022-12-30 — each labelled a "quick sanity run" in a comment that
the dashboard never showed. The leaderboard put all ten in one table,
so two of the rows were measured against an S&P that returned 25.47%
while the other eight faced 14.49%. Part of the ranking was a ranking
of windows.

A shared constant makes that failure impossible to reintroduce by
editing one file, and
``core/backtest/tests/test_validation_window.py`` fails if a script
goes back to hard-coding its own dates.

Choosing the window
~~~~~~~~~~~~~~~~~~~

2019-12-30 is the last trading day of 2019, so the first NAV mark is a
clean 100% cash baseline before 2020 opens; 2024-12-31 is the last
trading day of 2024. The five years in between contain a crash
(2020), a mania (2021), a rate-driven bear (2022) and two recoveries —
enough regime variety that a strategy cannot look good on one market
alone.

A shorter window is still legitimate for a one-off experiment. Pass
explicit dates to :class:`~core.backtest.strategy_runner.RunnerConfig`
for that; what this module governs is the runs the leaderboard shows.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

#: Last trading day of 2019 — the baseline mark, before any position.
VALIDATION_START: date = date(2019, 12, 30)

#: Last trading day of 2024.
VALIDATION_END: date = date(2024, 12, 31)

#: How often the leaderboard runs ask their strategies to decide.
#:
#: Quarterly, not annual. Annual is too coarse for half the council:
#: Marks's doctrine is a cycle pendulum and Dreman's is a reaction to
#: earnings surprises, and neither can express itself on a book it is
#: only allowed to touch once a year. Graham's net-nets and Buffett's
#: forever-holds are unaffected, because each agent's own exit rules —
#: Greenblatt's twelve-month hold, Schloss's fifty-percent rule — decide
#: whether anything actually moves. Being *asked* four times a year does
#: not force churn; it just stops a doctrine from being mute for eleven
#: months at a time.
#:
#: What this does NOT buy is statistical confidence. The t-statistic on
#: an agent's alpha comes from the length of the window and the
#: consistency of the excess return, both measured on the daily series;
#: rebalancing more often changes the strategy, not the sample. On the
#: annual runs no agent's alpha was distinguishable from zero — the best
#: was Marks at t = 1.04 against the ~2 needed — and that will still be
#: true here. Only a longer window moves it, and a longer window makes
#: the surviving-companies bias worse, so it is not free either.
#:
#: Annotated as the literal rather than ``str`` so it satisfies
#: ``RunnerConfig.rebalance_freq``'s ``RebalanceFreq`` without every
#: caller needing a cast. Spelled out here rather than imported from
#: :mod:`core.backtest.strategy_runner`, which would pull the whole
#: engine in to carry one type.
VALIDATION_REBALANCE: Literal["quarterly"] = "quarterly"

__all__ = ["VALIDATION_END", "VALIDATION_REBALANCE", "VALIDATION_START"]
