"""The regime dial: four public series, one number between 0 and 4.

What it is for
~~~~~~~~~~~~~~

The Council doctrine uses macro in exactly one way — as a risk dial that
sets sleeve weights — and forbids it everywhere else. It never picks a
stock. That restriction is the point: the same literature that finds
macro forecasting useless for selection finds these four series useful
for describing the state you are already in.

The four, and the rule each one answers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

============  ==========================  ==========================
series        what                        risk-off when
============  ==========================  ==========================
BAMLH0A0HYM2  high-yield credit spread    above its 1y median *and*
                                          higher than four weeks ago
T10Y3M        10-year minus 3-month       inverted (below zero)
SP500         index level                 below its 200-day average
VIXCLS        implied volatility          above 1.5x its 1y median
============  ==========================  ==========================

The credit rule needs both halves. A spread that is wide but tightening
is a recovery, and treating it as risk-off would have gone defensive
into every rally off a bottom.

Why not a FRED API key
~~~~~~~~~~~~~~~~~~~~~~

``fredgraph.csv`` serves the full history of any series with no
credentials and no registration. A key buys nothing here.

Point-in-time honesty
~~~~~~~~~~~~~~~~~~~~~

Every reading is computed only from observations dated on or before
``as_of``. FRED revises some series, so a historical reading taken today
is not identical to what a reader saw then — this is stated rather than
hidden, and it is why the dial is a sleeve weight and never an entry
signal.
"""

from __future__ import annotations

import csv
import io
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Final
from urllib.error import URLError
from urllib.request import Request, urlopen

from core.logger import get_logger

logger = get_logger("agents.council.regime")

FRED_CSV: Final[str] = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

#: FRED rejects a default urllib User-Agent often enough to be a real
#: failure mode; any identifying string is accepted.
USER_AGENT: Final[str] = "The-Value-Council regime dial"

REQUEST_TIMEOUT_SECONDS: Final[int] = 30

#: Lookback for the "1 year median" both the credit and volatility rules
#: compare against. Calendar days, because FRED series are daily but not
#: every day is present.
MEDIAN_WINDOW_DAYS: Final[int] = 365

#: The trend rule's moving average, in observations rather than calendar
#: days — 200 trading days is the convention the rule refers to.
TREND_WINDOW_OBSERVATIONS: Final[int] = 200

#: How far back the credit rule looks to decide "rising".
CREDIT_CHANGE_DAYS: Final[int] = 28

#: Volatility is risk-off above this multiple of its own 1y median.
VIX_MEDIAN_MULTIPLE: Final[float] = 1.5

#: Below this many observations in the window a rule cannot be evaluated
#: and reports UNKNOWN rather than guessing. Four series that quietly
#: default to risk-on would be a dial that only ever says "buy".
MIN_OBSERVATIONS: Final[int] = 30


class Stance(StrEnum):
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    #: The series could not be read, or held too few observations. Counted
    #: as neither, and surfaced.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Signal:
    """One series' reading, with the arithmetic that produced it."""

    series: str
    stance: Stance
    #: The latest observation used, and its date.
    value: float | None
    as_of: date | None
    #: What it was compared against, in the same units.
    threshold: float | None
    #: Plain-language statement of the comparison actually made.
    reason: str


@dataclass(frozen=True)
class Regime:
    """The four signals and the count they produce."""

    signals: tuple[Signal, ...]
    as_of: date

    @property
    def risk_on_count(self) -> int:
        """How many of the four read risk-on. UNKNOWN counts as neither."""
        return sum(1 for s in self.signals if s.stance is Stance.RISK_ON)

    @property
    def unknown_count(self) -> int:
        return sum(1 for s in self.signals if s.stance is Stance.UNKNOWN)

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "risk_on_count": self.risk_on_count,
            "unknown_count": self.unknown_count,
            "signals": [
                {
                    "series": s.series,
                    "stance": str(s.stance),
                    "value": s.value,
                    "as_of": s.as_of.isoformat() if s.as_of else None,
                    "threshold": s.threshold,
                    "reason": s.reason,
                }
                for s in self.signals
            ],
        }


Observation = tuple[date, float]


def fetch_series(series: str) -> list[Observation]:
    """Full history of ``series`` from FRED, oldest first.

    Missing observations are published as ``.`` and are dropped rather
    than interpolated — a gap is not a value.
    """
    url = FRED_CSV.format(series=series)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except (URLError, TimeoutError, OSError) as exc:
        logger.warning(f"{series}: fetch failed — {exc}")
        return []

    rows: list[Observation] = []
    reader = csv.reader(io.StringIO(body))
    header = next(reader, None)
    if header is None:
        return []
    for row in reader:
        if len(row) < 2 or row[1].strip() in {".", ""}:
            continue
        try:
            rows.append((date.fromisoformat(row[0].strip()), float(row[1])))
        except ValueError:
            continue
    return rows


def _until(rows: list[Observation], as_of: date) -> list[Observation]:
    """Observations dated on or before ``as_of``. The look-ahead guard."""
    return [(d, v) for d, v in rows if d <= as_of]


def _window(rows: list[Observation], as_of: date, days: int) -> list[float]:
    start = as_of - timedelta(days=days)
    return [v for d, v in rows if start <= d <= as_of]


