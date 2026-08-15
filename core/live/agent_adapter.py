"""Per-agent adapters that turn a backtest ``Strategy`` into a live runner.

The backtest interface (``Strategy.select`` returning weight dicts) is
necessary but not sufficient for live trading — for the dashboard we
also need:

* the score objects behind each target pick, so we can format
  bilingual ``why`` strings on every Position;
* a watchlist (positions 21-50 or 31-100 by rank) with their own whys.

Each adapter knows how to obtain those from its strategy's
``selection_history`` and full ranking. Construction is cheap; the
heavy work happens in :meth:`run_scan`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
    Strategy,
)
from core.live.why_translator import (
    buffett_why,
    dreman_watch_why,
    dreman_why,
    fisher_why,
    graham_defensive_why,
    graham_net_net_why,
    greenblatt_watch_why,
    greenblatt_why,
    klarman_why,
    lynch_why,
    marks_why,
    neff_why,
    schloss_watch_why,
    schloss_why,
)
from core.logger import get_logger

logger = get_logger("core.live.agent_adapter")

# Default watchlist size — names ranked just below the buy threshold.
DEFAULT_WATCHLIST_SIZE: int = 30


@dataclass(frozen=True)
class LiveTarget:
    """A target buy candidate for live trading."""

    ticker: str
    weight: float  # 0..1
    rank: int  # 1 = highest conviction
    why_en: str
    why_he: str
    score: Any  # the underlying typed score (MagicFormulaScore, etc.)


@dataclass(frozen=True)
class LiveWatch:
    ticker: str
    rank: int
    entry_trigger: str
    why_en: str
    why_he: str
    entry_price_target: float | None = None


@dataclass
class ScanResult:
    targets: list[LiveTarget]
    watchlist: list[LiveWatch]
    universe_size: int


class AgentAdapter:
    """Base — subclasses define how to enumerate targets + watchlist.

    Subclasses must populate the ``ranker`` callable (raw scoring of
    the qualifying universe) so we can derive the watchlist from the
    bottom of the ranked list. They share the same :meth:`run_scan`
    flow:

    1. Strategy ``select()`` produces the BUY weights.
    2. The full ranked list (from ranker) gives positions
       ``portfolio_size+1..portfolio_size+watch_size`` for the watchlist.
    """

    name: str  # set by subclass — must equal Strategy.name

    def __init__(self, strategy: Strategy, *, watchlist_size: int = DEFAULT_WATCHLIST_SIZE):
        self.strategy = strategy
        self.watchlist_size = watchlist_size
        self.entry_trigger: str = "rank enters top portfolio"

    def run_scan(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
        *,
        held: Mapping[str, HeldPosition] | None = None,
    ) -> ScanResult:
        weights = self.strategy.select(
            as_of, universe, prices, fundamentals, held=held
        )
        # Derive ordered targets from the strategy's last selection_history.
        targets = self._collect_targets(weights)
        watchlist = self._collect_watchlist(targets)
        return ScanResult(
            targets=targets, watchlist=watchlist, universe_size=len(universe)
        )

    # -- subclass hooks -------------------------------------------------
    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        raise NotImplementedError

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        raise NotImplementedError


# --------------------------------------------------------------------------
# Greenblatt
# --------------------------------------------------------------------------
class GreenblattLive(AgentAdapter):
    name = "greenblatt_magic_formula"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = "Magic Formula combined rank enters top 30"

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        sel = self.strategy.selection_history[-1]
        # Use top_scores order — already sorted by combined rank.
        ordered = [s for s in sel.top_scores if s.ticker in weights]
        out: list[LiveTarget] = []
        for i, s in enumerate(ordered, start=1):
            en, he = greenblatt_why(s)
            out.append(
                LiveTarget(
                    ticker=s.ticker,
                    weight=weights[s.ticker],
                    rank=i,
                    why_en=en,
                    why_he=he,
                    score=s,
                )
            )
        return out

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        sel = self.strategy.selection_history[-1]
        all_scores = getattr(sel, "all_scores", None)
        if all_scores is None:
            return []
        held = {t.ticker for t in targets}
        watch: list[LiveWatch] = []
        for s in all_scores:
            if s.ticker in held:
                continue
            en, he = greenblatt_watch_why(s)
            watch.append(
                LiveWatch(
                    ticker=s.ticker,
                    rank=s.combined_rank,
                    entry_trigger=self.entry_trigger,
                    why_en=en,
                    why_he=he,
                )
            )
            if len(watch) >= self.watchlist_size:
                break
        return watch


# --------------------------------------------------------------------------
# Schloss
# --------------------------------------------------------------------------
class SchlossLive(AgentAdapter):
    name = "walter_schloss"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = "P/B drops into Schloss bargain range"

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        sel = self.strategy.selection_history[-1]
        ordered = [s for s in sel.top_scores if s.ticker in weights]
        out: list[LiveTarget] = []
        for i, s in enumerate(ordered, start=1):
            en, he = schloss_why(s)
            out.append(
                LiveTarget(
                    ticker=s.ticker,
                    weight=weights[s.ticker],
                    rank=i,
                    why_en=en,
                    why_he=he,
                    score=s,
                )
            )
        return out

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        sel = self.strategy.selection_history[-1]
        all_scores = getattr(sel, "all_scores", None)
        if all_scores is None:
            return []
        held = {t.ticker for t in targets}
        watch: list[LiveWatch] = []
        for rank_idx, s in enumerate(all_scores, start=1):
            if s.ticker in held:
                continue
            en, he = schloss_watch_why(s)
            watch.append(
                LiveWatch(
                    ticker=s.ticker,
                    rank=rank_idx,
                    entry_trigger=self.entry_trigger,
                    why_en=en,
                    why_he=he,
                )
            )
            if len(watch) >= self.watchlist_size:
                break
        return watch


# --------------------------------------------------------------------------
# Graham — handles dual mode (Net-Net + Defensive)
# --------------------------------------------------------------------------
class GrahamLive(AgentAdapter):
    name = "benjamin_graham"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = "P/NCAV enters Graham buy zone OR Defensive thresholds met"

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        sel = self.strategy.selection_history[-1]
        out: list[LiveTarget] = []
        rank = 0
        # Net-Net first (higher conviction)
        for s in sel.top_scores:
            if s.ticker in weights:
                rank += 1
                en, he = graham_net_net_why(s)
                out.append(
                    LiveTarget(
                        ticker=s.ticker,
                        weight=weights[s.ticker],
                        rank=rank,
                        why_en=en,
                        why_he=he,
                        score=s,
                    )
                )
        for d in sel.top_defensive:
            if d.ticker in weights:
                rank += 1
                en, he = graham_defensive_why(d)
                out.append(
                    LiveTarget(
                        ticker=d.ticker,
                        weight=weights[d.ticker],
                        rank=rank,
                        why_en=en,
                        why_he=he,
                        score=d,
                    )
                )
        return out

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        # No external all-scores list available; we'd need to expose
        # one from the strategy. For now, return an empty watchlist
        # and rely on the next rebalance to surface candidates.
        return []


# --------------------------------------------------------------------------
# Dreman
# --------------------------------------------------------------------------
class DremanLive(AgentAdapter):
    name = "david_dreman"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = "joins bottom-quintile cohort on 2+ metrics"

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        sel = self.strategy.selection_history[-1]
        ordered = [s for s in sel.top_scores if s.ticker in weights]
        out: list[LiveTarget] = []
        for i, s in enumerate(ordered, start=1):
            en, he = dreman_why(s)
            out.append(
                LiveTarget(
                    ticker=s.ticker,
                    weight=weights[s.ticker],
                    rank=i,
                    why_en=en,
                    why_he=he,
                    score=s,
                )
            )
        return out

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        sel = self.strategy.selection_history[-1]
        all_scores = getattr(sel, "all_scores", None)
        if all_scores is None:
            return []
        held = {t.ticker for t in targets}
        watch: list[LiveWatch] = []
        for rank_idx, s in enumerate(all_scores, start=1):
            if s.ticker in held:
                continue
            en, he = dreman_watch_why(s)
            watch.append(
                LiveWatch(
                    ticker=s.ticker,
                    rank=rank_idx,
                    entry_trigger=self.entry_trigger,
                    why_en=en,
                    why_he=he,
                )
            )
            if len(watch) >= self.watchlist_size:
                break
        return watch


# --------------------------------------------------------------------------
# Hybrid agents (Neff, Buffett, Lynch, Marks, Klarman, Fisher)
#
# All six follow the same shape — pull ordered ``top_scores`` from the
# strategy's last ``selection_history`` entry, format bilingual whys via
# the per-agent formatters in :mod:`why_translator`. Watchlist is empty
# by default; the strategies don't currently surface their full ranking
# beyond the buy list. (Same pattern as ``GrahamLive`` — adding watchlists
# is a follow-up that requires an ``all_scores`` field on each Selection.)
# --------------------------------------------------------------------------
def _hybrid_collect_targets(
    strategy: Strategy,
    weights: dict[str, float],
    why: Callable[[Any], tuple[str, str]],
) -> list[LiveTarget]:
    """Shared ``_collect_targets`` body for the hybrid adapters."""
    sel = strategy.selection_history[-1]
    ordered = [s for s in sel.top_scores if s.ticker in weights]
    out: list[LiveTarget] = []
    for i, s in enumerate(ordered, start=1):
        en, he = why(s)
        out.append(
            LiveTarget(
                ticker=s.ticker,
                weight=weights[s.ticker],
                rank=i,
                why_en=en,
                why_he=he,
                score=s,
            )
        )
    return out


class NeffLive(AgentAdapter):
    name = "john_neff"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = "Total-Return/PE rank enters Neff buy list"

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        return _hybrid_collect_targets(self.strategy, weights, neff_why)

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        return []  # see module note on watchlists for hybrid agents


class BuffettLive(AgentAdapter):
    name = "warren_buffett"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = "Margin of safety widens to 15%+ at Owner-Earnings DCF"

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        return _hybrid_collect_targets(self.strategy, weights, buffett_why)

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        return []


class LynchLive(AgentAdapter):
    name = "peter_lynch"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = "PEG drops into category buy zone"

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        return _hybrid_collect_targets(self.strategy, weights, lynch_why)

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        return []


class MarksLive(AgentAdapter):
    name = "howard_marks"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = (
            "Cycle-adjusted score crosses posture-specific threshold"
        )

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        return _hybrid_collect_targets(self.strategy, weights, marks_why)

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        return []


class KlarmanLive(AgentAdapter):
    name = "seth_klarman"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = "Conservative DCF margin of safety reaches 30%+"

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        return _hybrid_collect_targets(self.strategy, weights, klarman_why)

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        return []


class FisherLive(AgentAdapter):
    name = "philip_fisher"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = (
            "5-point quant score reaches Tier A (5/5) or Tier B (4/5)"
        )

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        return _hybrid_collect_targets(self.strategy, weights, fisher_why)

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        return []


# --------------------------------------------------------------------------
# Market Core
# --------------------------------------------------------------------------
class MarketCoreLive(AgentAdapter):
    """The eleventh seat: largest liquid companies, cap-weighted.

    Simpler than every adapter above it, because the strategy it wraps
    has no score object to translate — the reason a name is held *is*
    its market capitalisation, and the strategy already publishes that
    with the weight it produced.

    The watchlist is the next thirty companies by size. For a screen
    that ranks on one thing, "what would be bought next" is not a
    judgement call.
    """

    name = "market_core"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = "market capitalisation enters the largest 25"

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        picks = getattr(self.strategy, "last_picks", [])
        out: list[LiveTarget] = []
        for rank, pick in enumerate(picks, start=1):
            weight = weights.get(pick.ticker)
            if weight is None:
                continue
            out.append(
                LiveTarget(
                    ticker=pick.ticker,
                    weight=weight,
                    rank=rank,
                    why_en=pick.why_en,
                    why_he=pick.why_he,
                    score=pick,
                )
            )
        return out

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        held = {t.ticker for t in targets}
        ranking = getattr(self.strategy, "last_ranking", [])
        out: list[LiveWatch] = []
        for rank, pick in enumerate(ranking, start=1):
            if pick.ticker in held:
                continue
            out.append(
                LiveWatch(
                    ticker=pick.ticker,
                    rank=rank,
                    entry_trigger=self.entry_trigger,
                    why_en=(
                        f"market capitalisation ${pick.market_cap / 1e9:,.0f}bn — "
                        f"ranked {rank}, just below the book"
                    ),
                    why_he=(
                        f"שווי שוק {pick.market_cap / 1e9:,.0f} מיליארד$ — "
                        f"מקום {rank}, מעט מתחת לתיק"
                    ),
                )
            )
            if len(out) >= self.watchlist_size:
                break
        return out



# --------------------------------------------------------------------------
# The Council
# --------------------------------------------------------------------------
class PabraiLive(AgentAdapter):
    """Mohnish Pabrai on the same rails as the eleven.

    Every other adapter reads a ``selection_history`` of typed scores —
    a Magic Formula rank, a P/NCAV, a discount to book. This one reads
    the :class:`~agents.council.pipeline.Selection` the engine produced,
    because what it has to explain is not a single number but which
    structural floor let a name in: net cash against market cap, a price
    below tangible book, or an earnings yield on the whole firm. That
    sentence is Gate A's own verdict, quoted verbatim.

    The watchlist is the names that cleared gates A to C and then failed
    Gate D, the knife guard or the sector cap. That is a truer watchlist
    than the other agents' — theirs is the tail of a ranked list, names
    that nearly qualified; these fully qualified on the numbers and were
    stopped by something nameable.
    """

    name = "mohnish_pabrai"

    def __init__(self, strategy: Strategy, **kw: Any) -> None:
        super().__init__(strategy, **kw)
        self.entry_trigger = (
            "clears all four gates and enters the top of the composite rank"
        )

    def _collect_targets(self, weights: dict[str, float]) -> list[LiveTarget]:
        selection = getattr(self.strategy, "last_selection", None)
        composites = (
            {b.ticker: b for b in selection.basket} if selection else {}
        )
        screened = selection.screened if selection else {}
        out: list[LiveTarget] = []
        for rank, ticker in enumerate(
            sorted(
                weights,
                key=lambda t: (
                    -(composites[t].composite if t in composites else 0.0),
                    t,
                ),
            ),
            start=1,
        ):
            result = screened.get(ticker)
            floor = (result.floor if result else None) or "held from a prior rebalance"
            entry = composites.get(ticker)
            score = f"{entry.composite:.3f}" if entry else ""
            out.append(
                LiveTarget(
                    ticker=ticker,
                    weight=weights[ticker],
                    rank=rank,
                    why_en=(
                        f"Structural floor: {floor}."
                        + (f" Composite {score}." if score else "")
                    ),
                    why_he=(
                        f"רצפה מבנית: {floor}."
                        + (f" ציון משוקלל {score}." if score else "")
                    ),
                    score=entry.composite if entry else None,
                )
            )
        return out

    def _collect_watchlist(self, targets: list[LiveTarget]) -> list[LiveWatch]:
        selection = getattr(self.strategy, "last_selection", None)
        if selection is None:
            return []
        opened = {t.ticker for t in targets}
        out: list[LiveWatch] = []
        for rank, ticker in enumerate(
            (t for t in selection.provisional if t not in opened), start=1
        ):
            if rank > self.watchlist_size:
                break
            result = selection.screened.get(ticker)
            failures = result.failures if result else []
            if failures:
                reason = f"{failures[0].gate}: {failures[0].detail}"
                en = f"Cleared the numbers, stopped by Gate {reason}"
                he = f"עברה את המספרים, נעצרה בשער {reason}"
            else:
                en = "Cleared every gate; waiting on the rank or the sector cap."
                he = "עברה את כל השערים; ממתינה לדירוג או לתקרת הסקטור."
            out.append(
                LiveWatch(
                    ticker=ticker,
                    rank=rank,
                    entry_trigger=self.entry_trigger,
                    why_en=en,
                    why_he=he,
                )
            )
        return out


__all__ = [
    "AgentAdapter",
    "BuffettLive",
    "DremanLive",
    "FisherLive",
    "GrahamLive",
    "GreenblattLive",
    "KlarmanLive",
    "LiveTarget",
    "LiveWatch",
    "LynchLive",
    "MarketCoreLive",
    "MarksLive",
    "NeffLive",
    "PabraiLive",
    "ScanResult",
    "SchlossLive",
]
