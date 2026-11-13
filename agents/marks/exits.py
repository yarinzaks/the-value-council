"""When Marks sells a name he holds — and when he keeps it.

The problem this fixes (audit SF-X1, item 13i)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Playbook §7: Marks "rarely makes binary buy/sell decisions". He trims
25% as the price approaches fair value, another 25% when it reaches it,
and exits fully once it is past fair value with the cycle hot; he sells
at any price when the thesis breaks. §7.3 lists what is not a trigger:
volatility, quarterly earnings noise, generic downturns when the cycle
is mid or early. None of it ran. ``HowardMarks.select`` ignored
``held``, and the engine sold a Marks name only because it left the
day's target list — 44 exits since 2026-08-05, none citing value,
cycle or thesis.

What this module decides
~~~~~~~~~~~~~~~~~~~~~~~~

One :class:`~core.live.doctrine_exits.ExitVerdict` per held name.
Marks's exits run in shadow
(:data:`core.live.doctrine_exits.DOCTRINE_EXIT_MODES`): the runner
journals each verdict beside what the engine did; no weight changes.

**Fair value.** The agent computes no intrinsic value (audit SF-X1:
"no intrinsic value at all"). The one valuation line its screen does
hold is the posture's earnings-yield floor
(:func:`agents.marks.ranking.earnings_yield_floor_pct`): below it the
screen will not rank a name at all. So fair value here is the price at
which the held name's earnings yield falls to that floor — EPS times
``100 / floor`` — on the same EPS the screen reads (diluted, else
basic). The line moves with the cycle by design: a Hot screen demands
6.5% (15.4x), a Cool one 4.5% (22.2x), so the same price is nearer
fair value when the pendulum is high. This is a stand-in the owner has
to accept or replace; it is named in every reason.

**The cycle.** "Hot" for §7.1 trigger 1 and §7.2's last rung, and
"positioned for reversion" for trigger 2, are read as the day's posture
being in :data:`HOT_POSTURES` (Warm or Hot) — the two postures the
temperature module calls defensive.

Checked in this order:

* **Trigger 4 — the thesis breaks**, first because a broken thesis is
  not held for a better price. The entry thesis is a name that passes
  the light quality gates (:mod:`agents.marks.filters`); it is broken
  when the filings say one of them no longer holds:

  - book equity reported zero or negative;
  - full-year net income reported zero or negative (the fetcher reads
    flow concepts over a full-year period, so this is a loss *year*,
    not the quarterly noise §7.3 excludes);
  - D/E above the gate's ``max_de`` (1.0). Unreported debt is unknown,
    not a breach (audit FUND-4).

  Market cap below the gate's floor is not a break: it is a price
  decline, and §7.3 says volatility alone is not an exit.

* **§7.2's ladder.** Price as a % of fair value:

  - at or past :data:`FAIR_VALUE_PCT` (100%) with the cycle Warm or
    Hot: **sell** — "as price exceeds fair value with cycle hot, fully
    exit" (trigger 2 says "reaches", so the line itself counts);
  - at or past 100% with the cycle Neutral or colder: keep, the second
    25% trim rung, named and not executed;
  - from :data:`APPROACH_PCT` (80%) to 100%: keep, the first 25% trim
    rung. The playbook gives "approaches" no number; 80% is Klarman's
    (§7.2 calls the approach "similar to Klarman");
  - below 80%: keep. With the cycle Warm or Hot the reason names
    trigger 1's trim ("even good companies should be trimmed when the
    broader cycle is hot").

A verdict is keep or sell, so neither 25% trim is executed.

Not decided here:

* **The trims** and §7.2's "add position" on a further drop.
* **Trigger 3 — a better risk-adjusted opportunity.** It weighs a held
  name against a candidate; a verdict is about one held name.
* Thesis breaks read from text (catalyst failed, "cycle didn't behave
  as expected"): the LLM memo's territory. Its
  ``scaling_out_plan`` price ladder is not read: memos are written for
  the screen's top candidates only, in live mode only.

When a verdict cannot be reached honestly — no fundamentals, no price,
no positive EPS, or no measured market (an empty universe defaults the
posture to Neutral without reading anything) — and no thesis break
fired on what *is* known, no verdict is given for that name. A keep on
no evidence would, once enforced, hold the name indefinitely; silence
leaves it to the engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from core.backtest.point_in_time import PointInTimeFinancials
from core.backtest.strategy_runner import (
    FundamentalsLookup,
    HeldPosition,
    PriceLookup,
)
from core.live.doctrine_exits import ExitVerdict
from core.logger import get_logger

from .filters import DEFAULT_MAX_DE, debt_to_equity
from .ranking import earnings_yield_floor_pct
from .temperature import Posture

logger = get_logger("agents.marks.exits")

#: §7.2: "as price approaches fair value, trim 25%". The playbook gives
#: no number; Klarman's 80% (§7.2 calls the approach similar to his).
APPROACH_PCT: float = 80.0

#: §7.2: "as price reaches fair value, trim another 25%"; past it with
#: the cycle hot, "fully exit".
FAIR_VALUE_PCT: float = 100.0

#: The postures read as "the cycle is hot" (§7.1 trigger 1, §7.2) and
#: "positioned for reversion" (§7.1 trigger 2).
HOT_POSTURES: frozenset[Posture] = frozenset({"Warm", "Hot"})

_PRECISION = 6


def thesis_break(
    fin: PointInTimeFinancials, *, max_de: float = DEFAULT_MAX_DE
) -> str | None:
    """Why the entry gates no longer hold on the filings, or None."""
    if fin.total_equity is not None and fin.total_equity <= 0:
        return (
            f"thesis broken: book equity ${fin.total_equity:,.0f} is no "
            f"longer positive, which the entry gates require"
        )
    if fin.net_income is not None and fin.net_income <= 0:
        return (
            f"thesis broken: net income ${fin.net_income:,.0f} for the "
            f"latest fiscal year is no longer positive, which the entry "
            f"gates require"
        )
    de = debt_to_equity(fin)
    if de is not None and round(de, _PRECISION) > max_de:
        return (
            f"thesis broken: balance sheet deteriorated, D/E {de:.2f} above "
            f"the {max_de} the entry gates allow"
        )
    return None


def _eps(fin: PointInTimeFinancials) -> float | None:
    """The EPS the screen prices: diluted, else basic; positive only."""
    eps = fin.eps_diluted if fin.eps_diluted is not None else fin.eps_basic
    if eps is None or eps <= 0:
        return None
    return eps


def decide(
    position: HeldPosition,
    fin: PointInTimeFinancials | None,
    price: float | None,
    posture: Posture | None,
    *,
    max_de: float = DEFAULT_MAX_DE,
) -> ExitVerdict | None:
    """Keep or sell one held name, or None when it cannot be judged.

    ``posture`` is the day's measured cycle posture, or None when the
    market could not be measured.
    """
    ticker = position.ticker
    if fin is None or price is None or price <= 0:
        return None

    broken = thesis_break(fin, max_de=max_de)
    if broken is not None:
        return ExitVerdict(ticker, sell=True, reason=broken)

    eps = _eps(fin)
    if eps is None or posture is None:
        logger.info(f"{ticker} — no positive EPS or no measured cycle; no exit verdict")
        return None

    floor = earnings_yield_floor_pct(posture)
    fair_pe = 100.0 / floor
    fair_price = eps * fair_pe
    pe = price / eps
    pct = round(100.0 * price / fair_price, _PRECISION)
    value = (
        f"price ${price:,.2f} is {pct:.1f}% of fair value "
        f"${fair_price:,.2f}/share (P/E {pe:.1f} against the {posture} "
        f"screen's {fair_pe:.1f}x value line, its {floor:.1f}% "
        f"earnings-yield floor)"
    )
    hot = posture in HOT_POSTURES

    if pct >= FAIR_VALUE_PCT and hot:
        return ExitVerdict(
            ticker,
            sell=True,
            reason=(
                f"past fair value with the cycle {posture}: {value}; a cycle "
                f"positioned for reversion is the playbook's full exit"
            ),
        )
    if pct >= FAIR_VALUE_PCT:
        return ExitVerdict(
            ticker,
            sell=False,
            reason=(
                f"second trim rung: {value}, at or past it with the cycle "
                f"{posture}, not positioned for reversion; the playbook's "
                f"second 25% trim is not executed (a verdict is keep or "
                f"sell), the position is kept"
            ),
        )
    if pct >= APPROACH_PCT:
        return ExitVerdict(
            ticker,
            sell=False,
            reason=(
                f"first trim rung: {value}, approaching it; the playbook's "
                f"first 25% trim is not executed (a verdict is keep or "
                f"sell), the position is kept"
            ),
        )
    reason = (
        f"thesis intact; {value}, under the {APPROACH_PCT:.0f}% rung where "
        f"the first 25% trim begins"
    )
    if hot:
        reason += (
            f"; the cycle is {posture}, where trigger 1 trims even good "
            f"companies — the trim is not executed (a verdict is keep or "
            f"sell), the position is kept"
        )
    return ExitVerdict(ticker, sell=False, reason=reason)


def verdicts(
    held: Mapping[str, HeldPosition] | None,
    prices: PriceLookup,
    fundamentals: FundamentalsLookup,
    posture: Posture | None,
    *,
    as_of: date,
    max_de: float = DEFAULT_MAX_DE,
) -> list[ExitVerdict]:
    """A verdict for every held name that can be judged, by ticker order.

    Price and fundamentals come from the same lookups the screen reads,
    so a held name is judged on the day's quote, never on a stale mark.
    """
    if not held:
        return []
    out: list[ExitVerdict] = []
    for ticker in sorted(held):
        verdict = decide(
            held[ticker],
            fundamentals.get(ticker),
            prices.get(ticker),
            posture,
            max_de=max_de,
        )
        if verdict is None:
            logger.info(f"{as_of}: {ticker} held — no Marks exit verdict (data)")
            continue
        out.append(verdict)
    if out:
        sells = sum(1 for v in out if v.sell)
        logger.info(
            f"{as_of}: Marks exits on {len(held)} held ({posture}): "
            f"{len(out) - sells} keep, {sells} sell, "
            f"{len(held) - len(out)} no verdict"
        )
    return out


__all__ = [
    "APPROACH_PCT",
    "FAIR_VALUE_PCT",
    "HOT_POSTURES",
    "decide",
    "thesis_break",
    "verdicts",
]
