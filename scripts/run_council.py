"""Run the Council's deterministic half and publish what it found.

Usage::

    .venv/bin/python -m scripts.run_council --mode heartbeat
    .venv/bin/python -m scripts.run_council --mode close

Both modes read the book, check Part 4, and watch the filings of what is
held. Neither trades. The Council's own portfolio is seeded like every
other agent's and stays in cash until a human approves a position, which
is what its doctrine requires and not a limitation of this script.

What it writes
~~~~~~~~~~~~~~

``data/council/runs/<date>_<mode>.json``   the full run record
``data/council/state.json``                what the dashboard reads

The two are separate because they answer different questions. The run
record is the audit trail — one file per run, never overwritten, the
thing a reader consults in two years to judge whether the process was
sound. The state file is a single current view, rewritten every run,
and holds nothing that is not derivable from the records beside it.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime

from agents.council.journal import Journal
from agents.council.journal import summary as journal_summary
from agents.council.regime import read_regime
from agents.council.runs import AGENT_SLUG, Book, close, heartbeat, write_result
from core.backtest.data_loader import PriceDataLoader
from core.live.portfolio import LivePortfolio
from core.logger import get_logger
from core.paths import DATA_ROOT, portfolios_dir

logger = get_logger("scripts.run_council")

COUNCIL_DIR = DATA_ROOT / "council"
RUNS_DIR = COUNCIL_DIR / "runs"
STATE_PATH = COUNCIL_DIR / "state.json"

#: Sessions the liquidity measure looks over. Matches what the other
#: agents use, so "illiquid" means the same thing across the dashboard.
LIQUIDITY_SESSIONS = 63


def load_book(as_of: date) -> Book:
    """The Council's own portfolio, with liquidity measured where it can be."""
    portfolio = LivePortfolio.load_or_seed(AGENT_SLUG, directory=portfolios_dir())
    # Persisted, not just held in memory. load_or_seed happily invents a
    # $10,000 book on every run and forgets it again, which left the
    # Council the one agent with no file under data/portfolios — and the
    # dashboard builds every live view by reading that directory, so it
    # was absent from the cards, the ranking and the totals no matter
    # what its state file said. Writing it here costs nothing when the
    # book is unchanged and makes the agent visible on the same terms as
    # the other eleven.
    portfolio.save(directory=portfolios_dir())
    positions = [
        {
            "ticker": p.ticker,
            "shares": p.shares,
            "current_price": p.current_price,
        }
        for p in portfolio.positions
    ]
    nav = portfolio.total_nav

    adv: dict[str, float] = {}
    if positions:
        try:
            adv = PriceDataLoader().median_dollar_volume(
                [p["ticker"] for p in positions], as_of, sessions=LIQUIDITY_SESSIONS
            )
        except Exception as exc:
            # Reported rather than swallowed: without it the illiquidity
            # limit reports UNKNOWN, which is the honest outcome and is
            # visible on the dashboard.
            logger.warning(f"liquidity unavailable — {exc}")

    peak = _peak_nav(nav)
    return Book(
        nav=nav,
        cash=portfolio.cash,
        peak_nav=peak,
        positions=positions,
        adv=adv,
    )


def _peak_nav(current: float) -> float:
    """Highest NAV ever recorded, so the drawdown means something.

    Kept in the state file rather than recomputed, because the record of
    a peak is the only thing that survives the drawdown itself.
    """
    previous = 0.0
    if STATE_PATH.exists():
        try:
            previous = float(json.loads(STATE_PATH.read_text()).get("peak_nav", 0.0))
        except (OSError, ValueError, TypeError):
            previous = 0.0
    return max(previous, current)


def publish_state(result: dict, book: Book, journal: Journal) -> None:
    """One current view for the dashboard."""
    state = {
        "agent": AGENT_SLUG,
        "updated": datetime.now(UTC).isoformat(timespec="seconds"),
        "run": result["run"],
        "nav": book.nav,
        "cash": book.cash,
        "cash_weight": book.cash_weight,
        "peak_nav": book.peak_nav,
        "positions": len(book.positions),
        "drawdown_from_peak": result.get("drawdown_from_peak"),
        "circuit_breaker": result.get("circuit_breaker", False),
        "all_clear": result.get("all_clear"),
        "limits": result.get("limits", []),
        "breaches": result.get("breaches", []),
        "unknown_limits": result.get("unknown_limits", []),
        "filings_flagged": result.get("filings_flagged", []),
        "regime": result.get("regime"),
        "journal": journal_summary(journal),
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1, sort_keys=True))
    logger.info(f"published {STATE_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("heartbeat", "close"), required=True)
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD, defaults to today")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    book = load_book(as_of)
    journal = Journal()

    if args.mode == "heartbeat":
        result = heartbeat(book, journal=journal)
    else:
        result = close(book, journal=journal, regime=read_regime(as_of))

    write_result(result, directory=RUNS_DIR)
    publish_state(result, book, journal)

    print(f"{args.mode}: nav ${book.nav:,.2f}, {len(book.positions)} positions")
    if result["run"] == "heartbeat":
        print(f"  all clear: {result['all_clear']}")
        for b in result["breaches"]:
            print(f"  BREACH {b['limit']} — {b['observed']} vs {b['cap']}")
    else:
        dial = result["regime"]
        print(f"  regime: {dial['risk_on_count']}/4 risk-on")
    for e in result.get("filings_flagged", []):
        print(f"  filing {e['ticker']} {e['code']} [{e['severity']}] {e['meaning']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
