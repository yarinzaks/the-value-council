"""Does choosing a factor on past performance work at all?

The single train/test split said no, loudly: the design that ranked
second on 2011-2018 came twelfth on 2019-2026, and the rank correlation
between the two windows was -0.440. But one split is one draw. This
runs the choice repeatedly — rank on the trailing two years, hold the
winner for one, repeat across the whole history — so the question
becomes whether the *procedure* has a track record rather than whether
one design got lucky once.

Three things are compared, over the same periods:

* **selecting** — follow the trailing winner, which is what a person
  reading a leaderboard actually does;
* **diversifying** — hold every design at once and stop choosing;
* **the index**.

Usage::

    .venv/bin/python -m scripts.run_walk_forward
"""

from __future__ import annotations

import sys
from datetime import date

import pandas as pd

from core.logger import get_logger
from core.research.factors import add_fundamental_factors
from core.research.fundamentals_panel import panel_path
from core.research.price_panel import (
    PanelSpec,
    benchmark_returns,
    build_price_panel,
    load_price_matrices,
    rebalance_dates,
)
from core.research.walk_forward import (
    annualise,
    consistency,
    design_returns,
    max_drawdown,
    run,
)
from scripts.research_designs import PRICE_ONLY, WITH_FUNDAMENTALS

logger = get_logger("scripts.run_walk_forward")

#: Everything the price history supports. Both the development window
#: and the holdout have been seen by now, so there is nothing left to
#: protect — what this measures is stability across regimes, which is a
#: far weaker thing to select on than a single best number.
FULL_START = date(2011, 1, 1)
FULL_END = date(2026, 8, 8)


def main() -> int:
    spec = PanelSpec(start=FULL_START, end=FULL_END, frequency="Q")
    prices = build_price_panel(spec)
    adj, _ = load_price_matrices(spec)
    dates = rebalance_dates(adj, spec)
    bench = benchmark_returns(adj, dates)

    fundamentals_file = panel_path("fundamentals_panel")
    if fundamentals_file.exists():
        panel = add_fundamental_factors(
            prices, pd.read_parquet(fundamentals_file)
        )
    else:
        logger.warning("no fundamentals panel — price-only designs")
        panel = prices

    returns = design_returns(panel, PRICE_ONLY + WITH_FUNDAMENTALS)
    logger.info(f"{returns.shape[1]} designs over {returns.shape[0]} quarters")

    result = run(returns, bench)

    print()
    print("=" * 78)
    print(f"WALK-FORWARD  {FULL_START} → {FULL_END}")
    print("=" * 78)
    print()
    print("Following the trailing winner, versus not choosing at all:")
    rows = [
        {
            "approach": "select the trailing winner",
            "CAGR%": round(annualise(result.procedure), 2),
            "maxDD%": round(max_drawdown(result.procedure), 2),
        },
        {
            "approach": "hold every design equally",
            "CAGR%": round(annualise(result.diversified), 2),
            "maxDD%": round(max_drawdown(result.diversified), 2),
        },
        {
            "approach": "the index",
            "CAGR%": round(annualise(result.benchmark), 2),
            "maxDD%": round(max_drawdown(result.benchmark), 2),
        },
    ]
    print(pd.DataFrame(rows).to_string(index=False))
    print()

    switches = int((result.chosen != result.chosen.shift(1)).sum())
    print(
        f"The procedure changed its mind {switches} times across "
        f"{len(result.chosen)} quarters. It picked:"
    )
    print(result.chosen.value_counts().to_string())
    print()

    print("Consistency of each design (ranked by its worst two-year stretch):")
    print(consistency(returns, bench).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
