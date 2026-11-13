"""When Fisher sells a name he holds — and when he keeps it.

The problem this fixes (audit SF-X1, item 13f)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Playbook §7: "The default is **never sell**." Fisher names exactly
three legitimate sell triggers — a mistake in the original purchase,
a company that "would no longer pass 12+ of 15 points if newly
evaluated today", and a clearly superior opportunity — and §7.2 lists
what is not one: appreciation, a "too high" market, a forecast
recession, a disappointing quarter, a downgrade, sector rotation.
None of it ran. ``PhilipFisher.select`` ignored ``held``, and the
engine sold a Fisher name only because it left the day's target list:
17 sales since 2026-08-05, median holding 64.5 days, CRM sold twice and
bought back twice.

What this module decides
~~~~~~~~~~~~~~~~~~~~~~~~

One :class:`~core.live.doctrine_exits.ExitVerdict` per held name.
Fisher's exits run in shadow
(:data:`core.live.doctrine_exits.DOCTRINE_EXIT_MODES`): the runner
journals each verdict beside what the engine did; no weight changes.
A held name is re-assessed the way the entry screen assessed it — the
same quality gates (:mod:`agents.fisher.filters`) and the same five
quant-checkable points (:func:`agents.fisher.quality_score.score_quality`)
— so it is judged by the doctrine that bought it.

**Trigger 2 — the company no longer matches the original assessment.**
Newly evaluated today, it would not be bought:

* the balance-sheet gates fail — book equity no longer positive, or
  D/E above the screen's ceiling (``max_de``, 0.6). Unreported debt is
  unknown, not a breach (audit FUND-4);
* or fewer than :data:`ENTRY_MIN_POINTS` (4, the Tier B floor — the
  quant stand-in for the playbook's 12 of 15) of the five points can
  still pass. A point fails only on a measured value below its floor;
  a point the filings cannot measure is unknown. The sell needs enough
  measured failures that even every unknown passing would not reach 4.

**Retention.** At least four points measured passing and the gates
holding: keep, on or off today's list. Tier A slipping to Tier B is a
keep — it would still be bought.

What is deliberately *not* a sell, because §7.2 / §10 say so: a price
of any level (the screen's P/E ceiling and market-cap floor price an
entry, they do not grade a business — "pure gains are not a sell
trigger"), and a loss year (the screen's positive-net-income gate is an
entry proxy; "quarterly earnings disappointed" is not a trigger).

Not decided here:

* **Trigger 1 — a mistake in the original purchase.** The playbook
  defines it qualitatively ("the company's qualitative position was
  misjudged from the start"); nothing in the filings says the original
  scuttlebutt was wrong.
* **Trigger 3 — a clearly superior opportunity.** It weighs a held name
  against a candidate; a verdict is about one held name.
* **The ten qualitative points and Point 15 (integrity).** The LLM
  memo's territory, not the filings'.

When a verdict cannot be reached honestly — no fundamentals, or too
many unmeasurable points to tell whether four would pass — and no gate
failed on what *is* known, no verdict is given for that name. A keep on
no evidence would, once enforced, hold the name indefinitely; silence
leaves it to the engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import FundamentalsLookup, HeldPosition
from core.data.edgar_cache import EdgarCache
from core.live.doctrine_exits import ExitVerdict
from core.logger import get_logger

from .filters import DEFAULT_MAX_DE, debt_to_equity
from .quality_score import (
    DEFAULT_MARGIN_TREND_FLOOR_BPS,
    DEFAULT_MAX_SHARE_DILUTION_PCT_5YR,
    DEFAULT_MIN_OPERATING_MARGIN_PCT,
    DEFAULT_MIN_RD_TO_REVENUE_PCT,
    DEFAULT_MIN_REVENUE_CAGR_PCT,
    QualityScore,
    score_quality,
)

logger = get_logger("agents.fisher.exits")

#: The fewest of the five quant points a name needs to be bought — Tier
#: B in :func:`agents.fisher.ranking.score_candidates`. The playbook's
#: trigger 2 threshold is "would no longer pass 12+ of 15 if newly
#: evaluated today"; on the five points the screen can measure, the
#: entry bar is 4.
ENTRY_MIN_POINTS: int = 4

#: How many quant points there are.
QUANT_POINTS: int = 5

_PRECISION = 6


@dataclass(frozen=True)
class PointReading:
    """One quant point as re-measured today.

    ``passed`` is None when the filings cannot measure it — unknown,
    which is neither a pass nor a failure.
    """

    label: str
    passed: bool | None
    detail: str


def readings(qs: QualityScore) -> list[PointReading]:
    """The five points of ``qs``, each pass / fail / unknown.

    Thresholds are the screen's defaults — the ones
    :func:`agents.fisher.ranking.score_candidates` buys on.
    """

    def reading(
        label: str, value: float | None, ok: bool, measured: str, floor: str
    ) -> PointReading:
        if value is None:
            return PointReading(label, None, f"{label}: not measurable")
        return PointReading(label, ok, f"{label}: {measured} ({floor})")

    cagr = qs.revenue_cagr_5yr_pct
    rd = qs.rd_to_revenue_pct
    opm = qs.operating_margin_pct
    trend = qs.margin_trend_5yr_bps
    dil = qs.share_count_change_5yr_pct
    return [
        reading(
            "point 1 market potential",
            cagr,
            cagr is not None and cagr >= DEFAULT_MIN_REVENUE_CAGR_PCT,
            f"5-yr revenue CAGR {cagr or 0.0:.1f}%",
            f"floor {DEFAULT_MIN_REVENUE_CAGR_PCT:g}%",
        ),
        reading(
            "point 3 R&D effectiveness",
            rd,
            rd is not None and rd >= DEFAULT_MIN_RD_TO_REVENUE_PCT,
            f"R&D {rd or 0.0:.1f}% of revenue",
            f"floor {DEFAULT_MIN_RD_TO_REVENUE_PCT:g}%",
        ),
        reading(
            "point 5 profit margins",
            opm,
            opm is not None and opm >= DEFAULT_MIN_OPERATING_MARGIN_PCT,
            f"operating margin {opm or 0.0:.1f}%",
            f"floor {DEFAULT_MIN_OPERATING_MARGIN_PCT:g}%",
        ),
        reading(
            "point 6 margin maintenance",
            trend,
            trend is not None and trend >= DEFAULT_MARGIN_TREND_FLOOR_BPS,
            f"5-yr margin trend {trend or 0.0:+.0f} bps",
            f"floor {DEFAULT_MARGIN_TREND_FLOOR_BPS:+g} bps",
        ),
        reading(
            "point 13 equity dilution",
            dil,
            dil is not None and dil <= DEFAULT_MAX_SHARE_DILUTION_PCT_5YR,
            f"5-yr share count {dil or 0.0:+.1f}%",
            f"ceiling {DEFAULT_MAX_SHARE_DILUTION_PCT_5YR:+g}%",
        ),
    ]


def gate_break(fin: PointInTimeFinancials, *, max_de: float = DEFAULT_MAX_DE) -> str | None:
    """Why the entry screen's balance-sheet gates now fail, or None."""
    if fin.total_equity is not None and fin.total_equity <= 0:
        return (
            f"no longer matches the original assessment: book equity "
            f"${fin.total_equity:,.0f} is no longer positive"
        )
    de = debt_to_equity(fin)
    # A millionth: float division must not turn the ceiling into a breach.
    if de is not None and round(de, _PRECISION) > max_de:
        return (
            f"no longer matches the original assessment: D/E {de:.2f} above "
            f"the {max_de:g} the entry screen allows"
        )
    return None


