"""Rule 18 — spread the bet across industries, not just across names.

Dreman's own numbering, playbook §6.1:

    18. Invest equally in 20-30 stocks, diversified among 15+ industries

and, stated separately in the same section, **no single industry above
15%** of the book — tighter than Neff's permitted 35%, deliberately.

The reason is in the playbook too: "wide diversification protects
against the inevitable 15-20% of holdings that turn out to be value
traps." The cheapest quintile on P/E, P/B, P/CF and yield is not a
random sample of the market. It is where the damaged businesses are,
and they cluster — an industry does not fall out of favour one company
at a time.

What was actually happening
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Nothing in the strategy had a concept of industry. ``select`` took the
top N by composite rank and equal-weighted them, so the book's
composition was whatever the screen handed back.

The live book at the time of writing: **25 holdings across 11
industries**, against Rule 18's 15. **Insurance carriers alone 28.5%**,
against a 15% cap. **Financials as a whole 49.9%** — half the portfolio
in the one sector the playbook singles out by name:

    "financials reliably trade at low P/E, low P/B, often with high
    dividends — multi-metric contrarian qualification is common. The
    risk: structural crises in highly-levered industries can be more
    severe than historical patterns predict."

That paragraph is the introduction to the playbook's 2008 case study.
The strategy was reproducing the setup the case study exists to warn
about, because the rule that prevents it was never implemented.

How industries are identified
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SIC major group — the first two digits of the SEC's own classification,
which arrives with every filing. It is coarse (63 is all insurance
carriers, life and property alike) and it is what the filer asserts
rather than what an index provider decides, but it is the only industry
label in the data and it separates banks from utilities from chemicals,
which is the distinction Rule 18 is about.

A company whose SIC is unknown is its own singleton group rather than
being pooled with every other unknown. Pooling would invent a fake
industry that the caps then constrain as though it were real.

What this does not fix
~~~~~~~~~~~~~~~~~~~~~~

Rule 18 caps *industries*, and the playbook's 2008 lesson is about a
*sector*. Those are not the same size. Applied to the live book, the
largest industry falls from 32.0% to 12.5% — inside the cap — while
financials in aggregate fall only from 56.0% to 43.8%, because banks
(60), brokers (62), insurers (63) and holding companies (67) are four
separate SIC major groups and each is entitled to its own slots.

That is Rule 18 working exactly as Dreman wrote it, and it is still
most of a book in one sector during a credit crisis. The playbook's own
warning — "structural crises in highly-levered industries can be more
severe than historical patterns predict" — is not answered by an
industry cap alone. A sector-level ceiling is a separate rule that
Dreman does not state and this module does not invent.

The 25-to-16 shrinkage in that comparison is an artifact of applying
the rule retrospectively to an already-concentrated book with no fresh
candidates to backfill from. A live run screens the whole universe and
has far more industries to draw on.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from core.data.sic_codes import sic_for
from core.logger import get_logger

logger = get_logger("agents.dreman.diversification")


#: Rule 18's industry floor.
DEFAULT_MIN_INDUSTRIES: int = 15

#: Playbook §6.1's per-industry ceiling, in percent of the book.
DEFAULT_MAX_INDUSTRY_WEIGHT_PCT: float = 15.0


class _Ranked(Protocol):
    """The one field this module needs from a score row.

    Declared read-only: a mutable ``ticker: str`` member would not match
    a frozen dataclass like :class:`agents.dreman.ranking.DremanScore`,
    because protocol attribute matching is mutability-sensitive.
    """

    @property
    def ticker(self) -> str: ...


@dataclass(frozen=True)
class DiversificationReport:
    """What the constraint did, for the audit trail."""

    industries: int
    max_industry_weight_pct: float
    largest_industry: str | None
    dropped_for_concentration: tuple[str, ...]
    met_industry_floor: bool


def industry_of(ticker: str) -> str:
    """SIC major group as a two-character key.

    Unknown SIC yields a per-ticker singleton, so an unclassified name
    is never grouped with another unclassified name.
    """
    sic = sic_for(ticker)
    if sic is None:
        return f"?{ticker}"
    return str(sic).zfill(4)[:2]


def max_per_industry(portfolio_size: int, min_industries: int) -> int:
    """How many names one industry may hold.

    ``ceil(size / floor)`` is the largest per-industry count that still
    admits the industry floor: at Dreman's own 30 names and 15
    industries it is 2, which forces at least 15 groups. Never below 1,
    or nothing could be selected at all.
    """
    if min_industries <= 0:
        return portfolio_size
    return max(1, math.ceil(portfolio_size / min_industries))


def diversify[RankedT: _Ranked](
    ranked: list[RankedT],
    *,
    portfolio_size: int,
    min_industries: int = DEFAULT_MIN_INDUSTRIES,
    max_industry_weight_pct: float = DEFAULT_MAX_INDUSTRY_WEIGHT_PCT,
) -> tuple[list[RankedT], DiversificationReport]:
    """Pick ``portfolio_size`` names under Rule 18's industry limits.

    ``ranked`` must already be sorted best-first; rank order is
    preserved within whatever the constraints allow.

    Two passes. The first walks the ranking and admits a name only
    while its industry has room. That alone bounds concentration but
    does not guarantee breadth — 25 names at 2 per industry can sit in
    13 groups. The second pass closes the gap: while the book is short
    of the floor and an unrepresented industry has a candidate, the
    lowest-ranked name from the most crowded industry is swapped out
    for it.

    When the universe genuinely cannot supply ``min_industries``, the
    book comes back short and the report says so. That is deliberate.
    The cap cannot manufacture breadth the screen does not contain, and
    Rule 18's protection is structural — "wide diversification protects
    against the inevitable 15-20% of holdings that turn out to be value
    traps." A screen offering three insurers and nothing else is not an
    opportunity to hold three insurers; it is the absence of the
    protection, and the response is less risk rather than more. Two
    slots go to insurance and the rest stay in cash.
    """
    if portfolio_size <= 0:
        raise ValueError(f"portfolio_size must be positive; got {portfolio_size}")
    if not ranked:
        return [], DiversificationReport(0, 0.0, None, (), False)

    cap = max_per_industry(portfolio_size, min_industries)
    groups = {r.ticker: industry_of(r.ticker) for r in ranked}

    chosen: list[RankedT] = []
    counts: Counter[str] = Counter()
    dropped: list[str] = []
    for row in ranked:
        if len(chosen) >= portfolio_size:
            break
        g = groups[row.ticker]
        if counts[g] >= cap:
            dropped.append(row.ticker)
            continue
        chosen.append(row)
        counts[g] += 1

    # Pass two: buy breadth with the weakest names we hold.
    chosen = _fill_missing_industries(
        chosen, ranked, groups, counts, min_industries, dropped
    )

    weight_pct = 100.0 / len(chosen) if chosen else 0.0
    counts = Counter(groups[r.ticker] for r in chosen)
    largest, largest_n = (
        counts.most_common(1)[0] if counts else (None, 0)
    )
    report = DiversificationReport(
        industries=len(counts),
        max_industry_weight_pct=largest_n * weight_pct,
        largest_industry=largest,
        dropped_for_concentration=tuple(dropped),
        met_industry_floor=len(counts) >= min_industries,
    )
    if not report.met_industry_floor:
        logger.info(
            f"only {report.industries} industries available "
            f"(Rule 18 asks for {min_industries}); "
            f"largest is {largest} at {report.max_industry_weight_pct:.1f}%"
        )
    if report.max_industry_weight_pct > max_industry_weight_pct:
        # Reachable only when the book is too small for the cap to be
        # satisfiable — three names cannot be spread under 15% each.
        logger.warning(
            f"industry {largest} at {report.max_industry_weight_pct:.1f}% "
            f"exceeds the {max_industry_weight_pct:.0f}% cap on a "
            f"{len(chosen)}-name book"
        )
    return chosen, report


def _fill_missing_industries[RankedT: _Ranked](
    chosen: list[RankedT],
    ranked: list[RankedT],
    groups: dict[str, str],
    counts: Counter[str],
    min_industries: int,
    dropped: list[str],
) -> list[RankedT]:
    """Swap the weakest crowded name for the best unrepresented one."""
    represented = set(counts)
    if len(represented) >= min_industries:
        return chosen

    held = {r.ticker for r in chosen}
    newcomers = [
        r
        for r in ranked
        if r.ticker not in held and groups[r.ticker] not in represented
    ]
    for candidate in newcomers:
        if len(represented) >= min_industries:
            break
        crowded, n = counts.most_common(1)[0]
        if n <= 1:
            break  # every industry is a singleton; nothing to give up
        for i in range(len(chosen) - 1, -1, -1):
            if groups[chosen[i].ticker] == crowded:
                dropped.append(chosen[i].ticker)
                counts[crowded] -= 1
                chosen[i] = candidate
                g = groups[candidate.ticker]
                counts[g] += 1
                represented.add(g)
                break
    return chosen


__all__ = [
    "DEFAULT_MAX_INDUSTRY_WEIGHT_PCT",
    "DEFAULT_MIN_INDUSTRIES",
    "DiversificationReport",
    "diversify",
    "industry_of",
    "max_per_industry",
]
