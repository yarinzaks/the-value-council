"""Bundle exchange listings and point-in-time S&P 500 membership.

Fills the two universe rules in ``COUNCIL_SELECTION.md`` section 1 that
had no data source in this project: U1 (US common stock on NYSE /
NASDAQ / AMEX) and U8 (not an S&P 500 member).

Outputs, both read by :mod:`core.data.listings`:

* ``data_bundled/company_exchange.json`` — ``TICKER -> exchange``.
* ``data_bundled/sp500_membership.csv.gz`` — dated membership snapshots
  back to 1996, gzipped (5.5MB of text, 44KB compressed, because
  consecutive daily snapshots are nearly identical).

Both change slowly — exchange listings almost never, index membership a
few dozen times a year — so this is run by hand rather than on a
schedule. Neither file is required for the other eleven agents; a
missing bundle degrades to UNKNOWN, which fails the gate rather than
passing it.

Usage::

    .venv/bin/python -m scripts.prefetch_listings
    .venv/bin/python -m scripts.prefetch_listings --only exchange
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

from core.logger import get_logger

logger = get_logger("scripts.prefetch_listings")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_DIR = PROJECT_ROOT / "data_bundled"

EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
MEMBERSHIP_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
)

#: The SEC returns 403 without a contact string. Their published limit
#: is 10 requests a second; this script makes one.
SEC_USER_AGENT = "TheValueCouncil yarinzaks15500@gmail.com"

#: Sanity floors. The SEC file carried 10,398 rows and the membership
#: file 2,719 snapshots when this was written. A fetch that returns
#: dramatically less has succeeded at the HTTP level and failed at the
#: data level, which is the failure mode that would silently empty the
#: universe on the next run.
MIN_EXCHANGE_ROWS = 5_000
MIN_MEMBERSHIP_SNAPSHOTS = 1_000


class PrefetchError(RuntimeError):
    """A fetch returned something that must not be written to a bundle."""


def _get(url: str, *, user_agent: str | None = None) -> bytes:
    request = urllib.request.Request(url)
    if user_agent:
        request.add_header("User-Agent", user_agent)
    with urllib.request.urlopen(request, timeout=120) as response:
        return bytes(response.read())


def fetch_exchanges() -> dict[str, str]:
    """``TICKER -> exchange`` from the SEC's own listing file.

    Rows with no exchange are dropped rather than stored as null: the
    reader treats an absent ticker as UNKNOWN already, and a null in the
    bundle would be a second way to spell the same thing.
    """
    payload = json.loads(_get(EXCHANGE_URL, user_agent=SEC_USER_AGENT))
    fields = payload["fields"]
    ticker_at = fields.index("ticker")
    exchange_at = fields.index("exchange")

    rows = payload["data"]
    if len(rows) < MIN_EXCHANGE_ROWS:
        raise PrefetchError(
            f"{EXCHANGE_URL} returned {len(rows)} rows, "
            f"below the {MIN_EXCHANGE_ROWS} floor — refusing to write"
        )

    out: dict[str, str] = {}
    for row in rows:
        ticker = str(row[ticker_at] or "").strip().upper()
        exchange = str(row[exchange_at] or "").strip().upper()
        if ticker and exchange:
            out[ticker] = exchange
    return out


def fetch_membership() -> bytes:
    """The historical components file, verbatim.

    Kept as delivered rather than reshaped. It is the only free fix for
    survivorship bias that ``COUNCIL_DATA.md`` names — its 1996 snapshot
    still lists AAMRQ, the bankrupt AMR — and rewriting it here would be
    one more place for that property to be lost.
    """
    raw = _get(MEMBERSHIP_URL)
    snapshots = raw.count(b"\n") - 1  # minus the header
    if snapshots < MIN_MEMBERSHIP_SNAPSHOTS:
        raise PrefetchError(
            f"{MEMBERSHIP_URL} returned {snapshots} snapshots, "
            f"below the {MIN_MEMBERSHIP_SNAPSHOTS} floor — refusing to write"
        )
    if not raw.startswith(b"date,tickers"):
        raise PrefetchError(
            f"{MEMBERSHIP_URL} header is {raw[:40]!r}, expected 'date,tickers'"
        )
    return raw


def _write_atomically(path: Path, write: Callable[[Path], object]) -> None:
    """Write through a temp file so a failed fetch cannot truncate a bundle."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        write(tmp)
        shutil.move(str(tmp), str(path))
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=("exchange", "membership"),
        default=None,
        help="Refresh just one bundle. Default refreshes both.",
    )
    args = parser.parse_args(argv)

    failed = False

    if args.only in (None, "exchange"):
        try:
            exchanges = fetch_exchanges()
        except (PrefetchError, OSError, ValueError, KeyError) as exc:
            logger.error(f"exchange bundle not refreshed: {exc}")
            failed = True
        else:
            path = BUNDLE_DIR / "company_exchange.json"
            _write_atomically(
                path,
                lambda p: p.write_text(json.dumps(exchanges, indent=0, sort_keys=True)),
            )
            counts: dict[str, int] = {}
            for ex in exchanges.values():
                counts[ex] = counts.get(ex, 0) + 1
            summary = ", ".join(
                f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
            )
            logger.info(f"wrote {path.name}: {len(exchanges)} tickers ({summary})")

    if args.only in (None, "membership"):
        try:
            raw = fetch_membership()
        except (PrefetchError, OSError, ValueError) as exc:
            logger.error(f"membership bundle not refreshed: {exc}")
            failed = True
        else:
            path = BUNDLE_DIR / "sp500_membership.csv.gz"
            _write_atomically(
                path,
                lambda p: p.write_bytes(gzip.compress(raw, compresslevel=9)),
            )
            logger.info(
                f"wrote {path.name}: {raw.count(chr(10).encode()) - 1} snapshots, "
                f"{path.stat().st_size / 1024:.0f}KB compressed"
            )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
