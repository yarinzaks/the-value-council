"""Run the playbook-as-written Graham screen over the US market.

Usage::

    .venv/bin/python -m scripts.run_graham_audit
    .venv/bin/python -m scripts.run_graham_audit --no-live   # cache only

Read-only: it trades nothing and touches no portfolio. See
:mod:`agents.graham.playbook_audit` for what is applied and why it is
kept apart from the agent's own filter.

How it runs
~~~~~~~~~~~

1. One request for every US quote (Nasdaq's screener download) and two
   for the yields that move the valuation constants (FRED: Moody's AAA
   and the 20-year Treasury).
2. Every ticker in the local EDGAR cache is screened against those
   quotes. The cache can be months old, so this pass only nominates.
3. Every nominee — a BUY, a WATCH, or one criterion from a category — is
   screened again on its live SEC ``companyfacts``. The live pass is the
   answer; ``--no-live`` skips it and says so in the report.

What it writes
~~~~~~~~~~~~~~

``<data root>/graham_audit/<date>.json`` — inputs, counts, every
nominee's full result, and each live fetch that failed and why.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import requests

from agents.graham.playbook_audit import AuditResult, MarketRates, audit, is_near_miss
from core.config import get_settings
from core.data.edgar_cache import EdgarCache
from core.logger import get_logger
from core.paths import PROJECT_ROOT, data_root

logger = get_logger("scripts.run_graham_audit")

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
TICKER_MAP_PATH = PROJECT_ROOT / "data_bundled" / "company_tickers.json"
# SEC allows 10 requests a second; stay well under it.
SEC_REQUEST_INTERVAL_S = 0.15
HTTP_TIMEOUT_S = 60
# Nasdaq drops connections from non-browser agents (the SEC-style
# "name email" agent gets no response at all), while SEC requires one
# that identifies the caller — so the agent is chosen per host.
NASDAQ_USER_AGENT = "Mozilla/5.0"

Quote = tuple[float, str]  # (last price, "sector/industry")


def parse_nasdaq_quotes(payload: object) -> dict[str, Quote]:
    """Map ticker → (price, industry) from Nasdaq's screener JSON.

    Raises ``ValueError`` when the payload is not the expected shape —
    an empty quote map would silently screen nothing.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unexpected Nasdaq payload shape: no data.rows list")
    quotes: dict[str, Quote] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol", "")).strip().upper()
        raw_price = str(row.get("lastsale", "")).replace("$", "").replace(",", "")
        try:
            price = float(raw_price)
        except ValueError:
            continue
        if symbol and price > 0:
            industry = f"{row.get('sector') or ''}/{row.get('industry') or ''}"
            quotes[symbol] = (price, industry)
    if not quotes:
        raise ValueError("Nasdaq payload contained no usable quotes")
    return quotes


