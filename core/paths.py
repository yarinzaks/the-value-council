"""Single source of truth for Value Council data paths.

Why this module exists
~~~~~~~~~~~~~~~~~~~~~~~

macOS TCC restricts non-interactive processes from reading files under
``~/Documents``. We learned this the hard way: launchd-spawned Python
runs were silently denied access to the EDGAR cache, which made the
daily runner produce zero trades for several days while *appearing*
to succeed.

The permanent fix is to keep all runtime data in a TCC-safe location
(``~/Library/Application Support/value-council/``). Every module that
needs a data path imports from here so we never reintroduce
``PROJECT_ROOT / "data" / ...`` paths.

Resolution order
~~~~~~~~~~~~~~~~

1. ``$VALUE_COUNCIL_DATA_DIR`` — explicit override (set by the launchd
   plist; also handy for tests).
2. ``~/Library/Application Support/value-council/`` — default on macOS.
3. ``$XDG_DATA_HOME/value-council`` — Linux convention.
4. ``<project_root>/data/`` — final fallback for repos without HOME
   (e.g. some CI setups).

The first existing root wins. Subdirectories are created on demand by
the modules that own them.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_data_root() -> Path:
    env = os.environ.get("VALUE_COUNCIL_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    home = os.environ.get("HOME")
    if home:
        # macOS preferred location.
        mac_root = Path(home) / "Library" / "Application Support" / "value-council"
        # Use it if it exists OR if we're on a Mac (common case).
        if mac_root.exists() or _is_macos():
            return mac_root
        # Linux/XDG fallback.
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path(home) / ".local" / "share")
        return Path(xdg) / "value-council"
    # Last resort — project-local. Should rarely fire.
    return PROJECT_ROOT / "data"


def _is_macos() -> bool:
    return os.uname().sysname == "Darwin" if hasattr(os, "uname") else False


# Resolve once at import time. Tests that need a different root can
# either set VALUE_COUNCIL_DATA_DIR before importing or call
# ``data_root()`` to re-resolve.
DATA_ROOT: Path = _resolve_data_root()


def data_root() -> Path:
    """Re-resolve the data root from current env. Useful in tests."""
    return _resolve_data_root()


# Subdirectory accessors. These compute paths lazily off ``DATA_ROOT``
# so a single env-var change cascades everywhere.
def cache_dir() -> Path:
    return DATA_ROOT / "cache"


def fundamentals_cache_dir() -> Path:
    return DATA_ROOT / "fundamentals_cache"


def portfolios_dir() -> Path:
    return DATA_ROOT / "portfolios"


def decisions_dir() -> Path:
    """Decisions the live runner made — the dashboard's journal."""
    return DATA_ROOT / "decisions"


def backtest_decisions_dir() -> Path:
    """Decisions a *backtest* made, kept out of the live journal.

    Both logs share the :class:`~core.backtest.decision_logger.Decision`
    schema and are named by decision date, so writing them to one
    directory put a 2020 rebalance's BUY next to a 2026 live BUY with
    nothing but the date to tell them apart. The dashboard concatenates
    every file in an agent's directory, so a five-year backtest silently
    added its own history to the journal of what the agent actually did.

    Separate roots make the distinction structural rather than something
    each reader has to infer from a year.
    """
    return DATA_ROOT / "backtest_decisions"


def backtest_results_dir() -> Path:
    return DATA_ROOT / "backtest_results"


def cron_logs_dir() -> Path:
    return DATA_ROOT / "cron_logs"


def edgar_filings_db() -> Path:
    return cache_dir() / "edgar_filings.sqlite"


def prices_db() -> Path:
    return cache_dir() / "prices.sqlite"


def universe_index() -> Path:
    return cache_dir() / "full_market_universe_index.json"


def fiscal_calendar_path() -> Path:
    """When each company is next expected to file. Rebuilt annually."""
    return cache_dir() / "fiscal_calendar.json"


def ensure_dirs() -> None:
    """Create all known subdirectories. Idempotent — safe to call from
    any entry point."""
    for d in (
        DATA_ROOT,
        cache_dir(),
        fundamentals_cache_dir(),
        portfolios_dir(),
        decisions_dir(),
        backtest_decisions_dir(),
        backtest_results_dir(),
        cron_logs_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)


__all__ = [
    "DATA_ROOT",
    "PROJECT_ROOT",
    "backtest_decisions_dir",
    "backtest_results_dir",
    "cache_dir",
    "cron_logs_dir",
    "data_root",
    "decisions_dir",
    "edgar_filings_db",
    "ensure_dirs",
    "fundamentals_cache_dir",
    "portfolios_dir",
    "prices_db",
    "universe_index",
]
