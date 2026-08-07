"""Universe Protocol — common interface for all investable universes.

The :class:`BacktestRunner` only needs to know which tickers are
active on a given date. By coding to this Protocol, the runner can
plug into any concrete universe type:

* :class:`HistoricalUniverse` — Wikipedia-sourced S&P 500 with change log
* :class:`FullMarketUniverse` — every SEC active filer above a market cap floor
* :class:`TASEUniverse` — Tel Aviv Stock Exchange constituents
* (anything else with the same one-method shape)

The Protocol is structural — concrete classes do not need to inherit
from it; mypy/pyright accept anything with a matching ``constituents_at``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Universe(Protocol):
    """Anything that can answer "which tickers are tradeable on date X."""

    def constituents_at(self, as_of: date | datetime) -> list[str]:
        """Return the ticker list active on ``as_of``.

        Implementations should aim for survivorship-bias-free
        membership: a ticker that was a member on ``as_of`` and later
        delisted should still appear in the result. An implementation
        that cannot meet this must say so in its own docstring rather
        than inherit the guarantee by silence — a caller reading only
        this protocol would otherwise assume it holds everywhere.

        Where the two shipped implementations stand:

        * :class:`core.backtest.universe.SP500Universe` — meets it. It
          reconstructs membership from the index change log, so removed
          members reappear at the dates they were in the index.
        * :class:`core.backtest.full_market_universe.FullMarketUniverse`
          — does **not**. Its roster is the SEC's current registrant
          list, so issuers that left the market before the prefetch are
          absent at every historical date. See that module's warning.
        """
        ...


__all__ = ["Universe"]
