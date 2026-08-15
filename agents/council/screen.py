"""The screen: what makes a company a candidate.

``COUNCIL_SELECTION.md`` section 2. Four gates, all of which must pass.
Gate A is the only one with alternatives inside it — three separate
paths to a structural floor, any one of which is enough.

The rule that shapes everything here
------------------------------------

**A gate that cannot be computed FAILS.** UNKNOWN is not a pass. This is
the doctrine's "missing is not zero" applied to the one place where
getting it wrong buys something: a screen that treats an unreadable
balance sheet as a clean one will, reliably, hand you the companies
whose filings are worst. Every gate therefore returns an explicit
state, and the absence of a number is recorded as its own outcome
rather than folded into a boolean.

The one softness is inside Gate A, and it is deliberate: its three paths
are alternatives, so a company whose goodwill has not been tagged since
2017 simply cannot use the tangible-book path. That closes a door, it
does not disqualify the name.

Why not P/E, and why not debt-to-equity
---------------------------------------

Both were considered and rejected in the doctrine, for reasons worth
keeping next to the code. P/E mixes the capital structure into the
multiple, so a levered company looks cheap precisely because it is
risky; EV/EBIT prices the whole firm and cannot be fooled that way.
Debt-to-equity divides by book equity, which buybacks can legitimately
drive negative — AutoZone has run negative book equity for years while
compounding — so it screams at some of the best operators and stays
silent on genuinely fragile ones. Net-debt-to-EBIT measures the thing
actually feared: whether the cash flows can carry the debt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from core.logger import get_logger

logger = get_logger("agents.council.screen")

#: Gate A path 1. The ceiling on EV/EBIT.
MAX_EV_TO_EBIT: float = 8.0

#: Gate A path 1's rate guard. The ceiling becomes
#: ``min(8, 1 / (DGS10 + RATE_GUARD_SPREAD))``, which binds only once
#: the 10-year yield passes 8.5%. It exists so a fixed multiple does not
#: keep calling things cheap in a rate regime where nothing is.
RATE_GUARD_SPREAD: float = 0.04

#: Gate A path 2. Net cash as a share of market cap.
MIN_NET_CASH_TO_MARKET_CAP: float = 0.25

#: Gate B. Leverage ceiling for a profitable company.
MAX_NET_DEBT_TO_EBIT: float = 3.0

#: Gate B's cash-box path. A company burning cash must hold at least
#: this many years of runway at the current burn.
MIN_CASH_BOX_RUNWAY_YEARS: float = 3.0

#: Gate C. Accruals ceiling, as a share of total assets. Earnings a
#: long way above the cash that arrived is the single most reliable
#: warning sign the balance sheet gives.
MAX_ACCRUALS_TO_ASSETS: float = 0.10

#: Gate C. Three-year share-count growth ceiling. Serial dilution turns
#: business success into shareholder failure.
MAX_SHARES_CAGR_3Y: float = 0.03


class Outcome(StrEnum):
    """Why a gate answered the way it did."""

    PASS = "pass"
    FAIL = "fail"
    #: The inputs were not available. Treated as FAIL by
    #: :meth:`ScreenResult.passed`, and kept distinct so the run log can
    #: tell a company that failed from one that could not be read.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict, with the number that produced it."""

    gate: str
    outcome: Outcome
    detail: str

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.PASS


@dataclass
class ScreenResult:
    """Every gate's verdict for one company."""

    ticker: str
    gates: list[GateResult] = field(default_factory=list)
    #: Which Gate A path carried it, if any. Recorded because the
    #: journal's thesis has to name the structural floor in one
    #: sentence, and this is that sentence's subject.
    floor: str | None = None
    #: True when Gate B was satisfied by the cash-box branch, which is
    #: the only thing that waives Gate C's positive-cash-flow test.
    cash_box: bool = False

    @property
    def passed(self) -> bool:
        """All four gates pass. UNKNOWN is not a pass."""
        return bool(self.gates) and all(g.ok for g in self.gates)

    @property
    def failures(self) -> list[GateResult]:
        return [g for g in self.gates if not g.ok]

    def __str__(self) -> str:
        if self.passed:
            return f"{self.ticker}: candidate ({self.floor})"
        first = self.failures[0]
        return f"{self.ticker}: {first.outcome} on {first.gate} — {first.detail}"


@dataclass(frozen=True)
class Financials:
    """Everything the four gates read, already point-in-time.

    Deliberately a plain value object with no fetching of its own. The
    gates are the doctrine and have to be testable without a network,
    a cache or a clock; assembling this is somebody else's job.

    Every field is optional because every field can genuinely be absent,
    and the gates are written to say so rather than to substitute a zero.
    """

    ticker: str
    market_cap: float | None = None
    enterprise_value: float | None = None
    ebit_ttm: float | None = None
    cfo_ttm: float | None = None
    fcf_ttm: float | None = None
    net_income_ttm: float | None = None
    total_assets: float | None = None
    #: total equity minus goodwill minus other intangibles.
    tangible_book: float | None = None
    net_cash: float | None = None
    #: Positive means debt exceeds cash. ``-net_cash`` where both are
    #: known, carried separately so a caller can supply one without the
    #: other.
    net_debt: float | None = None
    shares_cagr_3y: float | None = None
    #: The 10-year Treasury yield as a decimal, for Gate A's rate guard.
    #: ``None`` leaves the ceiling at its default rather than blocking:
    #: a FRED outage must not close the cheapest path in the screen.
    ten_year_yield: float | None = None


