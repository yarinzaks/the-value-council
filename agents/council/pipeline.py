"""The selection engine, end to end.

``COUNCIL_SELECTION.md`` sections 1 through 6, run in the order the
document specifies and in the order the costs allow.

    universe (1) -> rank the WHOLE universe (3) -> screen gates A-C (2)
      -> Gate D on the survivors (2) -> basket (3, 4) -> sizes (5)

Two orderings are doing real work here.

**The rank comes before the screen** because section 3 computes its
percentiles across the whole section-1 universe rather than across the
passers. Screening first and ranking the remainder would let a quarter
in which eleven names passed score its worst survivor where a rich
quarter scores its best.

**Gate D comes last** because it is the only gate that costs a network
round trip per company. Gates A to C are arithmetic over facts already
in memory; running them first turns Gate D's bill from thousands of
requests into tens. Section 2 lists the gates as equals, and they are
equals in authority — but not in price.

What this module does not do
----------------------------

It does not execute. It answers with target weights and a written
account of how it got there, and the runner does the rest on the same
rails as the other eleven agents.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from agents.council.assemble import Assembled
from agents.council.exits import DEFAULT_RANK_BUFFER, Sleeve
from agents.council.filings import OpinionIndex, gate_d_flags
from agents.council.rank import Ranked, rank_universe, select_basket
from agents.council.screen import FilingFlags, ScreenResult, screen
from agents.council.sizing import (
    RegimeCeilings,
    ceilings_for,
    sleeve_headroom,
    statistical_entry_weight,
)
from agents.council.universe import UniverseReport, build_universe
from core.logger import get_logger

logger = get_logger("agents.council.pipeline")


@dataclass
class Selection:
    """What the engine decided, and enough of why to audit it later."""

    as_of: date
    weights: dict[str, float] = field(default_factory=dict)
    universe: UniverseReport | None = None
    ranked: list[Ranked] = field(default_factory=list)
    #: Names that cleared gates A-C, before Gate D was paid for.
    provisional: list[str] = field(default_factory=list)
    screened: dict[str, ScreenResult] = field(default_factory=dict)
    flags: dict[str, FilingFlags] = field(default_factory=dict)
    basket: list[Ranked] = field(default_factory=list)
    ceilings: RegimeCeilings | None = None
    note: str = ""

    def summary(self) -> str:
        u = len(self.universe.tradeable) if self.universe else 0
        m = len(self.universe.mechanical) if self.universe else 0
        return (
            f"{self.as_of}: {u} tradeable, {m} mechanical, "
            f"{len(self.provisional)} cleared A-C, "
            f"{len(self.basket)} in the basket"
            + (f" — {self.note}" if self.note else "")
        )


def run_selection(
    rows: Sequence[Assembled],
    as_of: date,
    *,
    risk_on_dials: int | None,
    entries_blocked: bool = False,
    held: Sequence[str] = (),
    core_weight: float = 0.0,
    opinions: OpinionIndex | None = None,
    gate_d: object = gate_d_flags,
    basket_size: int | None = None,
) -> Selection:
    """Run sections 1-5 and return the statistical sleeve's targets.

    Args:
        rows: Every company in the roster, already assembled.
        as_of: The decision date.
        risk_on_dials: How many of the four FRED signals read risk-on.
            ``None`` takes section 9.1's tightest row.
        entries_blocked: E1. When the circuit breaker is active nothing
            new is opened, but what is held is still re-screened, so the
            exits the caller runs afterwards still see a current view.
        held: What the sleeve holds now. Kept out of the entry count and
            re-examined rather than assumed good.
        core_weight: What the core sleeve holds, which crowds the
            statistical ceiling down toward its 20% floor.
        opinions: Prebuilt audit-phrase index; built inside ``gate_d``
            when omitted.
        gate_d: ``(tickers, as_of, opinions=...) -> {ticker: FilingFlags}``.
            Injected so a test never reaches the network.
        basket_size: Overrides section 4's twenty names.

    Returns:
        A :class:`Selection` whose ``weights`` are the sleeve's targets.
        An empty ``weights`` is a legitimate answer: the doctrine is
        explicit that holding cash when nothing is cheap enough is the
        system working, not failing.
    """
    selection = Selection(as_of=as_of)
    ceilings = ceilings_for(risk_on_dials)
    selection.ceilings = ceilings

    # Section 1.
    report = build_universe([r.universe for r in rows], as_of)
    selection.universe = report
    mechanical = set(report.mechanical)

    # Section 3, over the whole universe -- see the module docstring.
    selection.ranked = rank_universe([r.rank for r in rows])

    # Section 2, gates A-C, on the mechanically eligible names only.
    by_ticker = {r.ticker: r for r in rows}
    provisional: list[str] = []
    for ticker in sorted(mechanical):
        row = by_ticker[ticker]
        # Gate D's inputs are unknown at this point, which fails the
        # gate; the A-C verdicts are what this pass is for.
        result = screen(row.financials, FilingFlags(ticker=ticker), as_of=as_of)
        selection.screened[ticker] = result
        if all(g.ok for g in result.gates if g.gate in ("A", "B", "C")):
            provisional.append(ticker)
    selection.provisional = provisional

    # Section 2, Gate D, paid for only on the survivors.
    candidates: list[str] = []
    if provisional:
        flags = gate_d(provisional, as_of, opinions=opinions)  # type: ignore[operator]
        selection.flags = flags
        for ticker in provisional:
            row = by_ticker[ticker]
            result = screen(
                row.financials,
                flags.get(ticker, FilingFlags(ticker=ticker)),
                as_of=as_of,
            )
            selection.screened[ticker] = result
            if result.passed:
                candidates.append(ticker)

    # Sections 3 and 4: the basket, subject to the knife guard and the
    # sector cap. Held names stay eligible so an existing position is
    # not sold merely because the entry gate is shut today.
    eligible = sorted(set(candidates) | {h.upper() for h in held})
    if entries_blocked:
        eligible = sorted({h.upper() for h in held})
        selection.note = "circuit breaker active — no new entries"
    elif not ceilings.mechanical_entries_allowed:
        eligible = sorted({h.upper() for h in held})
        selection.note = (
            f"{risk_on_dials}/4 dials risk-on — no mechanical entries"
        )

    from agents.council.sizing import STATISTICAL_NAMES

    size = basket_size if basket_size is not None else STATISTICAL_NAMES
    basket = select_basket(selection.ranked, eligible=eligible, size=size)

    # E8's buffer: bought into the top 20, held until rank 40. Without
    # this a name that drifts to rank 21 is sold and re-bought when it
    # drifts back, which is pure cost -- the buffer exists precisely to
    # stop names oscillating around the boundary from generating
    # turnover. select_basket answers "what would I buy today", so the
    # holds have to be added back here rather than inside it.
    if held:
        kept = {b.ticker for b in basket}
        held_upper = {h.upper() for h in held}
        by_rank = {r.ticker: i + 1 for i, r in enumerate(selection.ranked)}
        for entry in selection.ranked:
            if entry.ticker not in held_upper or entry.ticker in kept:
                continue
            if by_rank.get(entry.ticker, 10**9) <= DEFAULT_RANK_BUFFER:
                basket.append(entry)
                kept.add(entry.ticker)

    selection.basket = basket

    # Section 5.
    headroom = sleeve_headroom(
        sleeve=Sleeve.STATISTICAL,
        held_weight=0.0,
        ceilings=ceilings,
        core_weight=core_weight,
    )
    each = statistical_entry_weight(
        sleeve_weight=headroom,
        names=size,
        entry_scale=ceilings.entry_scale if not entries_blocked else 1.0,
    )
    selection.weights = {b.ticker: each for b in basket}

    logger.info(selection.summary())
    return selection
