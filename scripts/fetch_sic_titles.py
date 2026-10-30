"""Fetch the SEC's SIC code → industry title table.

Output: ``data_bundled/sic_titles.json`` mapping ``"3751" -> "MOTORCYCLES,
BICYCLES & PARTS"``.

Why bundle it
~~~~~~~~~~~~~

``data_bundled/company_sic.json`` already maps ticker → SIC code, which
is enough for industry-relative scoring but not for telling a reader
what a company does: 3751 is a number. The titles are the SEC's own
wording for its own taxonomy, published as one HTML table, and the
taxonomy has been stable for decades — so this is reference data to
fetch once and commit, not something a daily run should depend on.

Checked at the time of writing: 444 codes in the table, and every one of
the 397 distinct codes used by the 8,177 cached tickers that have a code
is present. Coverage is complete, so a missing title means the bundle is
stale rather than that the company is unusual.

Usage::

    .venv/bin/python -m scripts.fetch_sic_titles
    .venv/bin/python -m scripts.fetch_sic_titles --check

``--check`` re-fetches and reports differences without writing, which is
how to tell whether a refresh is needed.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data_bundled" / "sic_titles.json"

#: The list moved from /corpfin/ to /search-filings/ at some point; the
#: old path still answers with a meta-refresh redirect that a plain
#: fetcher will not follow, so the current path is used directly.
SIC_LIST_URL = (
    "https://www.sec.gov/search-filings/"
    "standard-industrial-classification-sic-code-list"
)

#: SEC EDGAR's only hard requirement is a User-Agent identifying the
#: caller. Same string the other prefetch scripts send.
SEC_USER_AGENT = "TheValueCouncil yarinzaks15500@gmail.com"

TIMEOUT_SECONDS = 60


def fetch_html(url: str = SIC_LIST_URL) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="ignore")


def parse_titles(page: str) -> dict[str, str]:
    """Extract ``code -> title`` from the page's table.

    Takes the last cell of each row rather than a fixed column index:
    the table carries an office name between the code and the title, and
    its position has changed before. A row whose first cell is not a
    three- or four-digit code is not a data row.
    """
    titles: dict[str, str] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        ]
        if len(cells) >= 2 and re.fullmatch(r"\d{3,4}", cells[0]) and cells[-1]:
            titles[cells[0]] = cells[-1]
    return titles


def _load_existing() -> dict[str, str]:
    if not OUTPUT_PATH.exists():
        return {}
    return dict(json.loads(OUTPUT_PATH.read_text()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report differences against the bundle without writing",
    )
    args = parser.parse_args(argv)

    titles = parse_titles(fetch_html())
    if not titles:
        print("parsed no codes — the page layout has changed", file=sys.stderr)
        return 1
    print(f"parsed {len(titles)} codes")

    existing = _load_existing()
    added = sorted(set(titles) - set(existing))
    removed = sorted(set(existing) - set(titles))
    changed = sorted(c for c in set(titles) & set(existing) if titles[c] != existing[c])

    if args.check:
        if not (added or removed or changed):
            print("bundle is current")
            return 0
        for code in added:
            print(f"  + {code}: {titles[code]}")
        for code in removed:
            print(f"  - {code}: {existing[code]}")
        for code in changed:
            print(f"  ~ {code}: {existing[code]!r} -> {titles[code]!r}")
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(dict(sorted(titles.items())), indent=1, ensure_ascii=False) + "\n"
    )
    print(
        f"wrote {OUTPUT_PATH.relative_to(PROJECT_ROOT)} "
        f"({len(added)} added, {len(removed)} removed, {len(changed)} changed)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
