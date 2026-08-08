"""Follow a position through a ticker rename.

Why a rename is not a trade
~~~~~~~~~~~~~~~~~~~~~~~~~~~

When an issuer changes its symbol, the company, the shares and the cost
basis are all unchanged — only the label moved. The books held ASGN,
whose issuer now files as EFOR: the same SIC and the identical set of 58
SEC accession numbers. The old symbol simply stopped producing bars, so
four agents carried a position frozen at a mark from seven weeks
earlier — $19.08 against EFOR's $32.22, understating it by 69%.

Selling it would be worse than leaving it. There is no counterparty and
no price: the last ASGN bar is stale by definition, and
``DailyRunner`` already refuses to sell at a stale mark for exactly
this reason. Booking a sale would invent a trade that never happened
and a realised P&L that nobody earned.

So this renames the symbol in place. Shares, entry price and entry date
carry over untouched; the position simply starts marking again.

What it refuses to do
~~~~~~~~~~~~~~~~~~~~~

Migrate two symbols that are not demonstrably the same issuer. They
must share a SIC code, carry the identical set of SEC accession numbers
— one submission cannot belong to two registrants — and the destination
must have priced more recently than the source.

Deliberately not a name comparison. The SEC's name map lists *current*
registrants, so the symbol being retired has already dropped out of it,
which is precisely the case this script exists for. Requiring a name
match refuses every real migration.

A symbol that stopped trading for any other reason — an acquisition, a
bankruptcy, a going-private — is *not* a rename, has no successor to
migrate to, and needs a decision this script has no business making.
Lynch's THR is one: it fails on SIC, and its last three bars fall from
$69.21 to $61.14 on five times normal volume before trading stops.

Usage::

    .venv/bin/python -m scripts.migrate_ticker ASGN EFOR
    .venv/bin/python -m scripts.migrate_ticker ASGN EFOR --apply
"""

from __future__ import annotations

import sqlite3
import sys

from core.backtest.data_loader import PriceDataLoader
from core.data.company_names import company_name
from core.data.sic_codes import sic_for
from core.live.portfolio import LivePortfolio
from core.logger import get_logger
from core.paths import edgar_filings_db, portfolios_dir

logger = get_logger("scripts.migrate_ticker")


class NotTheSameIssuerError(Exception):
    """The two symbols cannot be shown to be one company."""


def _accessions(ticker: str) -> set[str]:
    """Every SEC accession number cached under ``ticker``."""
    with sqlite3.connect(edgar_filings_db()) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT accession_number FROM filings WHERE ticker = ?",
                (ticker.upper(),),
            )
        }


def verify_same_issuer(old: str, new: str) -> list[str]:
    """Raise unless ``old`` and ``new`` are demonstrably one issuer.

    Returns the evidence lines, so the operator sees what was checked
    rather than a bare yes.
    """
    evidence: list[str] = []

    old_sic, new_sic = sic_for(old), sic_for(new)
    if old_sic is None or new_sic is None:
        raise NotTheSameIssuerError(
            f"no SIC for {old if old_sic is None else new} — cannot verify"
        )
    if old_sic != new_sic:
        raise NotTheSameIssuerError(f"SIC differs: {old}={old_sic}, {new}={new_sic}")
    evidence.append(f"SIC matches: {old_sic}")

    # The conclusive test. An accession number identifies one SEC
    # submission; two labels carrying the identical set of them are one
    # registrant, whatever any name map says. This is deliberately not a
    # name comparison: the SEC's name map lists *current* registrants,
    # so a symbol that has just been retired — exactly the case this
    # script exists for — has already dropped out of it.
    old_accs, new_accs = _accessions(old), _accessions(new)
    if not old_accs or not new_accs:
        raise NotTheSameIssuerError(
            f"no cached filings for {old if not old_accs else new} — cannot verify"
        )
    if old_accs != new_accs:
        shared = len(old_accs & new_accs)
        raise NotTheSameIssuerError(
            f"filing histories differ: {len(old_accs)} vs {len(new_accs)} "
            f"accessions, {shared} shared"
        )
    evidence.append(f"identical filing history: {len(old_accs)} accessions")

    # Supporting only. Present for the successor, absent for the symbol
    # being retired — which is why it cannot be a gate.
    new_name = company_name(new)
    if new_name:
        evidence.append(f"{new} files as: {new_name}")

    loader = PriceDataLoader()
    old_range, new_range = loader.cached_range(old), loader.cached_range(new)
    if new_range is None:
        raise NotTheSameIssuerError(f"{new} has no price history — nothing to migrate to")
    if old_range is not None and new_range[1] <= old_range[1]:
        raise NotTheSameIssuerError(
            f"{new} last priced {new_range[1]}, no later than {old} at "
            f"{old_range[1]} — this is not a live successor"
        )
    evidence.append(f"{new} prices through {new_range[1]}, {old} stops at "
                    f"{old_range[1] if old_range else 'never'}")
    return evidence


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    if len(args) != 2:
        print(__doc__)
        return 2
    old, new = args[0].upper(), args[1].upper()

    try:
        evidence = verify_same_issuer(old, new)
    except NotTheSameIssuerError as exc:
        print(f"REFUSED: {old} -> {new}")
        print(f"  {exc}")
        print()
        print("A symbol that stopped trading for any reason other than a")
        print("rename has no successor to migrate to. Closing such a")
        print("position is a decision this script will not make for you.")
        return 1

    print(f"{old} -> {new}")
    for line in evidence:
        print(f"  ✓ {line}")
    print()

    touched = 0
    for path in sorted(portfolios_dir().glob("*.json")):
        portfolio = LivePortfolio.load_or_seed(path.stem, directory=portfolios_dir())
        before = next(
            (p for p in portfolio.positions if p.ticker.upper() == old), None
        )
        also_held = any(p.ticker.upper() == new for p in portfolio.positions)

        # LivePortfolio owns the merge, because it owns the invariant:
        # one line per ticker. Relabelling the list here instead left
        # Klarman holding two EFOR positions, and _index_of only ever
        # sees the first.
        hit = portfolio.rename_ticker(old, new)

        if hit and before is not None:
            note = " (merged into existing line)" if also_held else ""
            print(
                f"  {path.stem:26} {before.shares:.2f} sh @ "
                f"{before.entry_price:.2f} from {before.entry_date}{note}"
            )

        if hit:
            touched += 1
            if apply:
                portfolio.save(directory=portfolios_dir())

    print()
    print(f"{touched} book(s) affected.")
    print("Written." if apply else "Report only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
