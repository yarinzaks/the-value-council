"""Cache validator — cross-check EDGAR cache vs an alternative source.

Pulls a sample of tickers from the EDGAR cache and compares key
fundamentals against the same fields from FMP. Differences are
expected and acceptable up to a documented tolerance (5% by default)
because:

* SEC XBRL uses the company's own concept tagging; FMP applies
  vendor-side normalization. They can disagree on which line is
  "revenue" for a holding company, etc.
* SEC reports as-filed values; FMP may incorporate restatements.

Outputs a structured :class:`ValidationReport` with per-ticker,
per-field deltas and a summary.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from core.logger import get_logger

from .fundamentals_fetcher import FundamentalsFetcher

logger = get_logger("core.data.cache_validator")


DEFAULT_TOLERANCE = 0.05  # 5%
DEFAULT_FIELDS_TO_VALIDATE: tuple[str, ...] = (
    "revenue",
    "net_income",
    "operating_income",
    "total_assets",
    "current_assets",
    "current_liabilities",
)


@dataclass(frozen=True)
class FieldComparison:
    """One field's value from EDGAR and from the comparison source."""

    ticker: str
    field: str
    edgar_value: float | None
    other_value: float | None
    relative_diff: float | None  # |edgar - other| / |other|, or None if either missing
    within_tolerance: bool
    notes: str = ""


@dataclass(frozen=True)
class TickerValidation:
    """All field comparisons for one ticker."""

    ticker: str
    as_of: date
    comparisons: list[FieldComparison] = field(default_factory=list)

    @property
    def fields_compared(self) -> int:
        return sum(1 for c in self.comparisons if c.relative_diff is not None)

    @property
    def fields_within_tolerance(self) -> int:
        return sum(
            1 for c in self.comparisons if c.relative_diff is not None and c.within_tolerance
        )

    @property
    def pass_rate(self) -> float:
        n = self.fields_compared
        return self.fields_within_tolerance / n if n else 0.0


@dataclass
class ValidationReport:
    """Aggregate report across all sampled tickers."""

    sample_size: int
    tolerance: float
    fields_validated: tuple[str, ...]
    per_ticker: list[TickerValidation] = field(default_factory=list)

    def overall_pass_rate(self) -> float:
        n_total = sum(t.fields_compared for t in self.per_ticker)
        n_pass = sum(t.fields_within_tolerance for t in self.per_ticker)
        return n_pass / n_total if n_total else 0.0

    def systematic_discrepancies(self, threshold: float = 0.30) -> list[str]:
        """Return field names with pass rate below ``threshold``."""
        per_field: dict[str, list[FieldComparison]] = {}
        for t in self.per_ticker:
            for c in t.comparisons:
                per_field.setdefault(c.field, []).append(c)
        bad: list[str] = []
        for f, comps in per_field.items():
            valid = [c for c in comps if c.relative_diff is not None]
            if not valid:
                continue
            pass_rate = sum(1 for c in valid if c.within_tolerance) / len(valid)
            if pass_rate < threshold:
                bad.append(f"{f} (pass_rate={pass_rate:.1%})")
        return bad

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_size": self.sample_size,
            "tolerance": self.tolerance,
            "fields_validated": list(self.fields_validated),
            "overall_pass_rate": self.overall_pass_rate(),
            "systematic_discrepancies": self.systematic_discrepancies(),
            "per_ticker": [
                {
                    "ticker": t.ticker,
                    "as_of": t.as_of.isoformat(),
                    "fields_compared": t.fields_compared,
                    "fields_within_tolerance": t.fields_within_tolerance,
                    "comparisons": [asdict(c) for c in t.comparisons],
                }
                for t in self.per_ticker
            ],
        }