def _unreadable(series: str, note: str) -> Signal:
    return Signal(
        series=series,
        stance=Stance.UNKNOWN,
        value=None,
        as_of=None,
        threshold=None,
        reason=note,
    )


def credit_signal(rows: list[Observation], as_of: date) -> Signal:
    """Risk-off when the spread is both wide *and* widening.

    Both halves are required. A spread above its median but tightening is
    what a recovery looks like, and calling that risk-off goes defensive
    into every rally off a bottom.
    """
    series = "BAMLH0A0HYM2"
    history = _until(rows, as_of)
    window = _window(rows, as_of, MEDIAN_WINDOW_DAYS)
    if len(window) < MIN_OBSERVATIONS or not history:
        return _unreadable(series, f"only {len(window)} observations in the window")

    latest_date, latest = history[-1]
    median = statistics.median(window)

    earlier = _until(rows, as_of - timedelta(days=CREDIT_CHANGE_DAYS))
    if not earlier:
        return _unreadable(series, "no observation four weeks back")
    four_weeks_ago = earlier[-1][1]

    wide = latest > median
    widening = latest > four_weeks_ago
    stance = Stance.RISK_OFF if (wide and widening) else Stance.RISK_ON
    return Signal(
        series=series,
        stance=stance,
        value=latest,
        as_of=latest_date,
        threshold=median,
        reason=(
            f"{latest:.2f} vs 1y median {median:.2f} "
            f"({'above' if wide else 'below'}), "
            f"vs {four_weeks_ago:.2f} four weeks ago "
            f"({'widening' if widening else 'tightening'})"
        ),
    )


def curve_signal(rows: list[Observation], as_of: date) -> Signal:
    """Risk-off when 10-year minus 3-month is negative."""
    series = "T10Y3M"
    history = _until(rows, as_of)
    if not history:
        return _unreadable(series, "no observation on or before as_of")
    latest_date, latest = history[-1]
    stance = Stance.RISK_OFF if latest < 0 else Stance.RISK_ON
    return Signal(
        series=series,
        stance=stance,
        value=latest,
        as_of=latest_date,
        threshold=0.0,
        reason=f"{latest:+.2f} — {'inverted' if latest < 0 else 'not inverted'}",
    )


def trend_signal(rows: list[Observation], as_of: date) -> Signal:
    """Risk-off when the index is below its 200-observation average."""
    series = "SP500"
    history = _until(rows, as_of)
    if len(history) < TREND_WINDOW_OBSERVATIONS:
        return _unreadable(series, f"only {len(history)} observations, need 200")
    latest_date, latest = history[-1]
    average = statistics.fmean(v for _, v in history[-TREND_WINDOW_OBSERVATIONS:])
    stance = Stance.RISK_OFF if latest < average else Stance.RISK_ON
    return Signal(
        series=series,
        stance=stance,
        value=latest,
        as_of=latest_date,
        threshold=average,
        reason=(
            f"{latest:,.2f} vs 200d average {average:,.2f} "
            f"({'below' if latest < average else 'above'})"
        ),
    )


def volatility_signal(rows: list[Observation], as_of: date) -> Signal:
    """Risk-off above 1.5x the 1-year median."""
    series = "VIXCLS"
    history = _until(rows, as_of)
    window = _window(rows, as_of, MEDIAN_WINDOW_DAYS)
    if len(window) < MIN_OBSERVATIONS or not history:
        return _unreadable(series, f"only {len(window)} observations in the window")
    latest_date, latest = history[-1]
    threshold = statistics.median(window) * VIX_MEDIAN_MULTIPLE
    stance = Stance.RISK_OFF if latest > threshold else Stance.RISK_ON
    return Signal(
        series=series,
        stance=stance,
        value=latest,
        as_of=latest_date,
        threshold=threshold,
        reason=(
            f"{latest:.2f} vs 1.5x 1y median {threshold:.2f} "
            f"({'above' if latest > threshold else 'below'})"
        ),
    )


#: Series id -> the rule that reads it. Ordered as the doctrine lists them.
RULES: Final[tuple[tuple[str, object], ...]] = (
    ("BAMLH0A0HYM2", credit_signal),
    ("T10Y3M", curve_signal),
    ("SP500", trend_signal),
    ("VIXCLS", volatility_signal),
)


def read_regime(
    as_of: date | None = None,
    *,
    fetch=fetch_series,
) -> Regime:
    """Read all four series and return the dial.

    ``fetch`` is injectable so the tests never touch the network; the
    default hits FRED.
    """
    when = as_of or date.today()
    signals: list[Signal] = []
    for series, rule in RULES:
        rows = fetch(series)
        if not rows:
            signals.append(_unreadable(series, "series could not be fetched"))
            continue
        signals.append(rule(rows, when))  # type: ignore[operator]

    regime = Regime(signals=tuple(signals), as_of=when)
    logger.info(
        f"{when}: regime risk-on {regime.risk_on_count}/4"
        + (f", {regime.unknown_count} unreadable" if regime.unknown_count else "")
    )
    return regime


__all__ = [
    "MEDIAN_WINDOW_DAYS",
    "TREND_WINDOW_OBSERVATIONS",
    "VIX_MEDIAN_MULTIPLE",
    "Observation",
    "Regime",
    "Signal",
    "Stance",
    "credit_signal",
    "curve_signal",
    "fetch_series",
    "read_regime",
    "trend_signal",
    "volatility_signal",
]
