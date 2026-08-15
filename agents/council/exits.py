"""The exit engine: how a position ends.

``COUNCIL_SELECTION.md`` section 6, and the piece everything already
built was missing. The regime dial, the position caps, the circuit
breaker, the filings watch and the punch card all say *no* to opening
something. Nothing in them closes anything, and a book that can only
buy is not a strategy.

The invariant
-------------

**Every BUY carries an exit block valid for its sleeve, or execution
refuses it.** :func:`validate_exit_block` is that check, and it is meant
to be called at the order layer rather than trusted to discipline. A
position with no live exit rule is a bug, not a holding — the repo's own
history is the argument: a name sat at a dead price for seventy days and
two more for fifty-three, all three found by hand.

Evaluation order, first match wins
----------------------------------

Checked on every heartbeat. The order is not arbitrary: the hard,
factual triggers come before the judgment-shaped ones, so a company
that is both delisting and merely expensive exits for the delisting.

===  ==============================================================
E1   circuit breaker: no buys of any kind; exits below still run
E2   terminal filing or 400-day staleness: sell next session
E3   event expiry, or trailing stop hit on a close
E4   core kill criterion triggered: review, with a deadman behind it
E5   price at or above fair value: trim, then exit
E6   weight over 35% from appreciation: trim to 25%
E7   eight quarterly reviews with no progress: the time stop
E8   at the quarterly rebalance, rank outside the buffer
E9   any Part-4 limit breach
===  ==============================================================

Nothing else sells. Deliberately absent: the price fell, the market
fell, it has been dead money, it is up a lot. And there is **no price
stop** on Statistical or Core positions — Kaminski and Lo show a stop on
a mean-reversion bet pays to be removed just before the reversal you are
buying. Event positions are the only price-stopped ones, because they
are the only trend-shaped bets in the book.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from core.logger import get_logger

logger = get_logger("agents.council.exits")

#: E8. Bought into the top 20, held until rank 40. Without the buffer a
#: name oscillating around rank 20 generates pure cost.
DEFAULT_RANK_BUFFER: int = 40

#: E3. Event positions trail on closing prices only, so an intraday
#: spike cannot take one out.
DEFAULT_TRAIL_ATR_MULTIPLE: float = 2.5
DEFAULT_ATR_PERIODS: int = 14

#: E4's deadman. A kill criterion triggers a review; if nobody performs
#: one, the position leaves anyway. A veto nobody shows up to exercise
#: must fail safe.
REVIEW_DEADLINE_DAYS: int = 5
REVIEW_LIQUIDATION_DAYS: int = 10

#: E5. At fair value trim half; at this multiple of it, leave entirely
#: unless a fresh Council re-underwrites at the new price.
FAIR_VALUE_EXIT_MULTIPLE: float = 1.25

#: E6. Doctrine Part 4: 25% at entry, trim above 35% on appreciation.
#: Section 6 supplies the target the doctrine left unstated.
TRIM_TRIGGER_WEIGHT: float = 0.35
TRIM_TARGET_WEIGHT: float = 0.25

#: E7. Eight quarters is two years of being wrong-looking, which the
#: doctrine budgets for, and the point past which it stops being
#: time-horizon arbitrage and starts being hope.
TIME_STOP_QUARTERS: int = 8

#: Core theses need three falsifiable kill criteria before entry.
#: Journal enforces this too; the exit block will not validate without
#: them either, so neither path can be the one that forgets.
REQUIRED_KILL_CRITERIA: int = 3


class Sleeve(StrEnum):
    """Which book a position belongs to, and therefore how it exits."""

    STATISTICAL = "statistical"
    EVENT = "event"
    CORE = "core"


class Action(StrEnum):
    """What the engine wants done."""

    NONE = "none"
    SELL = "sell"
    SELL_HALF = "sell_half"
    TRIM = "trim"


@dataclass(frozen=True)
class StatisticalExit:
    """Held until the composite rank leaves the buffer."""

    rank_buffer: int = DEFAULT_RANK_BUFFER


@dataclass(frozen=True)
class EventExit:
    """A pre-registered expiry and the only trailing stop in the book."""

    expiry: date
    trail_atr_multiple: float = DEFAULT_TRAIL_ATR_MULTIPLE
    atr_periods: int = DEFAULT_ATR_PERIODS


@dataclass(frozen=True)
class CoreExit:
    """Kill criteria written before entry, plus a fair-value band."""

    kill_criteria: tuple[str, ...]
    fair_value_low: float
    fair_value_high: float


ExitBlock = StatisticalExit | EventExit | CoreExit


class InvalidExitBlockError(ValueError):
    """An exit block that execution must refuse."""


def validate_exit_block(sleeve: Sleeve, block: ExitBlock | None) -> None:
    """Raise unless ``block`` is a live exit rule for ``sleeve``.

    Call this at the order layer, before a BUY is allowed through. It is
    the single check that makes an eternal position impossible, and it
    is a raise rather than a boolean so that ignoring it takes effort.

    Raises:
        InvalidExitBlockError: If the block is missing, belongs to another
            sleeve, or is internally incoherent.
    """
    if block is None:
        raise InvalidExitBlockError(f"{sleeve}: no exit block")

    if sleeve is Sleeve.STATISTICAL:
        if not isinstance(block, StatisticalExit):
            raise InvalidExitBlockError(f"{sleeve}: got {type(block).__name__}")
        if block.rank_buffer <= 0:
            raise InvalidExitBlockError(
                f"rank_buffer {block.rank_buffer} would sell on the day it bought"
            )
        return

    if sleeve is Sleeve.EVENT:
        if not isinstance(block, EventExit):
            raise InvalidExitBlockError(f"{sleeve}: got {type(block).__name__}")
        if block.trail_atr_multiple <= 0:
            raise InvalidExitBlockError(
                f"trail_atr_multiple {block.trail_atr_multiple} is not a stop"
            )
        if block.atr_periods <= 1:
            raise InvalidExitBlockError(f"atr_periods {block.atr_periods} is not a range")
        return

    if not isinstance(block, CoreExit):
        raise InvalidExitBlockError(f"{sleeve}: got {type(block).__name__}")
    if len(block.kill_criteria) < REQUIRED_KILL_CRITERIA:
        raise InvalidExitBlockError(
            f"core needs {REQUIRED_KILL_CRITERIA} kill criteria, "
            f"got {len(block.kill_criteria)} — a thesis with no kill criteria "
            "is an opinion with formatting"
        )
    if any(not c.strip() for c in block.kill_criteria):
        raise InvalidExitBlockError("a blank kill criterion is not a kill criterion")
    if not block.fair_value_high > block.fair_value_low > 0:
        raise InvalidExitBlockError(
            f"fair value band [{block.fair_value_low}, {block.fair_value_high}] "
            "is not an ordered positive range"
        )


@dataclass(frozen=True)
class PositionState:
    """Everything the engine observes about one holding.

    All observations are supplied rather than fetched, so the doctrine
    is testable without a network, a cache or a clock — and so a caller
    that cannot compute one passes ``None`` rather than a default that
    would quietly satisfy a rule.
    """

    ticker: str
    sleeve: Sleeve
    opened: date
    weight: float
    exit_block: ExitBlock

    # E2 — terminal facts about the filer.
    terminal_filing: str | None = None
    filing_age_days: int | None = None

    # E3 — event sleeve only.
    close: float | None = None
    trail_stop_level: float | None = None

    # E4 — core kill criteria and the review that must follow.
    kill_triggered: str | None = None
    trading_days_since_kill: int | None = None
    reviewed_since_kill: bool = False

    # E5 — where the price sits against the underwritten band.
    price: float | None = None
    re_underwritten: bool = False

    # E7 — the time stop.
    quarterly_reviews_without_progress: int | None = None

    # E8 — statistical sleeve only, read at the rebalance.
    composite_rank: int | None = None
    still_qualifies: bool | None = None

    # E9 — a Part-4 breach this position is the largest offender for.
    limit_breach: str | None = None


@dataclass(frozen=True)
class Verdict:
    """What to do with one position, and under which rule."""

    ticker: str
    action: Action
    rule: str
    reason: str
    #: Target weight for :attr:`Action.TRIM`. ``None`` otherwise.
    target_weight: float | None = None

    @property
    def sells(self) -> bool:
        return self.action in (Action.SELL, Action.SELL_HALF)


def _e2(p: PositionState) -> Verdict | None:
    if p.terminal_filing:
        return Verdict(
            p.ticker, Action.SELL, "E2",
            f"{p.terminal_filing} — sell next session, no council, no discussion",
        )
    if p.filing_age_days is not None and p.filing_age_days > 400:
        return Verdict(
            p.ticker, Action.SELL, "E2",
            f"filings {p.filing_age_days} days stale, past the 400-day bound",
        )
    return None


def _e3(p: PositionState, as_of: date) -> Verdict | None:
    if p.sleeve is not Sleeve.EVENT or not isinstance(p.exit_block, EventExit):
        return None
    if as_of >= p.exit_block.expiry:
        return Verdict(
            p.ticker, Action.SELL, "E3",
            f"event expiry {p.exit_block.expiry} reached",
        )
    if (
        p.close is not None
        and p.trail_stop_level is not None
        and p.close <= p.trail_stop_level
    ):
        return Verdict(
            p.ticker, Action.SELL, "E3",
            f"close {p.close:.2f} at or below the "
            f"{p.exit_block.trail_atr_multiple}x ATR"
            f"({p.exit_block.atr_periods}) trail at "
            f"{p.trail_stop_level:.2f}",
        )
    return None


def _e4(p: PositionState) -> Verdict | None:
    """The deadman. A veto nobody exercises must still bite."""
    if p.sleeve is not Sleeve.CORE or not p.kill_triggered:
        return None
    if p.reviewed_since_kill:
        return None
    elapsed = p.trading_days_since_kill
    if elapsed is None:
        return None
    if elapsed >= REVIEW_LIQUIDATION_DAYS:
        return Verdict(
            p.ticker, Action.SELL, "E4",
            f"kill criterion '{p.kill_triggered}' triggered and unreviewed "
            f"for {elapsed} sessions",
        )
    if elapsed >= REVIEW_DEADLINE_DAYS:
        return Verdict(
            p.ticker, Action.SELL_HALF, "E4",
            f"kill criterion '{p.kill_triggered}' triggered and unreviewed "
            f"for {elapsed} sessions — half now, all by day "
            f"{REVIEW_LIQUIDATION_DAYS}",
        )
    return None


def _e5(p: PositionState) -> Verdict | None:
    if p.sleeve is not Sleeve.CORE or not isinstance(p.exit_block, CoreExit):
        return None
    if p.price is None:
        return None
    high = p.exit_block.fair_value_high
    if p.price >= high * FAIR_VALUE_EXIT_MULTIPLE:
        if p.re_underwritten:
            return None  # a new decision, journaled as one
        return Verdict(
            p.ticker, Action.SELL, "E5",
            f"price {p.price:.2f} at {p.price / high:.2f}x fair value "
            f"{high:.2f}, not re-underwritten",
        )
    if p.price >= high:
        return Verdict(
            p.ticker, Action.SELL_HALF, "E5",
            f"price {p.price:.2f} reached fair value {high:.2f}",
        )
    return None


def _e6(p: PositionState) -> Verdict | None:
    if p.sleeve is not Sleeve.CORE:
        return None
    if p.weight > TRIM_TRIGGER_WEIGHT:
        return Verdict(
            p.ticker, Action.TRIM, "E6",
            f"weight {p.weight:.1%} above {TRIM_TRIGGER_WEIGHT:.0%} "
            f"from appreciation",
            target_weight=TRIM_TARGET_WEIGHT,
        )
    return None


def _e7(p: PositionState) -> Verdict | None:
    if p.sleeve is not Sleeve.CORE:
        return None
    n = p.quarterly_reviews_without_progress
    if n is not None and n >= TIME_STOP_QUARTERS:
        return Verdict(
            p.ticker, Action.SELL, "E7",
            f"{n} quarterly reviews with no progress toward the thesis — "
            "the time stop",
        )
    return None


def _e8(p: PositionState, *, rebalancing: bool) -> Verdict | None:
    if not rebalancing or p.sleeve is not Sleeve.STATISTICAL:
        return None
    if not isinstance(p.exit_block, StatisticalExit):
        return None
    if p.still_qualifies is False:
        return Verdict(
            p.ticker, Action.SELL, "E8",
            "no longer clears the screen or a universe rule",
        )
    if p.composite_rank is not None and p.composite_rank > p.exit_block.rank_buffer:
        return Verdict(
            p.ticker, Action.SELL, "E8",
            f"rank {p.composite_rank} outside the "
            f"{p.exit_block.rank_buffer} buffer",
        )
    return None


def _e9(p: PositionState) -> Verdict | None:
    if p.limit_breach:
        return Verdict(
            p.ticker, Action.TRIM, "E9",
            f"Part-4 breach: {p.limit_breach}",
            target_weight=TRIM_TARGET_WEIGHT,
        )
    return None


def evaluate_position(
    position: PositionState, as_of: date, *, rebalancing: bool = False
) -> Verdict:
    """Run E2-E9 in order and return the first match.

    E1 is not here: it governs whether new positions may be *opened*,
    not whether an existing one leaves, and it is answered by
    :func:`entries_blocked`. Its own row says exits below it still run,
    which is what keeps a drawdown from also freezing the book's ability
    to get out of what caused it.
    """
    for verdict in (
        _e2(position),
        _e3(position, as_of),
        _e4(position),
        _e5(position),
        _e6(position),
        _e7(position),
        _e8(position, rebalancing=rebalancing),
        _e9(position),
    ):
        if verdict is not None:
            return verdict
    return Verdict(position.ticker, Action.NONE, "", "no exit rule triggered")


def entries_blocked(drawdown_from_peak: float | None) -> tuple[bool, str]:
    """E1. Whether new positions of any kind may be opened.

    Args:
        drawdown_from_peak: Negative for a loss, e.g. ``-0.18``.
            ``None`` blocks: an unreadable NAV is not a safe one, and
            the same reasoning already governs the regime dial.

    Returns:
        ``(blocked, reason)``.
    """
    from agents.council.limits import CIRCUIT_BREAKER_DRAWDOWN

    if drawdown_from_peak is None:
        return True, "drawdown unreadable — no new entries"
    if drawdown_from_peak <= CIRCUIT_BREAKER_DRAWDOWN:
        return (
            True,
            f"circuit breaker: {drawdown_from_peak:.1%} from peak, past "
            f"{CIRCUIT_BREAKER_DRAWDOWN:.0%} — no buys of any kind, "
            "exits still run",
        )
    return False, ""


def evaluate_book(
    positions: Sequence[PositionState],
    as_of: date,
    *,
    rebalancing: bool = False,
) -> list[Verdict]:
    """Every position that needs action, in evaluation order.

    Positions with nothing triggered are omitted. A quiet book returning
    an empty list is the normal and correct outcome.
    """
    verdicts = [
        v
        for v in (
            evaluate_position(p, as_of, rebalancing=rebalancing) for p in positions
        )
        if v.action is not Action.NONE
    ]
    if verdicts:
        logger.info(
            f"{as_of}: {len(verdicts)} of {len(positions)} positions "
            f"triggered an exit rule "
            f"({', '.join(sorted({v.rule for v in verdicts}))})"
        )
    return verdicts
