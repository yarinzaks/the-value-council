"""Put one ticker through Graham's own screen and say what it found.

Why this exists
~~~~~~~~~~~~~~~

Asked "what do the agents say about XMTR", the assistant could only
answer that no agent had said anything — true, because XMTR is not in
any book, watchlist or journal row. The question behind it was a
different one: *what would they say*, if they looked.

For Graham that question is answerable exactly and for nothing, because
his doctrine is a set of absolute thresholds rather than a rank. P/E
<= 15, P/B <= 1.5, a current ratio of 2, twenty unbroken years of
dividends: every one of those is a fact about the company alone.
``agents.graham.playbook_audit`` already encodes them as the playbook
writes them, and ``scripts/run_graham_audit`` already runs them over
the whole market. This runs the same code over one name.

What it is NOT
~~~~~~~~~~~~~~

Not a second implementation of the doctrine. It imports ``audit`` and
the market-rate fetchers from the whole-market script, so a change to
the rules reaches both or neither. The one thing it adds is the
plumbing to assemble the three inputs for a single ticker.

Not an answer for every agent. Graham, Schloss, Buffett, Fisher and
Klarman judge a company against fixed thresholds; Greenblatt's Magic
Formula ranks earnings yield against the other 3,594 names, Dreman
wants the bottom fifth of the market's P/E, and Neff compares against
the market's average multiple. A rank cannot be computed from one
company, and inventing a verdict for those agents would be the same
mistake as telling a reader a position has a sell target when the row
names none. The daily run already computes those ranks over the whole
universe; capturing them is a separate change, and this file says so
rather than guessing.

Data
~~~~

* Fundamentals come from the local EDGAR cache — 8,290 tickers, free,
  no network. A ticker outside it cannot be screened here.
* The price comes from the local price store when the name is one the
  agents already follow, and from the NASDAQ screener otherwise.
* AAA and 20-year Treasury yields come from FRED, because the Graham
  Number's coefficient and the EPV rate move with them. Two small CSV
  requests, the same ones the whole-market script makes.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import requests

from agents.graham.playbook_audit import AuditResult, audit
from core.config import get_settings
from core.data.edgar_cache import EdgarCache
from core.live.hebrew_terms import is_hybrid, translate
from core.live.rtl import isolate_runs
from core.logger import get_logger
from core.paths import data_root
from scripts.run_graham_audit import (
    NASDAQ_SCREENER_URL,
    NASDAQ_USER_AGENT,
    _get,
    fetch_rates,
    parse_nasdaq_quotes,
)

logger = get_logger(__name__)

#: Agents whose doctrine is a rank or a market-relative threshold. A
#: single company carries no answer for them; see the module docstring.
#:
#: The disclaimer at the end of every verdict is built from this, so an
#: agent added here is named there without a second edit — two lists
#: that must agree are two lists that will not.
RELATIVE_AGENTS = {
    "greenblatt": ("Greenblatt", "גרינבלט"),
    "dreman": ("Dreman", "דרמן"),
    "neff": ("Neff", "נף"),
    "marks": ("Marks", "מארקס"),
}


class TickerScreenError(Exception):
    """The ticker cannot be screened, with the reason a reader needs."""


def local_price(ticker: str) -> tuple[float, str] | None:
    """The newest close from the store the dashboard reads, if present."""
    path = data_root() / "prices" / f"{ticker}.json"
    try:
        points = json.loads(path.read_text())["points"]
    except (OSError, KeyError, ValueError):
        return None
    if not points:
        return None
    last = points[-1]
    try:
        return float(last["c"]), str(last["d"])
    except (KeyError, TypeError, ValueError):
        return None


def screen(ticker: str, *, session: requests.Session) -> tuple[AuditResult, str]:
    """Graham's verdict on ``ticker``, and the date of the price used."""
    ticker = ticker.upper().strip()
    if not ticker:
        raise TickerScreenError("no ticker given")

    cache = EdgarCache()
    if ticker not in set(cache.tickers()):
        raise TickerScreenError(
            f"{ticker} has no filings in the local EDGAR cache, so there is "
            "nothing to measure. The cache holds the names the daily run has "
            "seen; a newly listed or foreign-filing company will not be in it."
        )

    priced = local_price(ticker)
    industry = ""
    if priced is not None:
        price, as_of = priced
    else:
        # Not a name the agents follow. One bulk request, the same the
        # whole-market audit makes.
        logger.info(f"{ticker}: no local price; asking the NASDAQ screener")
        quotes = parse_nasdaq_quotes(
            _get(session, NASDAQ_SCREENER_URL, user_agent=NASDAQ_USER_AGENT).json()
        )
        if ticker not in quotes:
            raise TickerScreenError(
                f"{ticker} is not listed in the NASDAQ screener either, so "
                "there is no price to measure against."
            )
        price, industry = quotes[ticker]
        as_of = "live"

    rates = fetch_rates(session)
    logger.info(
        f"{ticker}: ${price:,.2f} @ {as_of}; AAA {rates.aaa_corporate_pct}% "
        f"-> Graham Number coefficient {rates.graham_number_coeff:.2f}"
    )
    return audit(ticker, cache.load_dataframe(ticker), price, industry, rates), as_of


