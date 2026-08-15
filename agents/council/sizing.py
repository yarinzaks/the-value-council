"""How big a position is, and how much of the book each sleeve may hold.

``COUNCIL_SELECTION.md`` sections 4, 5 and 9.1.

Three sleeves, three deciders
-----------------------------

===========  =============================  ==========================
Sleeve       Who picks                      Who sells
===========  =============================  ==========================
Statistical  the screen and rank, no LLM    the rank plus hard events
Event        mechanical triggers            expiry plus trailing stop
Core         the Council only               kill criteria, fair value
===========  =============================  ==========================

The statistical sleeve exists so capital is not idle while the punch
card fills at its correct pace of nought to two core names a year. As
core names are approved, statistical shrinks pro rata toward its 20%
floor and never below.

Why equal weight, and not rank-tilted
-------------------------------------

With twenty anonymous names, tilting toward the top of the rank adds
estimation error rather than return. That is doctrine Part 4's whole
argument for the statistical case: you believe the screen works on
average and you cannot say which specific name will work. Sizing by
conviction requires a conviction you do not have.

The dial gates new capital only
-------------------------------

Section 9.1's ceilings tighten what may be *added*. They never force a
sale — a sleeve above its ceiling simply cannot add, and the cash floors
are reached by not buying. Core entries are not gated by the dial at
all: they answer to the Council and the circuit breaker, because the
moment everyone else is forced out is exactly when the doctrine wants
the Council able to act.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.council.exits import Sleeve
from core.logger import get_logger

logger = get_logger("agents.council.sizing")

#: Section 4. The statistical sleeve holds twenty names, or fewer when
#: fewer qualify — the system is allowed to say nothing is cheap enough.
STATISTICAL_NAMES: int = 20

#: Section 5 caps, per name, as a share of the whole book.
MAX_POSITION_STATISTICAL: float = 0.05
EVENT_ENTRY_SIZE: float = 0.03
MAX_POSITION_EVENT: float = 0.04
MAX_POSITION_CORE: float = 0.25

#: Section 5's bootstrap: day one is statistical 45% and cash 55%.
STATISTICAL_START_WEIGHT: float = 0.45

#: Section 3's floor. Statistical shrinks pro rata as core fills, and
#: stops here.
STATISTICAL_FLOOR_WEIGHT: float = 0.20

#: Section 4's ceiling on the core sleeve at maturity.
CORE_CEILING_WEIGHT: float = 0.60

#: Doctrine Part 4's absolute floor, beneath section 9.1's
#: regime-dependent ones.
HARD_CASH_FLOOR: float = 0.05


@dataclass(frozen=True)
class RegimeCeilings:
    """What section 9.1 permits at a given risk-on count."""

    risk_on_count: int | None
    statistical_ceiling: float
    event_ceiling: float
    #: 1.0 at full size, 0.5 at half, 0.0 when no mechanical entry is
    #: allowed at all.
    entry_scale: float
    cash_floor: float

    @property
    def mechanical_entries_allowed(self) -> bool:
        return self.entry_scale > 0.0


#: Section 9.1, verbatim. Indexed by how many of the four FRED dials
#: read risk-on.
_CEILINGS: dict[int, RegimeCeilings] = {
    4: RegimeCeilings(4, 0.45, 0.15, 1.0, 0.05),
    3: RegimeCeilings(3, 0.45, 0.15, 1.0, 0.05),
    2: RegimeCeilings(2, 0.35, 0.10, 0.5, 0.10),
    1: RegimeCeilings(1, 0.25, 0.05, 0.0, 0.15),
    0: RegimeCeilings(0, 0.20, 0.05, 0.0, 0.20),
}


def ceilings_for(risk_on_count: int | None) -> RegimeCeilings:
    """Section 9.1's row for ``risk_on_count``.

    ``None`` — a FRED outage — returns the most restrictive row rather
    than the most permissive. The regime module already refuses to count
    an unreadable dial as risk-on; this is the same reasoning one layer
    up, and it is why an outage tightens the book rather than loosening
    it.
    """
    if risk_on_count is None:
        logger.warning("regime unreadable — applying the 0-dial ceilings")
        return _CEILINGS[0]
    return _CEILINGS[max(0, min(4, risk_on_count))]


def position_cap(sleeve: Sleeve) -> float:
    """The most one name of ``sleeve`` may be, as a share of the book."""
    return {
        Sleeve.STATISTICAL: MAX_POSITION_STATISTICAL,
        Sleeve.EVENT: MAX_POSITION_EVENT,
        Sleeve.CORE: MAX_POSITION_CORE,
    }[sleeve]


def statistical_entry_weight(
    *,
    sleeve_weight: float,
    names: int = STATISTICAL_NAMES,
    entry_scale: float = 1.0,
) -> float:
    """Equal weight across the sleeve, capped per name.

    Args:
        sleeve_weight: What share of the book the sleeve may occupy.
        names: How many names it holds. Values below one are treated as
            one, so a sleeve with a single qualifier does not divide by
            zero and take the whole book instead.
        entry_scale: Section 9.1's half-size factor.

    Returns:
        The target weight for one name. Note the arithmetic: at the 45%
        start over twenty names this is 2.25%, so section 5's headline
        5% per-name cap never binds on the mechanical path. It is
        applied anyway, because the cap is what holds if the sleeve
        count is ever configured smaller.
    """
    each = sleeve_weight / max(1, names)
    return min(MAX_POSITION_STATISTICAL, each * entry_scale)


def event_entry_weight(*, entry_scale: float = 1.0) -> float:
    """Section 5: 3% fixed, capped at 4%."""
    return min(MAX_POSITION_EVENT, EVENT_ENTRY_SIZE * entry_scale)


def shrink_probability(p: float) -> float:
    """Pull a stated probability toward a coin flip.

    ``p_used = 0.5 + 0.6 (p - 0.5)``, from the runbook's Seat 6. Every
    forecaster is overconfident; the journal's calibration run measures
    by how much, and until it has enough resolved forecasts to say, this
    fixed shrinkage stands in. Applied before Kelly, never after —
    Kelly on an unshrunk probability is how a 65% belief becomes a
    44% position.
    """
    return 0.5 + 0.6 * (p - 0.5)


def kelly_fraction(p: float, *, win: float, loss: float) -> float:
    """The Kelly **position weight** for a two-outcome bet, floored at zero.

    ``w = p/loss - (1-p)/win``.

    The distinction that matters is stake versus weight. Textbook Kelly
    answers with the fraction of the bankroll to put fully at risk, but
    an equity position does not go to zero when the thesis is wrong — it
    falls by ``loss``. A position of weight ``w`` therefore risks
    ``w x loss``, and solving for ``w`` divides by the loss rather than
    multiplying by it. Multiplying instead understates every core
    position by a factor of ``1/loss squared``: on a bet that falls 30%
    when wrong, an 80% Kelly weight arrives as 7%.

    Args:
        p: Probability of the win case, already shrunk. Passing a raw
            probability here is the mistake :func:`shrink_probability`
            exists to prevent.
        win: Fractional gain if right, e.g. 1.5 for +150%.
        loss: Fractional loss if wrong, as a positive number.

    Returns:
        The share of the book Kelly would hold, or 0.0 when the bet has
        no edge. Never negative: this book does not short. Frequently
        larger than any cap in the doctrine, which is the expected
        outcome — full Kelly on a real edge is enormous, and Part 4's
        25% ceiling is what stands between that arithmetic and the book.
    """
    if win <= 0 or loss <= 0:
        return 0.0
    return max(0.0, p / loss - (1.0 - p) / win)


@dataclass(frozen=True)
class SizedEntry:
    """A proposed position size and which limit produced it."""

    ticker: str
    sleeve: Sleeve
    weight: float
    #: Which constraint bound: ``"kelly"``, ``"sleeve_cap"``,
    #: ``"position_cap"``, ``"cash_floor"`` or ``"regime"``. Recorded
    #: because a size the reader cannot attribute is a size nobody can
    #: audit two years later.
    binding: str


def size_core_entry(
    ticker: str,
    *,
    probability: float,
    win: float,
    loss: float,
    sleeve_headroom: float,
    cash_headroom: float,
) -> SizedEntry:
    """Runbook Seat 6: shrink the probability, then Kelly, then the caps.

    Args:
        probability: The Chair's raw probability for the bull case.
        win: Fractional upside in that case.
        loss: Fractional downside if wrong, positive.
        sleeve_headroom: How much of the book the core sleeve may still
            take, after what it already holds.
        cash_headroom: How much may be spent before the cash floor binds.
    """
    raw = kelly_fraction(shrink_probability(probability), win=win, loss=loss)
    candidates = [
        (raw, "kelly"),
        (MAX_POSITION_CORE, "position_cap"),
        (max(0.0, sleeve_headroom), "sleeve_cap"),
        (max(0.0, cash_headroom), "cash_floor"),
    ]
    weight, binding = min(candidates, key=lambda c: c[0])
    return SizedEntry(ticker=ticker, sleeve=Sleeve.CORE, weight=weight, binding=binding)


def sleeve_headroom(
    *,
    sleeve: Sleeve,
    held_weight: float,
    ceilings: RegimeCeilings,
    core_weight: float = 0.0,
) -> float:
    """How much more of the book ``sleeve`` may take, never negative.

    A sleeve already above its ceiling gets zero rather than a negative
    number: section 9.1 is explicit that the dial gates new capital and
    never forces a sale, so "you are 3% over" must read as "you may not
    add", not as an instruction to sell 3%.

    Args:
        core_weight: What core already holds. The statistical sleeve
            shrinks pro rata as core fills, but stops at its 20% floor,
            so this tightens the statistical ceiling and nothing else.
    """
    if sleeve is Sleeve.CORE:
        ceiling = CORE_CEILING_WEIGHT
    elif sleeve is Sleeve.EVENT:
        ceiling = ceilings.event_ceiling
    else:
        crowded_out = max(
            STATISTICAL_FLOOR_WEIGHT, STATISTICAL_START_WEIGHT - core_weight
        )
        ceiling = min(ceilings.statistical_ceiling, crowded_out)
    return max(0.0, ceiling - held_weight)
