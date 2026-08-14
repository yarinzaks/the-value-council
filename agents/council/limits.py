"""Part 4's hard limits — the floor that does not move.

The doctrine allows a 25% position, which is far beyond anything else in
this project. That ambition is only survivable because these limits are
checked every run and a breach forces action regardless of conviction.

Every limit here is a number from Part 4 of ``THE_VALUE_COUNCIL.md``, not
an interpretation of one.

The distinction that matters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A limit is either **entry-binding** (it stops a new position) or
**forcing** (it makes you act on one you already hold). The single-name
cap is both, at two different thresholds: 25% at entry, trim above 35%
on appreciation. Conflating them would either forbid a position that
grew into its size — which is what winning looks like — or permit
entering at a size the doctrine reserves for a holding that earned it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from core.logger import get_logger

logger = get_logger("agents.council.limits")

#: Largest a position may be when it is opened.
MAX_POSITION_AT_ENTRY: Final[float] = 0.25

#: Above this, appreciation must be trimmed back. Deliberately higher
#: than the entry cap: a position that grew here did so by being right.
TRIM_ABOVE: Final[float] = 0.35

#: Any set of names moving together (rho > 0.7) is one bet. Three AI
#: names is not diversification, it is the same bet held three times.
MAX_CORRELATED_CLUSTER: Final[float] = 0.45

#: Aggregate cap on names below the liquidity floor.
MAX_ILLIQUID_AGGREGATE: Final[float] = 0.20

#: A position is illiquid below this average daily dollar volume.
ILLIQUID_ADV_USD: Final[float] = 5_000_000.0

#: Never fully invested.
MIN_CASH: Final[float] = 0.05

#: Drawdown from peak beyond which no new position may be opened until a
#: human has reviewed. Not an exit rule — the doctrine is explicit that
#: volatility is not loss.
CIRCUIT_BREAKER_DRAWDOWN: Final[float] = -0.25


class LimitState(StrEnum):
    PASS = "pass"
    BREACH = "breach"
    #: The inputs to judge this limit were not available. Never silently
    #: a pass: an unmeasurable limit is reported so it can be fixed.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LimitCheck:
    """One limit, what was observed, and whether it holds."""

    limit: str
    observed: float | None
    cap: float
    state: LimitState
    #: Set when the breach requires an action rather than only blocking
    #: a new position.
    forces_action: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "limit": self.limit,
            "observed": self.observed,
            "cap": self.cap,
            "state": str(self.state),
            "forces_action": self.forces_action,
            "note": self.note,
        }


@dataclass(frozen=True)
class Position:
    """The minimum a limit check needs to know about a holding."""

    ticker: str
    weight: float
    #: Median daily dollar volume, or None when it could not be measured.
    adv_usd: float | None = None
    #: Names that move together carry the same cluster label. None means
    #: unclustered, which is not the same as "in a cluster of one" —
    #: unlabelled names cannot be checked and are reported as such.
    cluster: str | None = None


def check_single_names(positions: Sequence[Position]) -> list[LimitCheck]:
    """The 35% trim rule, per position.

    Entry sizing is checked elsewhere, before a position exists. This is
    the forcing half: anything that has appreciated past 35% must be cut
    back whatever the thesis says.
    """
    checks: list[LimitCheck] = []
    for p in sorted(positions, key=lambda x: -x.weight):
        breached = p.weight > TRIM_ABOVE
        checks.append(
            LimitCheck(
                limit=f"trim_above_35pct:{p.ticker}",
                observed=p.weight,
                cap=TRIM_ABOVE,
                state=LimitState.BREACH if breached else LimitState.PASS,
                forces_action=breached,
                note="trim to 35%" if breached else "",
            )
        )
    return checks


def check_clusters(positions: Sequence[Position]) -> list[LimitCheck]:
    """Correlated clusters against the 45% cap.

    Unlabelled positions are reported once as UNKNOWN rather than being
    treated as uncorrelated. Assuming independence is the error this
    limit exists to prevent.
    """
    totals: dict[str, float] = {}
    unlabelled = 0.0
    for p in positions:
        if p.cluster is None:
            unlabelled += p.weight
            continue
        totals[p.cluster] = totals.get(p.cluster, 0.0) + p.weight

    checks = [
        LimitCheck(
            limit=f"correlated_cluster:{name}",
            observed=weight,
            cap=MAX_CORRELATED_CLUSTER,
            state=(
                LimitState.BREACH
                if weight > MAX_CORRELATED_CLUSTER
                else LimitState.PASS
            ),
            forces_action=weight > MAX_CORRELATED_CLUSTER,
        )
        for name, weight in sorted(totals.items(), key=lambda kv: -kv[1])
    ]

    if unlabelled > 0:
        checks.append(
            LimitCheck(
                limit="correlated_cluster:unlabelled",
                observed=unlabelled,
                cap=MAX_CORRELATED_CLUSTER,
                state=LimitState.UNKNOWN,
                note=(
                    f"{unlabelled:.1%} of the book carries no cluster label and "
                    "cannot be checked against the 45% cap"
                ),
            )
        )
    return checks


def check_illiquid(positions: Sequence[Position]) -> LimitCheck:
    """Aggregate weight in names below the ADV floor."""
    unknown = sum(p.weight for p in positions if p.adv_usd is None)
    illiquid = sum(
        p.weight
        for p in positions
        if p.adv_usd is not None and p.adv_usd < ILLIQUID_ADV_USD
    )
    if unknown > 0:
        return LimitCheck(
            limit="illiquid_aggregate",
            observed=illiquid,
            cap=MAX_ILLIQUID_AGGREGATE,
            state=LimitState.UNKNOWN,
            note=f"{unknown:.1%} of the book has no measured ADV",
        )
    breached = illiquid > MAX_ILLIQUID_AGGREGATE
    return LimitCheck(
        limit="illiquid_aggregate",
        observed=illiquid,
        cap=MAX_ILLIQUID_AGGREGATE,
        state=LimitState.BREACH if breached else LimitState.PASS,
        forces_action=breached,
    )


def check_cash(cash_weight: float) -> LimitCheck:
    """The 5% floor. Being fully invested is itself a breach."""
    breached = cash_weight < MIN_CASH
    return LimitCheck(
        limit="cash_floor",
        observed=cash_weight,
        cap=MIN_CASH,
        state=LimitState.BREACH if breached else LimitState.PASS,
        forces_action=breached,
        note="raise cash" if breached else "",
    )


def check_leverage(gross_exposure: float) -> LimitCheck:
    """No leverage, ever.

    Stated as gross exposure rather than a borrowing flag because that is
    what can actually be observed from a book: if the positions sum to
    more than the capital, something was borrowed.
    """
    breached = gross_exposure > 1.0
    return LimitCheck(
        limit="leverage",
        observed=gross_exposure,
        cap=1.0,
        state=LimitState.BREACH if breached else LimitState.PASS,
        forces_action=breached,
        note="deleverage immediately" if breached else "",
    )


def check_drawdown(nav: float, peak_nav: float) -> LimitCheck:
    """The circuit breaker.

    Blocks new positions; it does not force a sale. The doctrine is
    explicit that volatility is not loss, and a drawdown rule that
    liquidated would convert a time-horizon edge into a short-horizon
    defeat — the most expensive error available here.
    """
    if peak_nav <= 0:
        return LimitCheck(
            limit="drawdown_circuit_breaker",
            observed=None,
            cap=CIRCUIT_BREAKER_DRAWDOWN,
            state=LimitState.UNKNOWN,
            note="no peak recorded yet",
        )
    drawdown = (nav / peak_nav) - 1.0
    breached = drawdown <= CIRCUIT_BREAKER_DRAWDOWN
    return LimitCheck(
        limit="drawdown_circuit_breaker",
        observed=drawdown,
        cap=CIRCUIT_BREAKER_DRAWDOWN,
        state=LimitState.BREACH if breached else LimitState.PASS,
        forces_action=False,  # blocks entry; never forces a sale
        note="no new positions until a human reviews" if breached else "",
    )


def entry_allowed(weight: float) -> LimitCheck:
    """Whether a proposed new position clears the 25% entry cap."""
    breached = weight > MAX_POSITION_AT_ENTRY
    return LimitCheck(
        limit="max_position_at_entry",
        observed=weight,
        cap=MAX_POSITION_AT_ENTRY,
        state=LimitState.BREACH if breached else LimitState.PASS,
    )


def check_all(
    positions: Sequence[Position],
    *,
    cash_weight: float,
    nav: float,
    peak_nav: float,
) -> list[LimitCheck]:
    """Every Part 4 limit that can be judged from the book alone."""
    gross = sum(p.weight for p in positions)
    checks: list[LimitCheck] = []
    checks.extend(check_single_names(positions))
    checks.extend(check_clusters(positions))
    checks.append(check_illiquid(positions))
    checks.append(check_cash(cash_weight))
    checks.append(check_leverage(gross))
    checks.append(check_drawdown(nav, peak_nav))
    return checks


def breaches(checks: Sequence[LimitCheck]) -> list[LimitCheck]:
    return [c for c in checks if c.state is LimitState.BREACH]


def unknowns(checks: Sequence[LimitCheck]) -> list[LimitCheck]:
    return [c for c in checks if c.state is LimitState.UNKNOWN]


def positions_from_portfolio(
    positions: Sequence[Mapping[str, object]],
    *,
    nav: float,
    adv: Mapping[str, float] | None = None,
    clusters: Mapping[str, str] | None = None,
) -> list[Position]:
    """Adapt the live-portfolio JSON shape to what these checks need."""
    if nav <= 0:
        return []
    out: list[Position] = []
    for p in positions:
        ticker = str(p["ticker"])
        value = float(p["shares"]) * float(p["current_price"])  # type: ignore[arg-type]
        out.append(
            Position(
                ticker=ticker,
                weight=value / nav,
                adv_usd=(adv or {}).get(ticker),
                cluster=(clusters or {}).get(ticker),
            )
        )
    return out


__all__ = [
    "CIRCUIT_BREAKER_DRAWDOWN",
    "ILLIQUID_ADV_USD",
    "MAX_CORRELATED_CLUSTER",
    "MAX_ILLIQUID_AGGREGATE",
    "MAX_POSITION_AT_ENTRY",
    "MIN_CASH",
    "TRIM_ABOVE",
    "LimitCheck",
    "LimitState",
    "Position",
    "breaches",
    "check_all",
    "check_cash",
    "check_clusters",
    "check_drawdown",
    "check_illiquid",
    "check_leverage",
    "check_single_names",
    "entry_allowed",
    "positions_from_portfolio",
    "unknowns",
]