# ---- presentation --------------------------------------------------------

#: What each status means in the reader's language. The statuses are
#: the playbook's, not this file's.
_STATUS_HE = {
    "BUY": "עוברת את הסינון",
    "WATCH": "קרובה — שווה מעקב",
    "REJECT_ANTI_PATTERN": "נפסלת על אנטי-דפוס",
    "REJECT_NO_CATEGORY": "נפסלת — לא נכנסת לאף קטגוריה",
    "INSUFFICIENT_DATA": "אין מספיק נתונים",
}

_CATEGORY_HE = {
    "Defensive": "המשקיע ההגנתי",
    "Enterprising": "המשקיע היזם",
    "Net-Net": "נט-נט",
}


def _criterion(text: str, *, he: bool) -> str:
    """One failure or anti-pattern, in the reader's language.

    The strings come from the playbook audit and are templated, not
    free text, so the shared phrase table renders all of them. Where it
    cannot, the English is kept rather than a half-Hebrew sentence —
    the same rule the notifier applies.
    """
    if not he:
        return text
    hebrew = translate(text)
    if hebrew == text or is_hybrid(hebrew):
        return text
    return isolate_runs(hebrew)


def render(result: AuditResult, as_of: str, *, locale: str) -> str:
    """The verdict as a reader sees it."""
    he = locale == "he"
    out: list[str] = []
    status = _STATUS_HE.get(result.status, result.status) if he else result.status
    if he:
        out.append(f"{result.ticker} לפי בנג'מין גראהם — {status}")
        out.append(f"מחיר ${result.price:,.2f} ({as_of})")
    else:
        out.append(f"{result.ticker} through Benjamin Graham's screen — {status}")
        out.append(f"price ${result.price:,.2f} ({as_of})")
    if result.note:
        out.append(result.note)

    if result.qualifying:
        names = [
            (_CATEGORY_HE.get(c, c) if he else c) for c in result.qualifying
        ]
        out.append("")
        out.append(("עוברת כ: " if he else "qualifies as: ") + ", ".join(names))

    for label, fails in (
        ("Defensive", result.defensive_failures),
        ("Enterprising", result.enterprising_failures),
        ("Net-Net", result.net_net_failures),
    ):
        if not fails:
            continue
        head = _CATEGORY_HE.get(label, label) if he else label
        out.append("")
        out.append(f"נכשלת ב{head}:" if he else f"fails {label}:")
        out += [f"  - {_criterion(f, he=he)}" for f in fails]

    if result.anti_patterns:
        out.append("")
        shown = [_criterion(a, he=he) for a in result.anti_patterns]
        out.append(("אנטי-דפוסים: " if he else "anti-patterns: ") + ", ".join(shown))

    thesis = result.thesis_he if he else result.thesis_en
    if thesis:
        out.append("")
        out.append(thesis)

    triggers = result.exit_triggers_he if he else result.exit_triggers_en
    if triggers:
        out.append("")
        out.append("מתי היה מוכר:" if he else "when it would sell:")
        out += [f"  - {isolate_runs(t) if he else t}" for t in triggers]

    # Said every time, because an absent agent is not a silent one.
    names = [pair[1 if he else 0] for pair in RELATIVE_AGENTS.values()]
    out.append("")
    if he:
        listed = "، ".join(names[:-1]).replace("، ", ", ") + f" ו{names[-1]}"
        out.append(
            f"זה גראהם בלבד. {listed} שופטים לפי דירוג מול כל השוק, "
            "ולמניה בודדת אין להם תשובה."
        )
    else:
        listed = ", ".join(names[:-1]) + f" and {names[-1]}"
        out.append(
            f"Graham only. {listed} judge by rank against the whole market, "
            "and have no answer for one company."
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("ticker", help="the ticker to screen, e.g. XMTR")
    parser.add_argument(
        "--locale", choices=("he", "en"), default="he", help="language of the verdict"
    )
    parser.add_argument(
        "--json", action="store_true", help="print the full result as JSON instead"
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="also write the JSON to this path"
    )
    args = parser.parse_args(argv)

    session = requests.Session()
    session.headers["User-Agent"] = get_settings().sec_user_agent
    try:
        result, as_of = screen(args.ticker, session=session)
    except TickerScreenError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    payload = {**asdict(result), "price_as_of": as_of, "agent": "benjamin_graham"}
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        logger.info(f"wrote {args.out}")
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json
          else render(result, as_of, locale=args.locale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
