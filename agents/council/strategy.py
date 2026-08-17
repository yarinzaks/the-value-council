"""Mohnish Pabrai as a Strategy, on the same rails as the other eleven.

``COUNCIL_SELECTION.md`` is the doctrine; :mod:`agents.council.pipeline`
runs it; this hands the result to the runner in the shape every other
agent uses. Going through :class:`Strategy` rather than a bespoke path
is the whole point — same execution, same cost model, same marks, same
snapshots, same rebalancing band as the eleven. An agent with its own
execution path would be incomparable to them no matter what the
dashboard showed.

What it replaced
----------------

Until now this file bought whatever three of the other eleven agents
already held. That rule made the twelfth agent a linear combination of
the other eleven — it could not outperform them by construction — and
it is the one trade section 8 of the doctrine explicitly assigns
elsewhere: *"a proposed trade that cannot be tied to one of its three
edges — forced selling, complexity, time — belongs to one of the other
eleven, not to this one."* The books-off-disk reader went with it.

The statistical sleeve only, for now
------------------------------------

What runs here is section 1 to 5's mechanical path: universe, screen,
rank, basket, equal weight. The Core sleeve is the Council's own, and it
answers to a written thesis with kill criteria rather than to a screen,
so it opens through the journal rather than through this method. The
Event sleeve is deferred — ATR, Form 10 detection and index-deletion
announcements are not built, and it is 0-15% of the book.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from agents.council.assemble import assemble_universe
from agents.council.exits import (
    PositionState,
    Sleeve,
    StatisticalExit,
    entries_blocked,
    evaluate_book,
)
from agents.council.pipeline import Selection, run_selection
from agents.council.review import quarters_without_progress
from core.backtest.strategy_runner import HeldPosition, Strategy
from core.logger import get_logger
from core.paths import council_reviews_dir

logger = get_logger("agents.council.strategy")

AGENT_SLUG = "mohnish_pabrai"

#: Entries already spent, derived from the journal rather than stored.
PUNCH_CARD_TOTAL = 20


class MohnishPabrai(Strategy):
    """Buys from forced sellers, below a floor it can verify."""

    name = AGENT_SLUG

    def __init__(
        self,
        *,
        edgar_cache: Any = None,
        price_loader: Any = None,
        regime_reader: Any = None,
        opinion_index: Any = None,
        drawdown_reader: Any = None,
        news_service: Any | None = None,
        reviews_dir: Any = None,
    ) -> None:
        """
        Args:
            edgar_cache: Where the filings are. Constructed on first use
                when omitted.
            price_loader: Supplies the 63-session dollar volume and the
                12-1 momentum window, neither of which the runner's
                per-day price lookup can answer.
            regime_reader: ``(as_of) -> Regime``. Injected so tests and
                offline runs need no FRED call. ``None`` leaves the dial
                unread, which takes section 9.1's tightest row.
            opinion_index: Prebuilt Gate D phrase index. Built inside
                the pipeline when omitted.
            drawdown_reader: ``() -> float | None``, the drawdown from
                peak NAV. Feeds E1. ``None`` reads as unreadable, which
                blocks entries — an unreadable NAV is not a safe one.
            news_service: Accepted and unused. The doctrine's section 10
                rules sentiment and news-flow scores out explicitly, and
                the 8-K stream is the event feed; the parameter stays so
                the runner's wiring does not have to special-case this
                agent.
        """
        self.edgar_cache = edgar_cache
        self.price_loader = price_loader
        self.regime_reader = regime_reader
        self.opinion_index = opinion_index
        self.drawdown_reader = drawdown_reader
        self.news_service = news_service
        # Where the REVIEW records live. Injectable so a test can point
        # it at a tmp_path rather than the real data root.
        self._reviews_dir = reviews_dir or council_reviews_dir()
        self.last_selection: Selection | None = None
        #: Tickers E2 wants out regardless of the runner's holding
        #: floor. Read by :class:`PabraiLive` into the scan result.
        self.last_forced_exits: list[str] = []
        #: Why each of them, for the run log and the decision record.
        self.last_exit_reasons: dict[str, str] = {}

    # ------------------------------------------------------------------
    def _cache(self) -> Any:
        if self.edgar_cache is None:
            from core.data.edgar_cache import EdgarCache

            self.edgar_cache = EdgarCache()
        return self.edgar_cache

    def _loader(self) -> Any:
        if self.price_loader is None:
            from core.backtest.data_loader import PriceDataLoader

            self.price_loader = PriceDataLoader()
        return self.price_loader

    def _risk_on_dials(self, as_of: date) -> int | None:
        if self.regime_reader is None:
            return None
        try:
            return int(self.regime_reader(as_of).risk_on_count)
        except Exception as exc:
            # Left as None, which takes the tightest ceilings. An
            # unreadable regime is not a green one.
            logger.warning(f"regime unreadable — {type(exc).__name__}: {exc}")
            return None

    def _drawdown(self) -> float | None:
        if self.drawdown_reader is None:
            return None
        try:
            value = self.drawdown_reader()
        except Exception as exc:
            logger.warning(f"drawdown unreadable — {type(exc).__name__}: {exc}")
            return None
        return None if value is None else float(value)

    def _price_inputs(
        self, tickers: Sequence[str], as_of: date, prices: Any
    ) -> tuple[dict[str, float | None], dict[str, float], dict[str, float | None]]:
        """Last close, 63-session dollar volume, and 12-1 momentum.

        The close comes from the runner's own lookup, which has already
        fetched it once for every agent. The other two need the loader's
        history and are asked for here.
        """
        loader = self._loader()
        last: dict[str, float | None] = {}
        for t in tickers:
            try:
                last[t] = prices.get(t)
            except Exception:
                last[t] = None

        try:
            volumes = loader.median_dollar_volume(tickers, as_of, sessions=63)
        except Exception as exc:
            logger.warning(f"dollar volume unavailable — {exc}")
            volumes = {}

        momentum: dict[str, float | None] = {}
        for t in tickers:
            try:
                momentum[t] = loader.trailing_return(
                    t, as_of, lookback_months=12, skip_months=1
                )
            except Exception:
                momentum[t] = None
        return last, volumes, momentum

    def _evaluate_exits(
        self,
        holding: Sequence[str],
        rows: Sequence[Any],
        as_of: date,
        *,
        held: Mapping[str, HeldPosition] | None,
    ) -> None:
        """Run E2 over what is held and record what must go.

        Only E2 fires here — a terminal filing, or filings gone past the
        400-day staleness bound. The rest of the table is either the
        Core sleeve's (E4-E7), the Event sleeve's (E3), or already
        expressed by the basket itself: E8's rank buffer decides
        membership in :mod:`agents.council.pipeline`, and a name that
        leaves the target list is sold by the runner in the ordinary way.

        The filings check costs one request per held name, which is
        twenty at most. That is the same reasoning that puts Gate D last
        in the screen: an expensive check is affordable exactly when it
        runs on a short list.
        """
        self.last_forced_exits = []
        self.last_exit_reasons = {}
        if not holding:
            return

        by_ticker = {r.ticker: r for r in rows}
        terminal: dict[str, str] = {}
        try:
            from agents.council.events import Severity, scan

            for event in scan(holding, since=as_of - timedelta(days=10)):
                if event.severity is Severity.CRITICAL:
                    terminal.setdefault(
                        event.ticker, f"{event.form} {event.code} — {event.meaning}"
                    )
        except Exception as exc:
            # A filings outage must not invent an exit, and must not
            # silently read as "nothing happened" either -- it is said
            # out loud so a quiet run is distinguishable from a blind one.
            logger.warning(f"{as_of}: filings unread — {type(exc).__name__}: {exc}")

        states: list[PositionState] = []
        for ticker in holding:
            row = by_ticker.get(ticker)
            latest = row.universe.latest_filing if row is not None else None
            position = (held or {}).get(ticker)
            states.append(
                PositionState(
                    ticker=ticker,
                    sleeve=Sleeve.STATISTICAL,
                    opened=(
                        position.entry_date if position is not None else as_of
                    ),
                    weight=0.0,
                    exit_block=StatisticalExit(),
                    # E7's time stop, now countable. Supplying None
                    # here was why the rule could never fire: the
                    # REVIEW run wrote no record to count.
                    quarterly_reviews_without_progress=quarters_without_progress(
                        ticker, directory=self._reviews_dir
                    ),
                    terminal_filing=terminal.get(ticker),
                    filing_age_days=(
                        None if latest is None else (as_of - latest).days
                    ),
                )
            )

        for verdict in evaluate_book(states, as_of):
            if verdict.sells:
                self.last_forced_exits.append(verdict.ticker)
                self.last_exit_reasons[verdict.ticker] = (
                    f"{verdict.rule}: {verdict.reason}"
                )
                logger.info(
                    f"{as_of}: forcing {verdict.ticker} out — {verdict.reason}"
                )

    # ------------------------------------------------------------------
    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: Any,
        fundamentals: Any,
        *,
        held: Mapping[str, HeldPosition] | None = None,
    ) -> dict[str, float]:
        """Target weights for the statistical sleeve.

        ``fundamentals`` is ignored: the runner supplies one vendor-shaped
        scalar per ticker, and every ratio in sections 2 and 3 is
        trailing-twelve-month and point-in-time. Those are assembled from
        the filings themselves rather than from a snapshot whose
        as-of date nobody recorded.
        """
        holding = sorted(held or {})
        blocked, reason = entries_blocked(self._drawdown())
        if blocked:
            logger.info(f"{as_of}: {reason}")

        last, volumes, momentum = self._price_inputs(universe, as_of, prices)
        rows = assemble_universe(
            universe,
            as_of,
            cache=self._cache(),
            prices=last,
            dollar_volumes=volumes,
            momentum=momentum,
        )

        selection = run_selection(
            rows,
            as_of,
            risk_on_dials=self._risk_on_dials(as_of),
            entries_blocked=blocked,
            held=holding,
            opinions=self.opinion_index,
        )
        self.last_selection = selection
        self._evaluate_exits(holding, rows, as_of, held=held)

        # The universe is the runner's answer on what is tradeable
        # today, and it is applied again here even though section 1
        # already consumed it: a name every gate passed yesterday can be
        # suspended this morning.
        tradeable = {t.upper() for t in universe}
        return {
            t: w
            for t, w in selection.weights.items()
            if not tradeable or t.upper() in tradeable
        }