def parse_fred_latest(text: str, series_id: str) -> float:
    """Last non-missing observation of a FRED CSV download."""
    latest: float | None = None
    for row in csv.reader(io.StringIO(text)):
        if len(row) != 2 or row[1] in ("", "."):
            continue
        try:
            latest = float(row[1])
        except ValueError:
            continue  # the header row
    if latest is None:
        raise ValueError(f"FRED series {series_id} has no observations")
    return latest


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def companyfacts_to_frame(payload: object) -> pd.DataFrame:
    """Flatten SEC ``companyfacts`` JSON into the EDGAR cache's column layout.

    Raises ``ValueError`` when the payload has no ``facts`` object.
    """
    facts_by_ns = _dict(payload).get("facts")
    if not isinstance(facts_by_ns, dict):
        raise ValueError("companyfacts payload has no 'facts' object")
    rows: list[dict[str, object]] = []
    for namespace, concepts in facts_by_ns.items():
        for concept, body in _dict(concepts).items():
            for unit, facts in _dict(_dict(body).get("units")).items():
                if not isinstance(facts, list):
                    continue
                for f in (x for x in facts if isinstance(x, dict)):
                    rows.append(
                        {
                            "concept": concept,
                            "namespace": namespace,
                            "unit": unit,
                            "value": f.get("val"),
                            "period_start": f.get("start"),
                            "period_end": f.get("end"),
                            "filed": f.get("filed"),
                            "form": f.get("form"),
                        }
                    )
    frame = pd.DataFrame(
        rows,
        columns=[
            "concept",
            "namespace",
            "unit",
            "value",
            "period_start",
            "period_end",
            "filed",
            "form",
        ],
    )
    for col in ("period_start", "period_end", "filed"):
        frame[col] = pd.to_datetime(frame[col], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    return frame


def _get(
    session: requests.Session, url: str, *, user_agent: str | None = None, **params: str
) -> requests.Response:
    headers = {"User-Agent": user_agent} if user_agent else None
    resp = session.get(url, params=params or None, headers=headers, timeout=HTTP_TIMEOUT_S)
    resp.raise_for_status()
    return resp


def fetch_rates(session: requests.Session) -> MarketRates:
    aaa = parse_fred_latest(_get(session, FRED_CSV_URL, id="AAA").text, "AAA")
    t20 = parse_fred_latest(_get(session, FRED_CSV_URL, id="DGS20").text, "DGS20")
    return MarketRates(aaa_corporate_pct=aaa, treasury_20y_pct=t20)


def fetch_and_audit(
    session: requests.Session, nominee: AuditResult, cik: int, rates: MarketRates
) -> AuditResult | str:
    """Re-run one nominee on live SEC facts; a failed fetch or bad payload returns why."""
    try:
        payload = _get(session, SEC_COMPANYFACTS_URL.format(cik=cik)).json()
        facts = companyfacts_to_frame(payload)
    except (requests.RequestException, ValueError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return audit(nominee.ticker, facts, nominee.price, nominee.industry, rates)


def load_cik_map(path: Path = TICKER_MAP_PATH) -> dict[str, int]:
    raw = json.loads(path.read_text())
    return {str(v["ticker"]).upper(): int(v["cik_str"]) for v in raw.values()}


def _summarise(results: list[AuditResult]) -> dict[str, int]:
    return dict(Counter(r.status for r in results).most_common())


def run(*, live: bool, out_dir: Path) -> Path:
    settings = get_settings()
    session = requests.Session()
    session.headers["User-Agent"] = settings.sec_user_agent

    quotes = parse_nasdaq_quotes(
        _get(session, NASDAQ_SCREENER_URL, user_agent=NASDAQ_USER_AGENT).json()
    )
    rates = fetch_rates(session)
    logger.info(
        f"{len(quotes)} quotes; AAA {rates.aaa_corporate_pct}% → GN coeff "
        f"{rates.graham_number_coeff:.2f}; 20y UST {rates.treasury_20y_pct}% → "
        f"EPV rate {rates.epv_rate:.4f}"
    )

    cache = EdgarCache()
    cached = [t for t in cache.tickers() if t in quotes]
    stage1: list[AuditResult] = []
    for i, ticker in enumerate(cached, 1):
        price, industry = quotes[ticker]
        stage1.append(audit(ticker, cache.load_dataframe(ticker), price, industry, rates))
        if i % 1000 == 0:
            # Each ticker is read once; the LRU would only grow.
            cache.clear_memory_cache()
            logger.info(f"cache pass {i}/{len(cached)}")
    nominees = [r for r in stage1 if is_near_miss(r)]
    logger.info(f"cache pass: {_summarise(stage1)}; {len(nominees)} nominees")

    final: list[AuditResult] = nominees
    fetch_errors: dict[str, str] = {}
    if live:
        cik_map = load_cik_map()
        final = []
        for r in nominees:
            cik = cik_map.get(r.ticker)
            if cik is None:
                fetch_errors[r.ticker] = "no CIK in company_tickers.json"
                continue
            final_result = fetch_and_audit(session, r, cik, rates)
            if isinstance(final_result, str):
                fetch_errors[r.ticker] = final_result
                logger.warning(f"{r.ticker}: live SEC fetch failed: {final_result}")
            else:
                final.append(final_result)
            time.sleep(SEC_REQUEST_INTERVAL_S)

    order = {"BUY": 0, "WATCH": 1}
    final.sort(
        key=lambda r: (
            order.get(r.status, 2),
            -(r.valuation.margin_of_safety if r.valuation else -1e9),
        )
    )
    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "live SEC companyfacts" if live else "local EDGAR cache only (may be stale)",
        "rates": asdict(rates)
        | {"graham_number_coeff": rates.graham_number_coeff, "epv_rate": rates.epv_rate},
        "universe": {"quotes": len(quotes), "screened": len(stage1)},
        "cache_pass": _summarise(stage1),
        "final": _summarise(final),
        "live_fetch_errors": fetch_errors,
        "results": [asdict(r) for r in final],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{date.today().isoformat()}.json"
    path.write_text(json.dumps(report, indent=1, default=str))
    _print(final, str(report["source"]), len(stage1), len(fetch_errors))
    return path


def _print(final: list[AuditResult], source: str, screened: int, n_errors: int) -> None:
    print(f"\nGraham playbook audit — {source}")
    print(f"screened {screened}  final: {_summarise(final)}")
    if n_errors:
        print(f"live fetch errors: {n_errors} (see report)")
    shown = [r for r in final if r.valuation is not None and not r.anti_patterns]
    if not shown:
        print("No company qualifies for any category. Hold cash (playbook 6.2).")
        return
    print(
        f"\n{'ticker':<7}{'status':<7}{'category':<14}{'price':>10}{'value':>10}{'MoS':>8}{'max entry':>11}"
    )
    for r in shown:
        v = r.valuation
        assert v is not None
        print(
            f"{r.ticker:<7}{r.status:<7}{v.category:<14}{r.price:>10.2f}"
            f"{v.intrinsic_value:>10.2f}{v.margin_of_safety:>8.1%}{v.max_entry_price:>11.2f}"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument(
        "--no-live", action="store_true", help="skip the live SEC re-check of nominees"
    )
    args = parser.parse_args(argv)
    path = run(live=not args.no_live, out_dir=data_root() / "graham_audit")
    print(f"\nreport: {path}")


if __name__ == "__main__":
    main()
