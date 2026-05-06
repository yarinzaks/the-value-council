"""SEC EDGAR Company Facts API client.

The Company Facts endpoint returns the **entire reported XBRL history**
of a company in a single JSON document. Each fact carries:

* ``val`` — the numeric value (or string)
* ``filed`` — the filing date that disclosed it (our PIT anchor)
* ``form`` — ``10-K``, ``10-Q``, ``8-K``, etc.
* ``fy`` / ``fp`` — fiscal year and period
* ``accn`` — accession number

This is a far more reliable PIT primary source than parsing individual
10-K filings via edgartools (which has API drift across versions).

Endpoint: ``https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json``

SEC requires a User-Agent identifying the requester (name + email);
we read it from settings.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config import get_settings
from core.exceptions import DataSourceError, RateLimitError, ValueCouncilError
from core.logger import get_logger

logger = get_logger("core.data.edgar_facts")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# SEC's stated rate limit is 10 req/s. We self-throttle to 8 req/s
# (125ms between calls) to stay well under and absorb burst noise.
_MIN_INTERVAL_SECONDS = 0.125


class EdgarFactsError(ValueCouncilError):
    """Raised when the Company Facts API cannot satisfy a request."""


@dataclass(frozen=True)
class XbrlFact:
    """One reported XBRL fact for a single (concept, period, filing).

    Pure data; persisted to parquet via :mod:`core.data.edgar_cache`.
    """

    concept: str  # e.g. "Revenues" or "OperatingIncomeLoss"
    namespace: str  # "us-gaap" or "dei"
    unit: str  # "USD", "USD/shares", "shares", etc.
    value: float
    period_start: date | None
    period_end: date
    filed: date  # the filing date — our PIT anchor
    form: str  # "10-K", "10-Q", "8-K", ...
    fiscal_year: int | None
    fiscal_period: str | None  # "FY", "Q1", "Q2", "Q3"
    accession_number: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "namespace": self.namespace,
            "unit": self.unit,
            "value": self.value,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat(),
            "filed": self.filed.isoformat(),
            "form": self.form,
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "accession_number": self.accession_number,
        }


def _log_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        f"EDGAR retry {retry_state.attempt_number}/3 after {type(exc).__name__}: {exc}"
    )


_retry_edgar = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((requests.RequestException, RateLimitError)),
    before_sleep=_log_retry,
    reraise=True,
)


class EdgarFactsClient:
    """Direct SEC Company Facts API client.

    Self-throttled to 8 req/s (well below SEC's 10 req/s limit).
    Thread-safe — multiple threads share one connection pool and one
    rate-limit lock.
    """

    _last_call_at: float = 0.0
    _lock: threading.Lock = threading.Lock()

    def __init__(self, user_agent: str | None = None) -> None:
        if user_agent is None:
            try:
                user_agent = get_settings().sec_user_agent
            except Exception as exc:  # noqa: BLE001
                raise EdgarFactsError(
                    f"SEC User-Agent unavailable: {exc}"
                ) from exc
        if not user_agent:
            raise EdgarFactsError("SEC requires a User-Agent (name + email)")
        self._user_agent = user_agent
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._session.headers["Accept"] = "application/json"
        self._cik_map: dict[str, int] | None = None

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------
    def _throttle(self) -> None:
        with EdgarFactsClient._lock:
            elapsed = time.monotonic() - EdgarFactsClient._last_call_at
            if elapsed < _MIN_INTERVAL_SECONDS:
                time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
            EdgarFactsClient._last_call_at = time.monotonic()

    @_retry_edgar
    def _get_json(self, url: str) -> dict[str, Any]:
        self._throttle()
        response = self._session.get(url, timeout=20)
        if response.status_code == 429:
            raise RateLimitError("edgar", f"rate limited: {url}")
        if response.status_code == 404:
            raise EdgarFactsError(f"not found: {url}")
        if not response.ok:
            raise DataSourceError("edgar", f"HTTP {response.status_code}: {url}")
        try:
            return response.json()
        except ValueError as exc:
            raise DataSourceError(
                "edgar", f"non-JSON body for {url}: {response.text[:200]}"
            ) from exc

    # ------------------------------------------------------------------
    # CIK resolution
    # ------------------------------------------------------------------
    def cik_for(self, ticker: str) -> int | None:
        """Return the integer CIK for ``ticker``, or None if unknown."""
        self._ensure_cik_map()
        return (self._cik_map or {}).get(ticker.upper())

    def _ensure_cik_map(self) -> None:
        if self._cik_map is not None:
            return
        # Try a bundled local file FIRST. SEC's www.sec.gov endpoint
        # 403's many cloud IP ranges (notably GitHub Actions runners)
        # — the same blocking that broke our cold-cache prefetch
        # attempts. data.sec.gov is permissive for the per-ticker XBRL
        # endpoint, but the ticker-mapping file lives only on
        # www.sec.gov. So we ship a recent copy in the repo and fall
        # back to the network only if it's missing or unparseable.
        import json as _json

        data: dict | None = None
        for local_path in self._candidate_tickers_paths():
            if local_path.exists():
                try:
                    data = _json.loads(local_path.read_text())
                    logger.info(f"loaded company_tickers from local {local_path}")
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        f"local company_tickers file unreadable ({local_path}): {exc}"
                    )
                    data = None
        if data is None:
            logger.info("loading SEC company-tickers map from www.sec.gov")
            data = self._get_json(COMPANY_TICKERS_URL)
        # data is a dict with integer-string keys; values are
        # {"cik_str": int, "ticker": str, "title": str}
        cik_map: dict[str, int] = {}
        for entry in data.values():
            try:
                ticker = str(entry["ticker"]).upper()
                cik = int(entry["cik_str"])
                # First entry wins on ticker collision (rare; usually
                # different share classes)
                cik_map.setdefault(ticker, cik)
            except (KeyError, ValueError, TypeError):
                continue
        self._cik_map = cik_map
        logger.info(f"loaded {len(cik_map)} ticker→CIK mappings")

    @staticmethod
    def _candidate_tickers_paths() -> list:
        """Where to look for a bundled ``company_tickers.json`` copy.

        Order: project-bundled first (always present in a fresh
        checkout), then the live data root (where a successful network
        fetch would persist a copy for next time).
        """
        from pathlib import Path

        from core.paths import DATA_ROOT

        project_root = Path(__file__).resolve().parent.parent.parent
        return [
            project_root / "data_bundled" / "company_tickers.json",
            DATA_ROOT / "cache" / "company_tickers.json",
        ]

    # ------------------------------------------------------------------
    # Company Facts
    # ------------------------------------------------------------------
    def get_company_facts(self, ticker: str) -> list[XbrlFact]:
        """Fetch all XBRL facts ever reported by ``ticker``.

        Returns a list of :class:`XbrlFact`. Empty list if the ticker
        has no SEC filings or the CIK is unknown.

        Raises:
            EdgarFactsError: when the SEC endpoint returns non-200 or
                the response shape is unrecognized.
        """
        ticker_u = ticker.upper()
        cik = self.cik_for(ticker_u)
        if cik is None:
            logger.info(f"no CIK for {ticker_u}; skipping")
            return []

        url = COMPANY_FACTS_URL.format(cik=cik)
        try:
            payload = self._get_json(url)
        except EdgarFactsError as exc:
            logger.warning(f"company_facts failed for {ticker_u} (CIK {cik}): {exc}")
            return []

        return self._parse_facts(payload)

    def _parse_facts(self, payload: dict[str, Any]) -> list[XbrlFact]:
        """Flatten the SEC Company Facts JSON into :class:`XbrlFact` rows."""
        facts: list[XbrlFact] = []
        namespaces = payload.get("facts", {})
        for ns, concepts in namespaces.items():
            if not isinstance(concepts, dict):
                continue
            for concept_name, concept_obj in concepts.items():
                if not isinstance(concept_obj, dict):
                    continue
                units = concept_obj.get("units", {})
                if not isinstance(units, dict):
                    continue
                for unit_name, datapoints in units.items():
                    if not isinstance(datapoints, list):
                        continue
                    for dp in datapoints:
                        try:
                            facts.append(
                                XbrlFact(
                                    concept=concept_name,
                                    namespace=ns,
                                    unit=unit_name,
                                    value=float(dp["val"]),
                                    period_start=_parse_date(dp.get("start")),
                                    period_end=_parse_date_required(dp["end"]),
                                    filed=_parse_date_required(dp["filed"]),
                                    form=str(dp.get("form", "")),
                                    fiscal_year=int(dp["fy"]) if dp.get("fy") is not None else None,
                                    fiscal_period=str(dp["fp"]) if dp.get("fp") else None,
                                    accession_number=str(dp.get("accn", "")),
                                )
                            )
                        except (KeyError, ValueError, TypeError) as exc:
                            logger.debug(f"skipping malformed fact: {exc}")
        return facts


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    return _parse_date_required(value)


def _parse_date_required(value: Any) -> date:
    if isinstance(value, date):
        return value
    s = str(value).strip()
    return datetime.strptime(s, "%Y-%m-%d").date()


__all__ = [
    "COMPANY_FACTS_URL",
    "COMPANY_TICKERS_URL",
    "EdgarFactsClient",
    "EdgarFactsError",
    "XbrlFact",
]
