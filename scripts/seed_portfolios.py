"""One-time portfolio seeding.

Creates fresh ``data/portfolios/<agent>.json`` files at $10,000 cash
for each of the four configured live agents — but only for those that
don't already have a portfolio. Idempotent and safe to re-run.

The daily runner does this on its own when an agent has no portfolio
file yet, so calling this script is optional. It exists so a fresh
clone can populate the dashboard with seeded portfolios *before* the
first scan completes.

Usage::

    .venv/bin/python -m scripts.seed_portfolios
"""

from __future__ import annotations

import sys

from core.live.portfolio import (
    DEFAULT_INITIAL_CASH,
    DEFAULT_PORTFOLIO_DIR,
    LivePortfolio,
    now_iso,
)


def main() -> int:
    agents = (
        "greenblatt_magic_formula",
        "walter_schloss",
        "benjamin_graham",
        "david_dreman",
    )
    DEFAULT_PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
    seeded = 0
    for agent in agents:
        path = DEFAULT_PORTFOLIO_DIR / f"{agent}.json"
        if path.exists():
            print(f"  ✓ {agent} — already exists, skipping")
            continue
        p = LivePortfolio(
            agent=agent,
            cash=DEFAULT_INITIAL_CASH,
            initial_cash=DEFAULT_INITIAL_CASH,
            last_updated=now_iso(),
        )
        p.save()
        seeded += 1
        print(f"  + {agent} — seeded ${DEFAULT_INITIAL_CASH:,.0f}")
    print(
        f"\nSeeded {seeded} new portfolio(s) (of {len(agents)}). "
        f"All persisted to {DEFAULT_PORTFOLIO_DIR}/"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
