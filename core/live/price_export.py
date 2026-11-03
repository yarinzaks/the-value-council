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

The benchmarks
~~~~~~~~~~~~~~

``SPY`` and ``QQQ`` are written beside the holdings, because the
dashboard's performance chart has promised a comparison since it was
written and had no data for it. The only benchmark series in the repo
is the backtest's, which ends 2024-12-31, while the live snapshots
start 2026-04-01: disjoint by construction, so every lookup missed and
the chart rendered an empty line under a "vs S&P 500" reading of "—".

Writing them as ordinary price files fixes that with the machinery
already here, and the year-wide window means the comparison arrives
with history behind it rather than starting from the day it shipped.

What it deliberately does not do
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Almost no network. A held ticker's bars were cached by the day's own
run, so it is served from the cache; one with no history is skipped
rather than fetched, and its absence is what the UI reads as "no chart
available" — the same absence-is-not-evidence rule applied everywhere
else.

The two benchmarks are the exception, and only on the first run after
this shipped: they are in no agent's universe, so nothing else caches
them, and ``PriceDataLoader.get_history`` fetches a missing range and
stores it. Two symbols, once. The call site treats this whole export
as presentation and swallows its errors, so a failed fetch costs a
chart line and nothing else.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
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

#: Written alongside the held names so the performance chart can draw
#: a comparison. SPY tracks the S&P 500 and QQQ the Nasdaq-100.
#:
#: A note on reading them. These agents buy value — low P/B, low P/E,
#: industrials and banks — and the Nasdaq-100 is growth. A gap against
#: QQQ measures which style the market favoured this season at least as
#: much as it measures the agent, so it is the weaker of the two
#: yardsticks and is here because a reader asked to see it.
DEFAULT_BENCHMARKS: tuple[str, ...] = ("SPY", "QQQ")


def export_prices(
    portfolios: list[LivePortfolio],
    *,
    as_of: date,
    loader: PriceDataLoader | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    root: Path = PRICES_DIR,
    benchmarks: Sequence[str] = DEFAULT_BENCHMARKS,
) -> int:
    """Write ``{TICKER}.json`` for every held ticker, and the benchmarks.

    Returns the number of files written. Each file is::

        {"ticker": "AAPL", "as_of": "2026-08-07",
         "points": [{"d": "2026-08-06", "c": 249.06}, ...]}

    Short keys because the file is read, not edited, and a 250-point
    series repeats them 250 times.
    """
    price_loader = loader or PriceDataLoader()
    held = {p.ticker for portfolio in portfolios for p in portfolio.positions}
    wanted_benchmarks = {b.upper() for b in benchmarks}
    # A union, so a benchmark an agent happens to hold is fetched once.
    tickers = sorted(held | wanted_benchmarks)
    if not tickers:
        return 0

    root.mkdir(parents=True, exist_ok=True)
    start = as_of - timedelta(days=window_days)
    written = 0
    for ticker in tickers:
        is_benchmark = ticker in wanted_benchmarks
        try:
            df = price_loader.get_history(ticker, start=start, end=as_of)
        except Exception as exc:
            # One unreadable ticker must not cost the others their
            # charts. A benchmark is louder than a holding: every agent
            # page reads the same two files, so one failure takes the
            # comparison off thirteen pages, and at DEBUG nobody would
            # see it until a reader noticed the line was gone.
            if is_benchmark:
                logger.warning(f"benchmark export {ticker} failed: {exc}")
            else:
                logger.debug(f"price export {ticker}: {exc}")
            continue
        if df.empty or "adj_close" not in df:
            if is_benchmark:
                logger.warning(f"benchmark export {ticker}: no usable history")
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

    logger.info(
        f"{as_of}: exported price series for {written}/{len(tickers)} tickers "
        f"({len(held)} held, {len(benchmarks)} benchmark)"
    )
    return written


__all__ = ["DEFAULT_WINDOW_DAYS", "PRICES_DIR", "export_prices"]
