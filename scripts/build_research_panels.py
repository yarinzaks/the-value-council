"""Build and cache the research panels.

Both panels are expensive once and free afterwards, which is the whole
point of :mod:`core.research`: measure every candidate into a table, and
then a strategy variant is a groupby rather than an hour of re-reading
EDGAR.

The fundamentals panel does not depend on the price panel, so the two
can be built at the same time — useful, because a full price refetch
takes about an hour and a half and the fundamentals sweep takes about
as long.

Usage::

    .venv/bin/python -m scripts.build_research_panels --which fundamentals
    .venv/bin/python -m scripts.build_research_panels --which prices
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime

import pandas as pd

from core.data.edgar_cache import EdgarCache
from core.data.ticker_filter import is_common_equity, is_primary_listing
from core.logger import get_logger
from core.paths import DATA_ROOT
from core.research.fundamentals_panel import (
    FundamentalsSpec,
    build_fundamentals_panel,
    panel_path,
)
from core.research.price_panel import (
    MIN_DOLLAR_VOLUME,
    PanelSpec,
    build_price_panel,
)

logger = get_logger("scripts.build_research_panels")

#: Panels span everything the price history can support. Bars begin
#: 2010-01-04, and the first rebalance needs a full twelve-month
#: momentum window behind it, so 2011 is the earliest honest start.
PANEL_START = date(2011, 1, 1)
PANEL_END = date(2026, 8, 8)


def research_universe() -> tuple[str, ...]:
    """Common-equity tickers that have both a price series and filings.

    Both halves are required. A ticker with filings and no prices cannot
    be held; one with prices and no filings cannot be scored on anything
    fundamental. Building the panel for either would fill it with rows
    that every later join throws away.
    """
    conn = sqlite3.connect(DATA_ROOT / "cache" / "prices.sqlite")
    try:
        priced = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM prices")}
    finally:
        conn.close()

    filed = set(EdgarCache().tickers())
    both = priced & filed
    common = sorted(
        t for t in both if is_common_equity(t) and is_primary_listing(t)
    )
    logger.info(
        f"universe: {len(priced):,} priced ∩ {len(filed):,} filing = "
        f"{len(both):,}, of which {len(common):,} are common equity"
    )
    return tuple(common)


#: Sessions above :data:`MIN_DOLLAR_VOLUME` a ticker needs, anywhere in
#: its life, to be worth measuring filings for.
#:
#: Half a trading year. One day is too weak a test — 4,992 tickers clear
#: it, because a single squeeze does — and a full year is too strong at
#: 3,385, since it starts asking that a company *stayed* liquid, which
#: is survivorship in a smaller costume. Half a year (3,792) separates a
#: name that was genuinely investable for a while from a shell that
#: spiked once.
MIN_LIQUID_SESSIONS = 126


def tradeable_universe(start: date, end: date) -> tuple[str, ...]:
    """Research universe, minus names never liquid enough to hold.

    The fundamentals sweep is the expensive half of the build — roughly
    ten seconds a ticker — and a name the panel's own per-date volume
    filter always rejects contributes rows that are joined and then
    discarded.

    The test is "was liquid for a while", not "is liquid now" and not
    "stayed liquid". A company that traded well for three years and then
    faded belongs in the panel for those three years, and the per-date
    filter decides which ones they were.
    """
    conn = sqlite3.connect(DATA_ROOT / "cache" / "prices.sqlite")
    try:
        rows = conn.execute(
            """
            SELECT ticker,
                   SUM(CASE WHEN close * volume >= ? THEN 1 ELSE 0 END) AS sessions
            FROM prices
            WHERE trade_date >= ? AND trade_date <= ?
            GROUP BY ticker
            """,
            (MIN_DOLLAR_VOLUME, start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    liquid = {t for t, sessions in rows if sessions >= MIN_LIQUID_SESSIONS}
    universe = tuple(t for t in research_universe() if t in liquid)
    logger.info(
        f"tradeable universe: {len(universe):,} tickers traded above "
        f"${MIN_DOLLAR_VOLUME:,.0f} on at least {MIN_LIQUID_SESSIONS} sessions"
    )
    return universe


def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--which",
        choices=("prices", "fundamentals", "merge"),
        required=True,
        help=(
            "merge concatenates saved panel slices into one file. Building "
            "2011-2018 and 2019-2026 separately halves the wait, but the two "
            "write to the same path, and a panel covering only the first "
            "half does not fail when asked about the second — it carries "
            "every 2018 filing forward instead."
        ),
    )
    parser.add_argument(
        "--parts",
        nargs="+",
        default=None,
        help="panel names to merge, e.g. fundamentals_panel_2011_2018 ...",
    )
    parser.add_argument("--start", type=_parse_date, default=PANEL_START)
    parser.add_argument("--end", type=_parse_date, default=PANEL_END)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()

    if args.which == "merge":
        if not args.parts:
            parser.error("--which merge needs --parts")
        frames = []
        for name in args.parts:
            path = panel_path(name)
            if not path.exists():
                parser.error(f"no panel at {path}")
            part = pd.read_parquet(path)
            logger.info(f"{name}: {len(part):,} rows")
            frames.append(part)
        panel = pd.concat(frames).sort_index()
        before = len(panel)
        panel = panel[~panel.index.duplicated(keep="last")]
        if before != len(panel):
            logger.info(f"dropped {before - len(panel):,} duplicate (date, ticker) rows")
        out = panel_path("fundamentals_panel")
        panel.to_parquet(out)
        logger.info(f"wrote {len(panel):,} merged rows to {out}")
        return 0

    if args.which == "prices":
        panel = build_price_panel(PanelSpec(start=args.start, end=args.end))
        out = panel_path("price_panel")
    else:
        tickers = tradeable_universe(args.start, args.end)
        spec_kwargs = {"tickers": tickers, "start": args.start, "end": args.end}
        if args.workers:
            spec_kwargs["workers"] = args.workers
        panel = build_fundamentals_panel(FundamentalsSpec(**spec_kwargs))
        out = panel_path("fundamentals_panel")

    panel.to_parquet(out)
    logger.info(f"wrote {len(panel):,} rows to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