@dataclass(frozen=True)
class FilingFlags:
    """Gate D's inputs. Any one of these disqualifies outright.

    ``None`` means the check could not be run, which fails the gate.
    ``False`` means it was run and found nothing.
    """

    ticker: str
    restatement_8k_402: bool | None = None
    going_concern: bool | None = None
    material_weakness: bool | None = None
    late_filing: bool | None = None


def ev_to_ebit_ceiling(ten_year_yield: float | None) -> float:
    """Gate A's cheapness ceiling, guarded against a high-rate regime.

    At any yield below 4.5% this returns the flat 8.0. Above it the
    reciprocal binds, so "cheap" tightens as the risk-free alternative
    improves rather than staying anchored to a number chosen in a
    different decade.
    """
    if ten_year_yield is None:
        return MAX_EV_TO_EBIT
    denominator = ten_year_yield + RATE_GUARD_SPREAD
    if denominator <= 0:
        return MAX_EV_TO_EBIT
    return min(MAX_EV_TO_EBIT, 1.0 / denominator)


def gate_a(f: Financials) -> GateResult:
    """Cheap enough — at least one structural floor.

    The three paths are alternatives, so an input missing for one does
    not close the others. What is required is that *some* path can be
    both computed and satisfied; a company where none of the three can
    even be evaluated is UNKNOWN, not FAIL, because nothing about it was
    actually tested.
    """
    reasons: list[str] = []
    evaluated = False

    # Path 1 — earnings yield on the whole firm.
    if f.enterprise_value is not None and f.ebit_ttm is not None:
        evaluated = True
        if f.ebit_ttm > 0:
            ceiling = ev_to_ebit_ceiling(f.ten_year_yield)
            multiple = f.enterprise_value / f.ebit_ttm
            if multiple <= ceiling:
                return GateResult(
                    "A", Outcome.PASS, f"EV/EBIT {multiple:.2f} <= {ceiling:.2f}"
                )
            reasons.append(f"EV/EBIT {multiple:.2f} > {ceiling:.2f}")
        else:
            reasons.append(f"EBIT_TTM {f.ebit_ttm:,.0f} not positive")

    # Path 2 — net cash as a share of what the market pays.
    if f.net_cash is not None and f.market_cap is not None and f.market_cap > 0:
        evaluated = True
        share = f.net_cash / f.market_cap
        if share >= MIN_NET_CASH_TO_MARKET_CAP:
            return GateResult(
                "A",
                Outcome.PASS,
                f"net cash {share:.1%} of market cap "
                f">= {MIN_NET_CASH_TO_MARKET_CAP:.0%}",
            )
        reasons.append(f"net cash {share:.1%} of market cap")

    # Path 3 — below the value of what it owns outright.
    if f.tangible_book is not None and f.market_cap is not None:
        if f.tangible_book > 0:
            evaluated = True
            if f.market_cap <= f.tangible_book:
                return GateResult(
                    "A",
                    Outcome.PASS,
                    f"market cap {f.market_cap:,.0f} <= "
                    f"tangible book {f.tangible_book:,.0f}",
                )
            reasons.append(
                f"price/tangible book {f.market_cap / f.tangible_book:.2f}"
            )
        else:
            reasons.append("tangible book not positive")

    if not evaluated:
        return GateResult("A", Outcome.UNKNOWN, "no path could be evaluated")
    return GateResult("A", Outcome.FAIL, "; ".join(reasons))


