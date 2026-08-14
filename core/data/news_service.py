"""News and events, delivered to the agents that want them.

Four news-capable sources were configured in this project and none of
them reached a decision. Finnhub, Marketaux and Alpha Vantage are only
constructible through :class:`~core.data.unified_fetcher.UnifiedFetcher`,
which is never instantiated in production code, and the FMP fallback
hook on ``FundamentalsFetcherConfig`` is never passed. The agents have
been deciding on prices and filings alone while three subscriptions sat
idle. This is the path that was missing.

Design
------

*Optional, per agent.* Strategies receive this through their
constructor, not through :meth:`Strategy.select`, whose signature the
eleven existing agents already implement. An agent that wants news asks
for one; an agent that does not is untouched. A doctrine reading
quarterly filings and holding for thirty days has no business reacting
to a headline, and forcing the feed on it would be the wrong default.

*Degrading, never failing.* Every source is optional and each is tried
in turn. A missing key, an expired key, a rate limit or an outage costs
that source and no more — :meth:`news_for` returns what the others
found. It raises only if asked to, via ``strict``. A news feed that can
halt the run that marks the books would be a worse bargain than no news
feed, which is what the last three months have effectively been.

*Cached within a run.* One agent asking about AAPL should not spend
four API calls because another asked a minute earlier. The cache is
per-instance and per-run, deliberately: it must not outlive the day it
describes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from core.data.models import NewsItem
from core.logger import get_logger

logger = get_logger("core.data.news_service")

#: How far back a "recent news" question looks by default. Long enough
#: to survive a quiet weekend, short enough that a run reacts to this
#: week rather than last quarter.
DEFAULT_LOOKBACK = timedelta(days=7)

#: Nothing is gained by handing a strategy five hundred headlines about
#: one company; the tail is syndication of the same story.
DEFAULT_LIMIT = 25


@dataclass
class SourceHealth:
    """What happened when a source was asked, for the run log."""

    name: str
    ok: bool
    items: int = 0
    error: str | None = None


@dataclass
class NewsService:
    """Every configured news source, behind one call.

    Construct with :meth:`from_settings`, which builds only the sources
    whose keys are present and reports what it got.
    """

    sources: list[tuple[str, object]] = field(default_factory=list)
    #: Populated by the last :meth:`news_for`, for the caller to log.
    health: list[SourceHealth] = field(default_factory=list)
    _cache: dict[tuple[str, date, int], list[NewsItem]] = field(
        default_factory=dict, repr=False
    )

    @classmethod
    def from_settings(cls) -> NewsService:
        """Build from whichever keys are configured.

        Sources whose key is absent are skipped without complaint — that
        is a configuration choice, not a fault. A key that is present but
        rejected is a fault, and shows up in :attr:`health` on first use
        rather than here, because finding out costs a network call.
        """
        sources: list[tuple[str, object]] = []

        for name, build in (
            ("finnhub", _finnhub),
            ("marketaux", _marketaux),
            ("alpha_vantage", _alpha_vantage),
        ):
            try:
                src = build()
            except Exception as exc:
                logger.debug(f"{name} not configured — {type(exc).__name__}")
                continue
            if src is not None:
                sources.append((name, src))

        logger.info(
            f"news service: {len(sources)} source(s) — "
            f"{', '.join(n for n, _ in sources) or 'none'}"
        )
        return cls(sources=sources)

    @property
    def available(self) -> bool:
        """True when at least one source is configured."""
        return bool(self.sources)

    def news_for(
        self,
        ticker: str,
        as_of: date,
        *,
        lookback: timedelta = DEFAULT_LOOKBACK,
        limit: int = DEFAULT_LIMIT,
        strict: bool = False,
    ) -> list[NewsItem]:
        """Recent articles for ``ticker``, newest first.

        Args:
            ticker: The symbol to ask about.
            as_of: The day the question is asked. Articles published
                after it are dropped — a live run should not see
                tomorrow, and a replayed one must not.
            lookback: How far back to look.
            limit: Most articles to return.
            strict: Raise instead of degrading. For diagnostics; a
                trading run should never set it.

        Returns:
            Deduplicated articles from every source that answered.
            Empty when none did, which is not distinguishable here from
            "nothing was written about this company" — read
            :attr:`health` if the difference matters.
        """
        key = (ticker.upper(), as_of, limit)
        if key in self._cache:
            return self._cache[key]

        since = as_of - lookback
        found: list[NewsItem] = []
        health: list[SourceHealth] = []

        for name, src in self.sources:
            try:
                items = _ask(src, name, ticker, since, as_of, limit)
            except Exception as exc:
                if strict:
                    raise
                msg = f"{type(exc).__name__}: {exc}"[:120]
                logger.warning(f"news source {name} failed for {ticker} — {msg}")
                health.append(SourceHealth(name, ok=False, error=msg))
                continue
            health.append(SourceHealth(name, ok=True, items=len(items)))
            found.extend(items)

        # Published-after-as_of is dropped rather than trusted. The
        # sources are queried with a date window, but they honour it to
        # differing degrees and one of them ignores the upper bound.
        found = [
            n
            for n in found
            if n.published_at is not None and n.published_at.date() <= as_of
        ]

        merged = _dedupe(found)[:limit]
        self.health = health
        self._cache[key] = merged
        return merged

    def has_recent_news(self, ticker: str, as_of: date, **kw) -> bool:
        """Whether anything at all was written. Cheap sugar over the above."""
        return bool(self.news_for(ticker, as_of, **kw))


def _ask(
    src: object, name: str, ticker: str, since: date, as_of: date, limit: int
) -> list[NewsItem]:
    """Call whichever method this source spells its news with."""
    if name == "finnhub":
        return src.get_company_news(  # type: ignore[attr-defined]
            ticker, since.isoformat(), as_of.isoformat(), limit=limit
        )
    if name == "marketaux":
        return src.get_news(ticker, limit=limit)  # type: ignore[attr-defined]
    if name == "alpha_vantage":
        return src.get_news_sentiment(ticker, limit=limit)  # type: ignore[attr-defined]
    raise ValueError(f"no news accessor known for {name}")


def _dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """One article per story, newest first.

    Three sources syndicating one wire story is one event, and an agent
    counting it three times would read a quiet day as a loud one. The url
    is the identity where present; otherwise the headline, lowercased,
    since the same story reaches different feeds with different casing.
    """
    seen: set[str] = set()
    out: list[NewsItem] = []
    for n in sorted(
        items,
        key=lambda x: x.published_at,
        reverse=True,
    ):
        key = (n.url or n.title).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


# Builders are separate so from_settings stays readable and each import
# is paid only when its key exists.


def _finnhub() -> object | None:
    from core.config import get_settings
    from core.data.finnhub_source import FinnhubSource

    return FinnhubSource() if get_settings().finnhub_api_key else None


def _marketaux() -> object | None:
    from core.config import get_settings
    from core.data.marketaux_source import MarketauxSource

    return MarketauxSource() if get_settings().marketaux_api_key else None


def _alpha_vantage() -> object | None:
    from core.config import get_settings
    from core.data.alpha_vantage_source import AlphaVantageSource

    return AlphaVantageSource() if get_settings().alpha_vantage_key else None
