"""Export a price series per held ticker, for the dashboard to draw.

Why this exists
~~~~~~~~~~~~~~~

The dashboard reads JSON from the data root; prices live in a SQLite
cache the Node process has no reader for. So a position page can show
what an agent paid and what the mark is now, and nothing in between —
the shape of the holding is invisible.

This writes one small file per ticker any agent currently holds, so the
UI can draw the line from entry to today with the entry marked on it.
Held names only: the universe is 6,601 tickers and nobody is looking at
the ones nobody owns.

What it deliberately does not do
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

No network. Every value comes from bars already cached by the day's
run, so this cannot slow a run down or fail it. A ticker with no cached
history is skipped rather than fetched, and its absence is what the UI
reads as "no chart available" — the same
absence-is-not-evidence rule applied everywhere else.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from core.backtest.data_loader import PriceDataLoader
from core.live.portfolio import LivePortfolio
from core.logger import get_logger
from core.paths import DATA_ROOT

logger = get_logger("core.live.price_export")

PRICES_DIR: Path = DATA_ROOT / "prices"

#: How much history to publish per ticker. A year covers the 52-week
#: range Schloss's entry condition speaks in, and keeps each file to a
#: few hundred points.
DEFAULT_WINDOW_DAYS: int = 365


def export_prices(
    portfolios: list[LivePortfolio],
    *,
    as_of: date,
    loader: PriceDataLoader | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    root: Path = PRICES_DIR,
) -> int:
    """Write ``{TICKER}.json`` for every ticker held by any portfolio.

    Returns the number of files written. Each file is::

        {"ticker": "AAPL", "as_of": "2026-08-07",
         "points": [{"d": "2026-08-06", "c": 249.06}, ...]}

    Short keys because the file is read, not edited, and a 250-point
    series repeats them 250 times.
    """
    price_loader = loader or PriceDataLoader()
    tickers = sorted(
        {p.ticker for portfolio in portfolios for p in portfolio.positions}
    )
    if not tickers:
        return 0

    root.mkdir(parents=True, exist_ok=True)
    start = as_of - timedelta(days=window_days)
    written = 0
    for ticker in tickers:
        try:
            df = price_loader.get_history(ticker, start=start, end=as_of)
        except Exception as exc:
            # One unreadable ticker must not cost the others their charts.
            logger.debug(f"price export {ticker}: {exc}")
            continue
        if df.empty or "adj_close" not in df:
            continue
        points = [
            {"d": idx.date().isoformat(), "c": round(float(v), 4)}
            for idx, v in df["adj_close"].items()
            if v == v  # drop NaN
        ]
        if not points:
            continue
        (root / f"{ticker}.json").write_text(
            json.dumps(
                {"ticker": ticker, "as_of": as_of.isoformat(), "points": points}
            )
        )
        written += 1

    logger.info(f"{as_of}: exported price series for {written}/{len(tickers)} held tickers")
    return written


__all__ = ["DEFAULT_WINDOW_DAYS", "PRICES_DIR", "export_prices"]
