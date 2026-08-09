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

from core.data.edgar_cache import EdgarCache
from core.data.ticker_filter import is_common_equity, is_primary_listing
from core.logger import get_logger
from core.paths import DATA_ROOT
from core.research.fundamentals_panel import (
    FundamentalsSpec,
    build_fundamentals_panel,
    panel_path,
)
from core.research.price_panel import PanelSpec, build_price_panel

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


def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--which", choices=("prices", "fundamentals"), required=True)
    parser.add_argument("--start", type=_parse_date, default=PANEL_START)
    parser.add_argument("--end", type=_parse_date, default=PANEL_END)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()

    if args.which == "prices":
        panel = build_price_panel(PanelSpec(start=args.start, end=args.end))
        out = panel_path("price_panel")
    else:
        tickers = research_universe()
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