def gate_b(f: Financials) -> tuple[GateResult, bool]:
    """Survivable — the debt is carryable, or there is no debt to carry.

    Returns the verdict and whether the cash-box branch was taken, which
    is the only thing that waives Gate C's positive-cash-flow test.
    """
    if f.ebit_ttm is None:
        return GateResult("B", Outcome.UNKNOWN, "EBIT_TTM unavailable"), False

    if f.ebit_ttm > 0:
        if f.net_debt is None:
            return GateResult("B", Outcome.UNKNOWN, "net debt unavailable"), False
        ratio = f.net_debt / f.ebit_ttm
        if ratio <= MAX_NET_DEBT_TO_EBIT:
            return (
                GateResult(
                    "B",
                    Outcome.PASS,
                    f"net debt/EBIT {ratio:.2f} <= {MAX_NET_DEBT_TO_EBIT:.1f}",
                ),
                False,
            )
        return (
            GateResult(
                "B",
                Outcome.FAIL,
                f"net debt/EBIT {ratio:.2f} > {MAX_NET_DEBT_TO_EBIT:.1f}",
            ),
            False,
        )

    # Loss-making: the only way through is a cash box with real runway.
    if f.net_cash is None or f.fcf_ttm is None:
        return (
            GateResult("B", Outcome.UNKNOWN, "net cash or FCF_TTM unavailable"),
            False,
        )
    if f.net_cash <= 0:
        return (
            GateResult("B", Outcome.FAIL, f"unprofitable with net cash {f.net_cash:,.0f}"),
            False,
        )
    if f.fcf_ttm >= 0:
        return (
            GateResult("B", Outcome.PASS, "unprofitable but net cash and FCF positive"),
            True,
        )
    runway = f.net_cash / abs(f.fcf_ttm)
    if runway >= MIN_CASH_BOX_RUNWAY_YEARS:
        return (
            GateResult("B", Outcome.PASS, f"cash box with {runway:.1f} years of runway"),
            True,
        )
    return (
        GateResult(
            "B",
            Outcome.FAIL,
            f"burning cash with {runway:.1f} years of runway, "
            f"below {MIN_CASH_BOX_RUNWAY_YEARS:.0f}",
        ),
        False,
    )


def gate_c(f: Financials, *, cash_box: bool) -> GateResult:
    """Not a fake — the earnings arrived as cash and the count held.

    Args:
        cash_box: Set when Gate B passed on its cash-box branch. That
            branch already established positive net cash and adequate
            runway, so requiring positive operating cash flow on top
            would make the branch unreachable — a company with positive
            CFO is not the loss-maker the branch was written for.
    """
    reasons: list[str] = []

    if not cash_box:
        if f.cfo_ttm is None:
            return GateResult("C", Outcome.UNKNOWN, "CFO_TTM unavailable")
        if f.cfo_ttm <= 0:
            reasons.append(f"CFO_TTM {f.cfo_ttm:,.0f} not positive")

    if f.net_income_ttm is None or f.cfo_ttm is None or f.total_assets is None:
        return GateResult("C", Outcome.UNKNOWN, "accruals inputs unavailable")
    if f.total_assets <= 0:
        return GateResult("C", Outcome.UNKNOWN, "total assets not positive")
    accruals = (f.net_income_ttm - f.cfo_ttm) / f.total_assets
    if accruals > MAX_ACCRUALS_TO_ASSETS:
        reasons.append(f"accruals {accruals:.1%} > {MAX_ACCRUALS_TO_ASSETS:.0%}")

    if f.shares_cagr_3y is None:
        return GateResult("C", Outcome.UNKNOWN, "3y share-count CAGR unavailable")
    if f.shares_cagr_3y > MAX_SHARES_CAGR_3Y:
        reasons.append(
            f"shares +{f.shares_cagr_3y:.1%}/yr > +{MAX_SHARES_CAGR_3Y:.0%}"
        )

    if reasons:
        return GateResult("C", Outcome.FAIL, "; ".join(reasons))
    return GateResult(
        "C",
        Outcome.PASS,
        f"accruals {accruals:.1%}, shares {f.shares_cagr_3y:+.1%}/yr",
    )


def gate_d(flags: FilingFlags) -> GateResult:
    """Untouchable — any one of these ends it.

    This is the same set the post-trade veto machinery already watches.
    Here it acts before the trade, which is the cheaper place for it.
    """
    checks = (
        ("8-K item 4.02 within 24 months", flags.restatement_8k_402),
        ("going-concern language in the latest 10-K", flags.going_concern),
        ("material weakness in the latest 10-K", flags.material_weakness),
        ("late filing (NT 10-K/NT 10-Q) within 12 months", flags.late_filing),
    )
    tripped = [name for name, value in checks if value is True]
    if tripped:
        return GateResult("D", Outcome.FAIL, "; ".join(tripped))
    unchecked = [name for name, value in checks if value is None]
    if unchecked:
        return GateResult(
            "D", Outcome.UNKNOWN, f"not checked: {', '.join(unchecked)}"
        )
    return GateResult("D", Outcome.PASS, "no disqualifying filing")


def screen(
    financials: Financials,
    flags: FilingFlags,
    *,
    as_of: date | None = None,
) -> ScreenResult:
    """Run all four gates.

    Every gate is evaluated even once one has failed. A screen that
    short-circuits saves nothing measurable and costs the run log the
    ability to say *how many* ways a name was disqualified, which is
    what tells a stale gate apart from a genuinely bad company.
    """
    result = ScreenResult(ticker=financials.ticker)

    a = gate_a(financials)
    b, cash_box = gate_b(financials)
    result.cash_box = cash_box
    c = gate_c(financials, cash_box=cash_box)
    d = gate_d(flags)
    result.gates = [a, b, c, d]

    if a.ok:
        result.floor = a.detail

    if result.passed:
        logger.info(f"{as_of or ''} {financials.ticker}: candidate — {a.detail}")
    return result
