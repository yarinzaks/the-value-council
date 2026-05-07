"""Seed $10K paper portfolios for the 6 hybrid council agents.

After PRs #2-#7 merged, the 6 hybrid agents (Neff, Buffett, Lynch,
Marks, Klarman, Fisher) need their live paper-trading portfolios
seeded so the dashboard can render them. The 4 original quant agents
already have portfolios under ``data/portfolios/``.

This script is idempotent: existing portfolio files are NOT
overwritten. Re-running after a partial seed will only create the
missing files.

Usage::

    .venv/bin/python -m scripts.seed_hybrid_portfolios

After running, ``data/portfolios/`` should contain 10 JSON files,
each $10,000 cash, no positions, no watchlist — total council
capital of $100,000.
"""

from __future__ import annotations

import json

from core.live.portfolio import (
    DEFAULT_PORTFOLIO_DIR,
    LivePortfolio,
    now_iso,
)
from core.logger import get_logger

logger = get_logger("scripts.seed_hybrid_portfolios")


# Slugs match each agent's class ``name`` attribute, which is also
# the JSON filename used by ``LivePortfolio.load_or_seed``.
HYBRID_AGENTS: tuple[str, ...] = (
    "john_neff",
    "warren_buffett",
    "peter_lynch",
    "howard_marks",
    "seth_klarman",
    "philip_fisher",
)

INITIAL_CASH: float = 10_000.0


def main() -> None:
    DEFAULT_PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
    seeded: list[str] = []
    skipped: list[str] = []

    for slug in HYBRID_AGENTS:
        path = DEFAULT_PORTFOLIO_DIR / f"{slug}.json"
        if path.exists():
            skipped.append(slug)
            continue
        portfolio = LivePortfolio(
            agent=slug,
            cash=INITIAL_CASH,
            initial_cash=INITIAL_CASH,
            last_updated=now_iso(),
        )
        portfolio.save()
        seeded.append(slug)

    if seeded:
        logger.info(f"seeded {len(seeded)} portfolios: {', '.join(seeded)}")
    if skipped:
        logger.info(
            f"skipped {len(skipped)} existing: {', '.join(skipped)}"
        )

    # Summary: show all 10 portfolios with their initial cash, and the
    # council total.
    all_files = sorted(DEFAULT_PORTFOLIO_DIR.glob("*.json"))
    total_nav = 0.0
    print()
    print("=" * 64)
    print("  Council paper portfolios")
    print("=" * 64)
    print(f"  {'Agent':<32}{'NAV':>12}{'Cash':>12}{'Status':>8}")
    print("-" * 64)
    for path in all_files:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  {path.name:<32}  load failed: {exc}")
            continue
        nav = float(data.get("total_nav", 0.0))
        cash = float(data.get("cash", 0.0))
        status = "new" if path.stem in seeded else "existing"
        total_nav += nav
        print(
            f"  {path.stem:<32}{nav:>12,.2f}{cash:>12,.2f}{status:>8}"
        )
    print("-" * 64)
    print(f"  {'TOTAL':<32}{total_nav:>12,.2f}")
    print("=" * 64)


if __name__ == "__main__":
    main()
