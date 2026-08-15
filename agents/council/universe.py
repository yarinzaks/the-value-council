"""The universe: what the engine is allowed to look at.

``COUNCIL_SELECTION.md`` section 1. Eight rules, in two groups.

U1-U6 define the tradeable universe and apply to everything. U7 and U8
narrow it further for the **statistical sleeve** only — a size band and
an index exclusion that stand in for the doctrine's "three or fewer
analysts", because no free source carries coverage counts. The Council
may still buy a large or index-listed company as a Core position after
reading it; the machine may not.

The same rule as the screen
---------------------------

A rule that cannot be evaluated FAILS. Every check returns an explicit
state and the run log can tell "we know this is OTC" from "we could not
find out where this trades" — but both stop the name.

The count is a health check
---------------------------

Section 1 expects the tradeable universe to land around two thousand
names and says plainly that **under 500 or over 5,000 means a gate is
miswired, not that the market changed**. :func:`build_universe` returns
that count alongside the members so a caller can assert on it rather
than discovering the miswiring through a strange basket three months
later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from agents.council.screen import Outcome
from core.logger import get_logger

logger = get_logger("agents.council.universe")

#: U3. Both halves: at least this many quarters of fundamentals, and a
#: latest filing no older than this. The quarter count kills shells and
#: dark companies; the staleness bound kills the successor-entity trap,
#: where a holdco reorganisation moves a ticker to a new CIK with about
#: one year of filings and none of the operating history.
MIN_QUARTERS_OF_FUNDAMENTALS: int = 8
MAX_FILING_AGE_DAYS: int = 400

#: U4. Below a dollar you are trading bankruptcy shells; FRCB continued
#: into OTC at $0.0004 and Yahoo kept serving it a price.
MIN_PRICE_USD: float = 1.00

#: U5. Exitability, measured as the median over 63 sessions rather than
#: the mean, so one block trade cannot make an illiquid name look liquid.
MIN_MEDIAN_DOLLAR_VOLUME: float = 500_000.0
DOLLAR_VOLUME_SESSIONS: int = 63

#: U6. Banks, insurers and REITs. EV/EBIT and net-cash arithmetic are
#: meaningless for a business whose liabilities are its raw material.
FINANCIAL_SIC_RANGE: tuple[int, int] = (6000, 6999)

#: U7. The statistical sleeve's size band — where institutions cannot
#: size a position even if they wanted to.
MIN_MARKET_CAP: float = 50_000_000.0
MAX_MARKET_CAP: float = 5_000_000_000.0

#: Section 1's own sanity band on the resulting count.
EXPECTED_UNIVERSE_RANGE: tuple[int, int] = (500, 5_000)


@dataclass(frozen=True)
class UniverseInputs:
    """One company's universe facts, already point-in-time.

    ``None`` everywhere means "could not be determined", which fails the
    rule that reads it. No field defaults to a value that would let a
    name through on missing data.
    """

    ticker: str
    #: U1. ``True`` on NYSE / NASDAQ / AMEX, ``False`` for OTC and the
    #: rest, ``None`` when the exchange is unknown.
    major_us_listing: bool | None = None
    #: U2. A domestic 10-K filer. A 20-F filer reports under IFRS, which
    #: the fundamentals engine answers UNKNOWN for, and UNKNOWN cannot
    #: be screened.
    files_10k: bool | None = None
    quarters_of_fundamentals: int | None = None
    latest_filing: date | None = None
    price: float | None = None
    median_dollar_volume_63d: float | None = None
    sic: int | None = None
    market_cap: float | None = None
    #: U8. ``True`` if an index member on the decision date.
    in_sp500: bool | None = None


@dataclass(frozen=True)
class RuleResult:
    """One universe rule's verdict."""

    rule: str
    outcome: Outcome
    detail: str

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.PASS


@dataclass
class MembershipResult:
    """Every rule's verdict for one company."""

    ticker: str
    rules: list[RuleResult] = field(default_factory=list)

    @property
    def tradeable(self) -> bool:
        """U1-U6 all pass — the company may be held at all."""
        return bool(self.rules) and all(
            r.ok for r in self.rules if r.rule in ("U1", "U2", "U3", "U4", "U5", "U6")
        )

    @property
    def mechanical(self) -> bool:
        """U1-U8 all pass — the statistical sleeve may buy it."""
        return bool(self.rules) and all(r.ok for r in self.rules)

    @property
    def failures(self) -> list[RuleResult]:
        return [r for r in self.rules if not r.ok]


def _bool_rule(name: str, value: bool | None, yes: str, no: str) -> RuleResult:
    if value is None:
        return RuleResult(name, Outcome.UNKNOWN, f"{yes} could not be determined")
    return RuleResult(name, Outcome.PASS if value else Outcome.FAIL, yes if value else no)