def decide(
    position: HeldPosition,
    fin: PointInTimeFinancials | None,
    *,
    cache: EdgarCache,
    as_of: date,
    max_de: float = DEFAULT_MAX_DE,
) -> ExitVerdict | None:
    """Keep or sell one held name, or None when it cannot be judged.

    No price is read: nothing in Fisher's exit doctrine depends on one.
    """
    ticker = position.ticker
    if fin is None:
        return None

    broken = gate_break(fin, max_de=max_de)
    if broken is not None:
        return ExitVerdict(ticker, sell=True, reason=broken)

    points = readings(score_quality(fin, cache=cache, as_of=as_of))
    passing = [p for p in points if p.passed is True]
    failing = [p for p in points if p.passed is False]
    unknown = [p for p in points if p.passed is None]

    if len(passing) + len(unknown) < ENTRY_MIN_POINTS:
        return ExitVerdict(
            ticker,
            sell=True,
            reason=(
                f"15-point assessment deteriorated: {len(passing)} of the "
                f"{QUANT_POINTS} quant-checkable points pass, under the "
                f"{ENTRY_MIN_POINTS} the entry screen requires; failing: "
                + "; ".join(p.detail for p in failing)
            ),
        )
    if len(passing) >= ENTRY_MIN_POINTS:
        rest = failing + unknown
        tail = (
            "; also " + "; ".join(p.detail for p in rest) if rest else ""
        )
        return ExitVerdict(
            ticker,
            sell=False,
            reason=(
                f"15-point assessment intact: {len(passing)} of the "
                f"{QUANT_POINTS} quant-checkable points pass, at least the "
                f"{ENTRY_MIN_POINTS} the entry screen requires; Fisher's "
                f"default is never sell{tail}"
            ),
        )
    logger.info(
        f"{ticker}: {len(passing)} points pass, {len(failing)} fail, "
        f"{len(unknown)} not measurable — no Fisher exit verdict"
    )
    return None


def verdicts(
    held: Mapping[str, HeldPosition] | None,
    fundamentals: FundamentalsLookup,
    *,
    cache: EdgarCache,
    as_of: date,
    max_de: float = DEFAULT_MAX_DE,
) -> list[ExitVerdict]:
    """A verdict for every held name that can be judged, by ticker order.

    Fundamentals come from the same lookup the screen reads, so a held
    name is assessed on the same filings as a candidate.
    """
    if not held:
        return []
    out: list[ExitVerdict] = []
    for ticker in sorted(held):
        verdict = decide(
            held[ticker],
            fundamentals.get(ticker),
            cache=cache,
            as_of=as_of,
            max_de=max_de,
        )
        if verdict is None:
            logger.info(f"{as_of}: {ticker} held — no Fisher exit verdict (data)")
            continue
        out.append(verdict)
    if out:
        sells = sum(1 for v in out if v.sell)
        logger.info(
            f"{as_of}: Fisher exits on {len(held)} held: "
            f"{len(out) - sells} keep, {sells} sell, "
            f"{len(held) - len(out)} no verdict"
        )
    return out


__all__ = [
    "ENTRY_MIN_POINTS",
    "QUANT_POINTS",
    "PointReading",
    "decide",
    "gate_break",
    "readings",
    "verdicts",
]
