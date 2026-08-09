"""Score every registered design on the development window.

The window split is the whole point. Designs are compared on
2011-2018 and nothing else; 2019 onward is sealed and can only be
reached by passing ``--holdout``, which is meant to happen exactly once,
after the design is frozen.

The guard is deliberately annoying. Nothing stops a person from peeking
except the decision not to, and a peek is unrecoverable: once a holdout
number has been seen it stops being a holdout, and no amount of care
afterwards puts the information back.

Usage::

    .venv/bin/python -m scripts.run_research_search
    .venv/bin/python -m scripts.run_research_search --holdout --i-am-done-designing
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import pandas as pd

from core.logger import get_logger
from core.research.evaluate import PERIODS_PER_YEAR, Design, evaluate
from core.research.factors import add_fundamental_factors
from core.research.fundamentals_panel import panel_path
from core.research.price_panel import (
    PanelSpec,
    benchmark_returns,
    build_price_panel,
    load_price_matrices,
    rebalance_dates,
    trend_exposure,
)
from scripts.research_designs import (
    PRICE_ONLY,
    WITH_FUNDAMENTALS,
    total_registered,
)

logger = get_logger("scripts.run_research_search")

#: Design and compare here. Prices begin 2010-01-04 and the first
#: rebalance needs a full twelve-month momentum window behind it.
DEV_START = date(2011, 1, 1)
DEV_END = date(2018, 12, 31)

#: Read once, at the end, and never before.
HOLDOUT_START = date(2019, 1, 1)
HOLDOUT_END = date(2026, 8, 8)


def _load_panels(
    start: date, end: date, frequency: str
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    spec = PanelSpec(start=start, end=end, frequency=frequency)
    prices = build_price_panel(spec)
    adj, _ = load_price_matrices(spec)
    dates = rebalance_dates(adj, spec)
    bench = benchmark_returns(adj, dates)
    exposure = trend_exposure(adj, dates)

    fundamentals_file = panel_path("fundamentals_panel")
    if fundamentals_file.exists():
        fundamentals = pd.read_parquet(fundamentals_file)
        panel = add_fundamental_factors(prices, fundamentals)
    else:
        logger.warning(
            f"no fundamentals panel at {fundamentals_file} — "
            f"scoring price-only designs"
        )
        panel = prices
    return panel, bench, exposure


def _score(
    panel: pd.DataFrame,
    designs: tuple[Design, ...],
    bench: pd.Series,
    periods_per_year: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for design in designs:
        missing = [
            leg.column for leg in design.legs if leg.column not in panel.columns
        ]
        if missing:
            logger.info(f"skipping '{design.name}': panel has no {missing}")
            continue
        try:
            summary, _ = evaluate(
                panel, design, bench, periods_per_year=periods_per_year
            )
        except ValueError as exc:
            logger.warning(f"'{design.name}' could not be scored: {exc}")
            continue
        rows.append(summary.as_row())
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--holdout",
        action="store_true",
        help="score on 2019-2026 instead of the development window",
    )
    parser.add_argument(
        "--frequency",
        choices=("M", "Q"),
        default="Q",
        help=(
            "how often the strategy decides. Q by default, matching the "
            "frequency every leaderboard agent is actually run at."
        ),
    )
    parser.add_argument(
        "--i-am-done-designing",
        action="store_true",
        help="required alongside --holdout; the design must be frozen first",
    )
    args = parser.parse_args()

    if args.holdout and not args.i_am_done_designing:
        parser.error(
            "--holdout needs --i-am-done-designing. Reading the holdout "
            "converts it into a second development window, and there is no "
            "third one."
        )

    start, end = (HOLDOUT_START, HOLDOUT_END) if args.holdout else (DEV_START, DEV_END)
    label = "HOLDOUT" if args.holdout else "development"
    logger.info(f"{label} window: {start} → {end}")

    panel, bench, exposure = _load_panels(start, end, args.frequency)
    per_year = PERIODS_PER_YEAR[args.frequency]
    rows = _score(panel, PRICE_ONLY + WITH_FUNDAMENTALS, bench, per_year)
    if not rows:
        logger.error("nothing could be scored")
        return 1

    table = pd.DataFrame(rows).sort_values("CAGR%", ascending=False)
    clean = bench.dropna()
    bench_cagr = (
        float((1.0 + clean).prod()) ** (per_year / len(clean)) - 1.0
    ) * 100.0

    print()
    print("=" * 78)
    print(f"{label.upper()} WINDOW  {start} → {end}")
    print("=" * 78)
    print(table.to_string(index=False))
    print()
    print(f"benchmark CAGR: {bench_cagr:.2f}%   rebalances: {len(clean)}")
    print(f"designs registered before any result was seen: {total_registered()}")
    print(
        f"months the trend rule would have sat in cash: "
        f"{int((exposure == 0).sum())} of {len(exposure)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