class CacheValidator:
    """Compare EDGAR-cached fundamentals to FMP-fetched values."""

    def __init__(
        self,
        edgar: FundamentalsFetcher,
        fmp_adapter,  # core.backtest.fmp_adapter.FMPAdapter
        *,
        tolerance: float = DEFAULT_TOLERANCE,
        fields: Iterable[str] = DEFAULT_FIELDS_TO_VALIDATE,
    ) -> None:
        if tolerance < 0:
            raise ValueError(f"tolerance must be non-negative; got {tolerance}")
        self.edgar = edgar
        self.fmp = fmp_adapter
        self.tolerance = tolerance
        self.fields = tuple(fields)

    # ------------------------------------------------------------------
    def validate_ticker(
        self,
        ticker: str,
        as_of: date | datetime,
    ) -> TickerValidation:
        as_of_d = (
            as_of.date() if isinstance(as_of, datetime) else as_of
        )
        edgar_values, _ = self.edgar.get_all_fields(ticker, as_of_d)

        # Pull FMP fundamentals for the same as_of via a synthetic
        # FilingMetadata. We grab the latest 10-K/10-Q that the FMP
        # adapter knows about; if the periods don't align this can
        # produce expected discrepancies — that's acceptable, we
        # report them.
        try:
            filings = self.fmp.list_filings(
                ticker, form_types=("10-K", "10-Q")
            )
        except Exception as exc:
            logger.warning(f"FMP list_filings({ticker}) failed: {exc}")
            filings = []
        eligible = [f for f in filings if f.filing_date <= as_of_d]
        if not eligible:
            logger.info(f"FMP has no filing for {ticker} on or before {as_of_d}")
            return TickerValidation(ticker=ticker, as_of=as_of_d)
        fmp_filing = max(eligible, key=lambda f: f.filing_date)
        try:
            fmp_values = self.fmp.parse_financials(fmp_filing)
        except Exception as exc:
            logger.warning(f"FMP parse_financials({ticker}) failed: {exc}")
            fmp_values = {}

        comps: list[FieldComparison] = []
        for f in self.fields:
            ev = edgar_values.get(f)
            ov = fmp_values.get(f)
            comps.append(_compare(ticker, f, ev, ov, self.tolerance))
        return TickerValidation(
            ticker=ticker, as_of=as_of_d, comparisons=comps
        )

    def validate_sample(
        self,
        tickers: list[str],
        sample_size: int,
        as_of: date | datetime,
        *,
        seed: int | None = 42,
    ) -> ValidationReport:
        """Sample ``sample_size`` tickers and validate each."""
        if seed is not None:
            random.seed(seed)
        sample = random.sample(tickers, min(sample_size, len(tickers)))
        report = ValidationReport(
            sample_size=len(sample),
            tolerance=self.tolerance,
            fields_validated=self.fields,
        )
        for t in sample:
            report.per_ticker.append(self.validate_ticker(t, as_of))
        return report


def _compare(
    ticker: str,
    field_name: str,
    a: float | None,
    b: float | None,
    tolerance: float,
) -> FieldComparison:
    if a is None or b is None:
        notes = []
        if a is None:
            notes.append("EDGAR missing")
        if b is None:
            notes.append("comparison missing")
        return FieldComparison(
            ticker=ticker,
            field=field_name,
            edgar_value=a,
            other_value=b,
            relative_diff=None,
            within_tolerance=False,
            notes=", ".join(notes),
        )
    if b == 0:
        # Avoid div-by-zero; if both are zero, perfect match.
        rel = 0.0 if a == 0 else float("inf")
    else:
        rel = abs(a - b) / abs(b)
    return FieldComparison(
        ticker=ticker,
        field=field_name,
        edgar_value=a,
        other_value=b,
        relative_diff=rel,
        within_tolerance=rel <= tolerance,
    )


__all__ = [
    "DEFAULT_FIELDS_TO_VALIDATE",
    "DEFAULT_TOLERANCE",
    "CacheValidator",
    "FieldComparison",
    "TickerValidation",
    "ValidationReport",
]
