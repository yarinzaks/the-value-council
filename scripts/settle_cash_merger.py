"""Settle a holding that was acquired for cash.

Why this is not a sale, and not a rename
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``scripts.migrate_ticker`` follows a position through a symbol change:
the company, the shares and the cost basis all survive, so the line is
relabelled and starts marking again. Its own docstring names the case it
refuses — "a symbol that stopped trading for any other reason — an
acquisition, a bankruptcy, a going-private — is *not* a rename, has no
successor to migrate to" — and names Lynch's THR as the example. This is
the tool for that case.

It is not a sale either. ``DailyRunner`` refuses to sell at a stale
mark, correctly, which leaves an acquired holding permanently stuck: it
cannot be marked, because no bar exists after the suspension, and it
cannot be sold, because the only price available is stale by
definition. Left alone it sits in the book at the last close forever,
counted in NAV at a number that no longer refers to anything.

What actually happened is neither. The shares ceased to exist on the
effective date and were converted into a fixed amount of cash by the
acquirer, at a price stated in the merger agreement rather than
discovered in a market.

Two consequences follow, and both are the reason this is a script and
not a flag on ``sell``:

* **No transaction cost.** The holder does not trade and no broker is
  involved, so charging the usual 10bp would invent a cost nobody paid.
* **The price is an input, not a lookup.** It comes from the filing.
  Nothing in the price cache knows it, and nothing ever will.

What it refuses to do
~~~~~~~~~~~~~~~~~~~~~

Settle a symbol that is still trading. If the cache holds a bar on or
after the effective date, the premise is wrong — either the date is
wrong or the company was not acquired — and quietly converting a live
holding to cash at a made-up price would be far worse than doing
nothing. It also refuses a non-positive price, and reports rather than
writes unless ``--apply`` is passed.

It does not handle stock or mixed consideration. Receiving shares of
the acquirer is a new position in a company no agent's doctrine ever
examined, and inventing that holding is a decision this script has no
business making.

Usage::

    .venv/bin/python -m scripts.settle_cash_merger THR 63.89 2026-06-01
    .venv/bin/python -m scripts.settle_cash_merger THR 63.89 2026-06-01 --apply
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date

from core.backtest.data_loader import PriceDataLoader
from core.live.portfolio import LivePortfolio
from core.logger import get_logger
from core.paths import portfolios_dir

logger = get_logger("scripts.settle_cash_merger")

#: A merger cash-out carries no commission: there is no trade and no
#: broker, only a conversion the acquirer performs.
MERGER_COST_BPS = 0.0


class StillTradingError(Exception):
    """The symbol has bars on or after the effective date."""


@dataclass(frozen=True)
class Settlement:
    """What one book received."""

    agent: str
    shares: float
    entry_price: float
    entry_date: str
    proceeds: float
    realized_pnl: float

    @property
    def realized_pct(self) -> float:
        basis = self.shares * self.entry_price
        return (self.realized_pnl / basis) * 100.0 if basis else 0.0


def verify_delisted(
    ticker: str,
    effective: date,
    *,
    loader: PriceDataLoader | None = None,
) -> list[str]:
    """Raise unless ``ticker`` stopped pricing before ``effective``.

    Returns the evidence lines, so the operator sees what was checked
    rather than a bare yes.
    """
    loader = loader or PriceDataLoader()
    cached = loader.cached_range(ticker)
    if cached is None:
        raise StillTradingError(
            f"no cached price history for {ticker} — cannot confirm it ever "
            "traded, let alone that it stopped"
        )
    first, last = cached
    last_date = date.fromisoformat(str(last)) if not isinstance(last, date) else last
    if last_date >= effective:
        raise StillTradingError(
            f"{ticker} priced on {last_date}, on or after the stated effective "
            f"date {effective} — it is still trading. Either the date is wrong "
            "or this company was not acquired."
        )
    return [
        f"{ticker} priced {first} through {last_date}",
        f"no bar on or after the effective date {effective} "
        f"({(effective - last_date).days} days of silence)",
    ]


def settle(
    ticker: str,
    cash_per_share: float,
    *,
    apply: bool = False,
    directory=None,
) -> list[Settlement]:
    """Convert every holding of ``ticker`` to cash. Returns what each got."""
    if cash_per_share <= 0:
        raise ValueError(f"non-positive cash consideration: {cash_per_share}")

    books = directory or portfolios_dir()
    settled: list[Settlement] = []

    for path in sorted(books.glob("*.json")):
        portfolio = LivePortfolio.load_or_seed(path.stem, directory=books)
        held = next(
            (p for p in portfolio.positions if p.ticker.upper() == ticker.upper()),
            None,
        )
        if held is None:
            continue

        record = portfolio.sell(
            held.ticker, price=cash_per_share, cost_bps=MERGER_COST_BPS
        )
        settled.append(
            Settlement(
                agent=path.stem,
                shares=record.shares,
                entry_price=held.entry_price,
                entry_date=held.entry_date,
                proceeds=record.gross_value,
                realized_pnl=record.realized_pnl_usd,
            )
        )
        if apply:
            portfolio.save(directory=books)

    return settled


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    if len(args) != 3:
        print(__doc__)
        return 2

    ticker = args[0].upper()
    try:
        cash = float(args[1])
        effective = date.fromisoformat(args[2])
    except ValueError as exc:
        print(f"bad argument: {exc}")
        return 2

    try:
        evidence = verify_delisted(ticker, effective)
    except StillTradingError as exc:
        print(f"REFUSED: {ticker} at ${cash:.2f} effective {effective}")
        print(f"  {exc}")
        return 1

    print(f"{ticker} acquired for cash at ${cash:.2f}/share, effective {effective}")
    for line in evidence:
        print(f"  ✓ {line}")
    print()

    try:
        settled = settle(ticker, cash, apply=apply)
    except ValueError as exc:
        print(f"REFUSED: {exc}")
        return 1

    if not settled:
        print(f"No book holds {ticker}. Nothing to settle.")
        return 0

    for s in settled:
        print(
            f"  {s.agent:26} {s.shares:.2f} sh @ {s.entry_price:.2f} "
            f"from {s.entry_date} -> ${s.proceeds:,.2f} cash "
            f"({s.realized_pnl:+,.2f}, {s.realized_pct:+.2f}%)"
        )

    print()
    print(f"{len(settled)} book(s) affected. No commission charged — a merger")
    print("conversion is not a trade.")
    print("Written." if apply else "Report only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