def check(inputs: UniverseInputs, as_of: date) -> MembershipResult:
    """Evaluate all eight rules.

    Every rule runs even after one has failed, for the same reason the
    screen evaluates every gate: how many ways a name was excluded is
    what distinguishes a miswired gate from a genuinely ineligible
    company.
    """
    i = inputs
    rules: list[RuleResult] = [
        _bool_rule(
            "U1",
            i.major_us_listing,
            "listed on a major US exchange",
            "not on NYSE / NASDAQ / AMEX",
        ),
        _bool_rule("U2", i.files_10k, "files a 10-K", "not a domestic 10-K filer"),
    ]

    # U3 — both halves, reported separately so the log says which failed.
    if i.quarters_of_fundamentals is None or i.latest_filing is None:
        rules.append(
            RuleResult("U3", Outcome.UNKNOWN, "filing history could not be read")
        )
    elif i.quarters_of_fundamentals < MIN_QUARTERS_OF_FUNDAMENTALS:
        rules.append(
            RuleResult(
                "U3",
                Outcome.FAIL,
                f"{i.quarters_of_fundamentals} quarters of fundamentals, "
                f"below {MIN_QUARTERS_OF_FUNDAMENTALS}",
            )
        )
    else:
        age = (as_of - i.latest_filing).days
        if age > MAX_FILING_AGE_DAYS:
            rules.append(
                RuleResult(
                    "U3",
                    Outcome.FAIL,
                    f"latest filing {age} days old, over {MAX_FILING_AGE_DAYS}",
                )
            )
        else:
            rules.append(
                RuleResult(
                    "U3",
                    Outcome.PASS,
                    f"{i.quarters_of_fundamentals} quarters, filed {age} days ago",
                )
            )

    # U4 — price floor.
    if i.price is None:
        rules.append(RuleResult("U4", Outcome.UNKNOWN, "no price"))
    elif i.price < MIN_PRICE_USD:
        rules.append(
            RuleResult("U4", Outcome.FAIL, f"${i.price:.4f} below ${MIN_PRICE_USD:.2f}")
        )
    else:
        rules.append(RuleResult("U4", Outcome.PASS, f"${i.price:.2f}"))

    # U5 — exitability.
    if i.median_dollar_volume_63d is None:
        rules.append(RuleResult("U5", Outcome.UNKNOWN, "no volume history"))
    elif i.median_dollar_volume_63d < MIN_MEDIAN_DOLLAR_VOLUME:
        rules.append(
            RuleResult(
                "U5",
                Outcome.FAIL,
                f"${i.median_dollar_volume_63d:,.0f}/day median, "
                f"below ${MIN_MEDIAN_DOLLAR_VOLUME:,.0f}",
            )
        )
    else:
        rules.append(
            RuleResult(
                "U5", Outcome.PASS, f"${i.median_dollar_volume_63d:,.0f}/day median"
            )
        )

    # U6 — financials excluded from the mechanical path.
    if i.sic is None:
        rules.append(RuleResult("U6", Outcome.UNKNOWN, "no SIC code"))
    elif FINANCIAL_SIC_RANGE[0] <= i.sic <= FINANCIAL_SIC_RANGE[1]:
        rules.append(
            RuleResult("U6", Outcome.FAIL, f"SIC {i.sic} is a bank, insurer or REIT")
        )
    else:
        rules.append(RuleResult("U6", Outcome.PASS, f"SIC {i.sic}"))

    # U7 — the sleeve's size band.
    if i.market_cap is None:
        rules.append(RuleResult("U7", Outcome.UNKNOWN, "no market cap"))
    elif not (MIN_MARKET_CAP <= i.market_cap <= MAX_MARKET_CAP):
        rules.append(
            RuleResult(
                "U7",
                Outcome.FAIL,
                f"${i.market_cap / 1e6:,.0f}M outside "
                f"${MIN_MARKET_CAP / 1e6:,.0f}M-${MAX_MARKET_CAP / 1e9:,.0f}bn",
            )
        )
    else:
        rules.append(RuleResult("U7", Outcome.PASS, f"${i.market_cap / 1e6:,.0f}M"))

    # U8 — index exclusion as the neglect proxy.
    if i.in_sp500 is None:
        rules.append(RuleResult("U8", Outcome.UNKNOWN, "index membership unknown"))
    elif i.in_sp500:
        rules.append(RuleResult("U8", Outcome.FAIL, "S&P 500 member"))
    else:
        rules.append(RuleResult("U8", Outcome.PASS, "not in the S&P 500"))

    return MembershipResult(ticker=i.ticker, rules=rules)


@dataclass
class UniverseReport:
    """Who is in, who may be bought mechanically, and the health check."""

    as_of: date
    tradeable: list[str]
    mechanical: list[str]
    #: Rule name -> how many names it excluded. Read this when a count
    #: comes in outside the expected band: one rule doing all the
    #: excluding is a miswiring, not a market.
    excluded_by: dict[str, int]

    @property
    def within_expected_range(self) -> bool:
        low, high = EXPECTED_UNIVERSE_RANGE
        return low <= len(self.tradeable) <= high


def build_universe(
    rows: list[UniverseInputs], as_of: date
) -> UniverseReport:
    """Partition the roster and report why names dropped out."""
    tradeable: list[str] = []
    mechanical: list[str] = []
    excluded_by: dict[str, int] = {}

    for row in rows:
        result = check(row, as_of)
        if result.tradeable:
            tradeable.append(row.ticker)
            if result.mechanical:
                mechanical.append(row.ticker)
        # Attribute the drop to the first rule that stopped it, so the
        # counts sum to the number excluded rather than double-counting
        # a company that failed four rules at once.
        first = next((r for r in result.failures), None)
        if first is not None:
            excluded_by[first.rule] = excluded_by.get(first.rule, 0) + 1

    report = UniverseReport(
        as_of=as_of,
        tradeable=tradeable,
        mechanical=mechanical,
        excluded_by=excluded_by,
    )
    breakdown = ", ".join(
        f"{k} {v}" for k, v in sorted(report.excluded_by.items())
    )
    logger.info(
        f"{as_of}: universe {len(tradeable)} tradeable, "
        f"{len(mechanical)} mechanical (excluded: {breakdown})"
    )
    if not report.within_expected_range:
        logger.warning(
            f"{as_of}: {len(tradeable)} tradeable names is outside "
            f"{EXPECTED_UNIVERSE_RANGE} — section 1 reads that as a miswired "
            f"gate rather than a changed market. Excluded: {breakdown}"
        )
    return report


def stale_filing_cutoff(as_of: date) -> date:
    """The oldest filing date U3 still accepts."""
    return as_of - timedelta(days=MAX_FILING_AGE_DAYS)
