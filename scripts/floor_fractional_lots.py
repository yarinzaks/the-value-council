"""Bring the live books down to whole shares.

Why the books hold fractions at all
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Between b07ec6f5 and 786c1878, sizing bought fractional shares. The
reasoning was about brokers in general — most US retail brokers have
offered fractional shares for years — and was never checked against the
one account these books exist to be copied into, which sells whole
shares.

Sizing now buys whole shares. The positions bought before it do not
change by themselves: a full exit sells its line entire, so a fractional
lot leaves the book the first time its name is dropped, but until then
the dashboard shows a holding nobody can place. 234 of 266 positions
across the thirteen books are fractional, and 45 of them are *under one
share* — Lam Research at 0.007184 shares, which is $2, and which the
dashboard already renders as "0 shares" because it formats share counts
as integers.

What this does
~~~~~~~~~~~~~~

Floors every lot and credits the fraction's market value to cash at the
position's current mark. A lot that floors to zero shares is closed and
its whole value goes to cash.

Deliberately not booked as trades. There are 234 of them, they are one
administrative act rather than 234 decisions, and 234 rows of
micro-sales in the ledger would bury the agents' real trading. The
daily ledger already records what each book did on its own days; this
is the correction of a modelling error, and the commit is its record.

NAV-neutral by construction: value leaves `invested` and arrives in
`cash` at the same mark, in the same pass. The recorded return history
is therefore untouched — no day's NAV moves, so nothing in the
published series is restated. What changes is the *composition* of
today's book, and the next daily run re-sizes it: `reachable_targets`
and `plan_whole_share_buys` put the freed cash back into whole shares
of the names each agent can reach.

Realised P&L is not credited on the floored fractions. Crediting it
would restate `cumulative_return_pct` on a trade nobody made; leaving
it means the fraction's gain stays unrealised and simply stops being
tracked, which understates nothing because the cash it became is
counted at the same value.

Usage::

    .venv/bin/python -m scripts.floor_fractional_lots
    .venv/bin/python -m scripts.floor_fractional_lots --apply
"""

from __future__ import annotations

import math
import sys

from core.live.portfolio import LivePortfolio, Position
from core.logger import get_logger
from core.paths import portfolios_dir

logger = get_logger("scripts.floor_fractional_lots")

#: Books to walk. Read from disk rather than from a registry so a book
#: the registry has not heard of is still corrected.
def _agent_slugs() -> list[str]:
    return sorted(
        path.stem
        for path in portfolios_dir().glob("*.json")
        if not path.stem.startswith(".")
    )


def floor_one(portfolio: LivePortfolio) -> tuple[float, list[tuple[str, float, float, float]]]:
    """Floor every lot in ``portfolio``; return the cash freed and a log.

    Each log row is ``(ticker, shares_before, shares_after, cash_freed)``.
    The portfolio is mutated in place and **not** saved — the caller
    decides whether this is a dry run.
    """
    freed = 0.0
    changes: list[tuple[str, float, float, float]] = []
    kept: list[Position] = []

    for pos in portfolio.positions:
        whole = float(math.floor(pos.shares))
        if whole == pos.shares:
            kept.append(pos)
            continue
        mark = pos.current_price
        if mark <= 0:
            # No fresh mark means no honest value for the fraction, and
            # the runner refuses to sell at a stale one for the same
            # reason. Left alone, to be corrected once it prices again.
            logger.warning(
                f"{portfolio.agent}/{pos.ticker}: {pos.shares} shares at no "
                f"mark — left fractional"
            )
            kept.append(pos)
            continue
        cash = (pos.shares - whole) * mark
        freed += cash
        changes.append((pos.ticker, pos.shares, whole, cash))
        if whole <= 0:
            continue  # the lot was never a share; it closes
        pos.shares = whole
        kept.append(pos)

    portfolio.positions = kept
    # Not rounded to the cent. A fraction of a share is not a whole
    # number of cents — 0.007184 of Lam Research at $239.11 is
    # $1.71757 — and rounding it moved three books' cumulative return
    # in the fourth decimal place. The point of this migration is that
    # it restates nothing, so the cent stays where the arithmetic puts
    # it.
    portfolio.cash += freed
    return freed, changes


def main() -> int:
    apply = "--apply" in sys.argv
    directory = portfolios_dir()
    slugs = _agent_slugs()
    if not slugs:
        print(f"no portfolios under {directory}")
        return 2

    print(f"{'agent':<26}{'lots':>6}{'closed':>8}{'cash freed':>13}{'NAV before':>12}{'NAV after':>12}")
    print("-" * 77)
    total_freed = 0.0
    total_closed = 0
    for slug in slugs:
        book = LivePortfolio.load_or_seed(slug, directory=directory)
        nav_before = book.total_nav
        freed, changes = floor_one(book)
        closed = sum(1 for _, _, after, _ in changes if after <= 0)
        # Re-mark so `invested`, `total_nav` and the weights agree with
        # the new share counts. The marks are the ones already on the
        # positions: this is not a price refresh.
        book.mark_to_market({p.ticker: p.current_price for p in book.positions})
        nav_after = book.total_nav
        if abs(nav_after - nav_before) > 0.05:
            print(
                f"\nREFUSED: {slug} NAV moved ${nav_before:,.2f} -> "
                f"${nav_after:,.2f}; flooring must be NAV-neutral"
            )
            return 1
        total_freed += freed
        total_closed += closed
        print(
            f"{slug:<26}{len(changes):>6}{closed:>8}{freed:>13,.2f}"
            f"{nav_before:>12,.2f}{nav_after:>12,.2f}"
        )
        if apply:
            book.save(directory=directory)

    print("-" * 77)
    print(f"{'TOTAL':<26}{'':>6}{total_closed:>8}{total_freed:>13,.2f}")
    print()
    if apply:
        print(f"written to {directory}")
    else:
        print("dry run — nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
