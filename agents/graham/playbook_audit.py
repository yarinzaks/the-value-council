"""Graham screen rebuilt from ``playbook.md`` alone — an audit of the agent.

Why a second implementation exists
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:mod:`agents.graham.filters` is what the agent trades on, and it is a
deliberately loose reading of the playbook: trailing P/E instead of the
three-year average, no ten-year earnings record, no dividend streak, a
$500M size floor instead of $2B. :mod:`agents.graham.watch` reuses that
filter on purpose so the watch list can never disagree with the buy path.

This module answers a different question: *what would Graham buy if the
playbook were applied as written?* It shares no code with the filter, so
a disagreement between the two is information, not drift. It is
read-only — it never touches a portfolio — and nothing in the live
runner imports it.

What it applies (playbook section in brackets)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* Category A, Defensive — all seven criteria [4.1].
* Category B, Enterprising — all five criteria [4.2].
* Category C, Net-Net — price ≤ ⅔ NCAV and positive TTM earnings [4.3].
* The anti-patterns as a hard gate before any category [10, 12.1 step 1].
* Intrinsic value per category and the 33% margin of safety [12.1 steps 6-7].
* The AAA-yield cut to the Graham Number coefficient [11.6] and the
  Treasury-driven EPV hurdle [4.4].
* A sell target per category and the sell triggers [7.1].

Each qualifying company also gets a thesis in English and Hebrew. It is
assembled from the figures above, not from a model: it restates the
arithmetic and predicts nothing, in keeping with playbook 3.2.

Known limits, stated rather than hidden
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* XBRL starts in 2009, so the 20-year dividend record [4.1 #4] can only
  be checked over the years that exist; the streak must cover every
  available year, with a floor of 15.
* EPS growth and the three-year P/E use net income over today's diluted
  share count. That is split-proof and penalises dilution, at the cost of
  crediting buybacks less than per-share EPS would.
* Banks, insurers and REITs file unclassified balance sheets, so they
  report no current assets and fall out as ``insufficient data``.
* Qualitative steps 3-5 (10-K, 13F, Form 4) are not attempted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

import pandas as pd

GRAHAM_NUMBER_COEFF = 22.5
GRAHAM_NUMBER_AAA_ANCHOR = 4.4  # playbook 11.6: multiply by 4.4 / Y above 7%
AAA_ADJUST_ABOVE_PCT = 7.0
EPV_BASE_RATE = 0.10
EPV_TREASURY_ANCHOR_PCT = 5.0
MIN_MARGIN_OF_SAFETY = 0.33

DEFENSIVE_MIN_MCAP_USD = 2e9
DEFENSIVE_MIN_CURRENT_RATIO = 2.0
DEFENSIVE_MIN_GROWTH = 0.33
DEFENSIVE_MAX_PE3 = 15.0
DEFENSIVE_MAX_PB = 1.5
DEFENSIVE_MAX_PE_X_PB = 22.5
DIVIDEND_STREAK_FLOOR_YEARS = 15

ENTERPRISING_MIN_CURRENT_RATIO = 1.5
ENTERPRISING_MAX_DEBT_TO_NCA = 1.1
ENTERPRISING_MAX_PRICE_TO_NTA = 1.2
ENTERPRISING_MAX_PE = 10.0

NET_NET_MAX_PRICE_TO_NCAV = 2 / 3

ANTI_MIN_MCAP_USD = 50e6
ANTI_MAX_INTANGIBLES_TO_EQUITY = 0.5
# Anti-pattern 10. Matched against the exchange's "sector/industry" label.
EXCLUDED_INDUSTRY_WORDS: tuple[str, ...] = (
    "biotechnology",
    "pharmaceutical",
    "software",
    "blank check",
    "internet",
    "edp services",
)

# A fiscal-year flow spans roughly a year; 52/53-week years land inside.
_FY_MIN_DAYS = 350
_FY_MAX_DAYS = 380
# A value this many times smaller than its own history was tagged in
# thousands by the filer (seen: UVV net income FY2021+, CIX 2026 shares).
_SCALE_ERROR_RATIO = 300
_STALE_BALANCE_SHEET_DAYS = 400

NET_INCOME_CONCEPTS = (
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
)
DIVIDEND_CONCEPTS = (
    "PaymentsOfDividendsCommonStock",
    "PaymentsOfDividends",
    "DividendsCommonStockCash",
    "DividendsCommonStock",
    "CommonStockDividendsPerShareDeclared",
    "CommonStockDividendsPerShareCashPaid",
    "DividendsCash",
)
_UNITS = ("USD", "USD/shares", "shares")

Category = Literal["Defensive", "Enterprising", "Net-Net"]
Status = Literal[
    "BUY",
    "WATCH",
    "REJECT_ANTI_PATTERN",
    "REJECT_NO_CATEGORY",
    "INSUFFICIENT_DATA",
]


@dataclass(frozen=True)
class MarketRates:
    """Yields, in percent, that move the playbook's valuation constants."""

    aaa_corporate_pct: float
    treasury_20y_pct: float

    @property
    def graham_number_coeff(self) -> float:
        if self.aaa_corporate_pct <= AAA_ADJUST_ABOVE_PCT:
            return GRAHAM_NUMBER_COEFF
        return GRAHAM_NUMBER_COEFF * GRAHAM_NUMBER_AAA_ANCHOR / self.aaa_corporate_pct

    @property
    def epv_rate(self) -> float:
        excess = max(0.0, self.treasury_20y_pct - EPV_TREASURY_ANCHOR_PCT)
        return EPV_BASE_RATE + excess / 100


@dataclass(frozen=True)
class Snapshot:
    """The figures the playbook needs, extracted from one company's XBRL facts.

    ``net_income`` and ``dividends`` are keyed by fiscal year. Balance
    sheet values are in USD as of ``balance_sheet_date``.
    """

    net_income: dict[int, float]
    dividends: dict[int, float]
    current_assets: float
    current_liabilities: float
    equity: float
    total_liabilities: float | None
    long_term_debt: float
    short_term_debt: float
    goodwill: float
    intangibles: float
    preferred: float
    shares: float
    ttm_net_income: float | None
    balance_sheet_date: pd.Timestamp
    ttm_basis: str


@dataclass(frozen=True)
class Valuation:
    category: Category
    intrinsic_value: float
    method: str
    margin_of_safety: float
    max_entry_price: float
    sell_target: float
    sell_target_method: str
    # Negative when the price is already above the sell target.
    upside_to_target: float


@dataclass(frozen=True)
class AuditResult:
    ticker: str
    price: float
    industry: str
    status: Status
    metrics: dict[str, float | int | str | None] = field(default_factory=dict)
    defensive_failures: tuple[str, ...] = ()
    enterprising_failures: tuple[str, ...] = ()
    net_net_failures: tuple[str, ...] = ()
    anti_patterns: tuple[str, ...] = ()
    qualifying: tuple[Category, ...] = ()
    valuation: Valuation | None = None
    note: str = ""
    thesis_en: str = ""
    thesis_he: str = ""
    exit_triggers_en: tuple[str, ...] = ()
    exit_triggers_he: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------
def _rows(facts: pd.DataFrame, concept: str) -> pd.DataFrame:
    return facts[(facts["concept"] == concept) & facts["unit"].isin(_UNITS)]


def _fiscal_year(period_end: pd.Timestamp) -> int:
    # Fiscal years ending January-March belong to the prior calendar year,
    # matching how retailers and March filers label them.
    return int(period_end.year if period_end.month > 3 else period_end.year - 1)


def annual_values(facts: pd.DataFrame, concepts: tuple[str, ...]) -> dict[int, float]:
    """Fiscal-year flow values; the first concept with data wins each year.

    Where a later filing restates a year, the latest filing wins.
    """
    out: dict[int, float] = {}
    for concept in concepts:
        rows = _rows(facts, concept)
        rows = rows[rows["period_start"].notna()]
        days = (rows["period_end"] - rows["period_start"]).dt.days
        rows = rows[(days >= _FY_MIN_DAYS) & (days <= _FY_MAX_DAYS)]
        if rows.empty:
            continue
        latest = rows.sort_values("filed").groupby("period_end").tail(1)
        for end, value in zip(latest["period_end"], latest["value"], strict=True):
            out.setdefault(_fiscal_year(end), float(value))
    return out


def fix_scale_errors(series: dict[int, float]) -> dict[int, float]:
    """Multiply by 1,000 any value the filer evidently tagged in thousands.

    Only a value ~300x below the series median *and* landing within 10x
    of it once scaled is touched, so a genuine collapse in earnings is
    left alone.
    """
    magnitudes = sorted(abs(v) for v in series.values() if v)
    if len(magnitudes) < 4:
        return dict(series)
    median = magnitudes[len(magnitudes) // 2]
    fixed: dict[int, float] = {}
    for year, value in series.items():
        scaled = abs(value) * 1000
        if (
            value
            and abs(value) * _SCALE_ERROR_RATIO < median
            and 0.1 * median <= scaled <= 10 * median
        ):
            fixed[year] = value * 1000
        else:
            fixed[year] = value
    return fixed


def latest_instant(
    facts: pd.DataFrame, concepts: tuple[str, ...]
) -> tuple[float | None, pd.Timestamp | None]:
    """Most recent point-in-time value across ``concepts``."""
    best_value: float | None = None
    best_end: pd.Timestamp | None = None
    for concept in concepts:
        rows = _rows(facts, concept)
        rows = rows[rows["period_start"].isna()]
        if rows.empty:
            continue
        row = rows.sort_values(["period_end", "filed"]).iloc[-1]
        if best_end is None or row["period_end"] > best_end:
            best_value, best_end = float(row["value"]), row["period_end"]
    return best_value, best_end


def instant_at(facts: pd.DataFrame, concepts: tuple[str, ...], at: pd.Timestamp) -> float | None:
    """Point-in-time value on exactly ``at`` from the first concept that has one."""
    for concept in concepts:
        rows = _rows(facts, concept)
        rows = rows[rows["period_start"].isna() & (rows["period_end"] == at)]
        if not rows.empty:
            return float(rows.sort_values("filed")["value"].iloc[-1])
    return None


def diluted_shares(facts: pd.DataFrame) -> float | None:
    """Latest diluted weighted share count, falling back to cover-page shares.

    Diluted weighted shares cover every share class (the cover-page figure
    is often one class only — NKE's is class B alone) and sit at or above
    shares outstanding, which errs conservative on per-share values.
    """
    weighted = facts[
        (facts["concept"] == "WeightedAverageNumberOfDilutedSharesOutstanding")
        & (facts["unit"] == "shares")
        & facts["period_start"].notna()
    ]
    if not weighted.empty:
        ordered = weighted.sort_values(["period_end", "filed"])
        last = ordered.iloc[-1]
        if last["period_end"] >= facts["period_end"].max() - timedelta(days=200):
            value = float(last["value"])
            if value * _SCALE_ERROR_RATIO < float(ordered["value"].median()):
                value *= 1000
            return value
    cover = facts[
        (facts["concept"] == "EntityCommonStockSharesOutstanding") & (facts["unit"] == "shares")
    ]
    if not cover.empty:
        latest = cover[cover["period_end"] == cover["period_end"].max()]
        latest = latest.sort_values("filed").drop_duplicates("value", keep="last")
        return float(latest["value"].sum())
    outstanding, _ = latest_instant(facts, ("CommonStockSharesOutstanding",))
    return outstanding


def ttm_net_income(facts: pd.DataFrame) -> tuple[float | None, str]:
    """Latest fiscal year plus the current YTD minus the prior-year YTD.

    Returns the value and its basis: ``TTM<date>``, ``FY<date>`` when no
    comparable YTD pair exists, or ``none``.
    """
    rows = _rows(facts, "NetIncomeLoss")
    rows = rows[rows["period_start"].notna()].copy()
    if rows.empty:
        return None, "none"
    rows["days"] = (rows["period_end"] - rows["period_start"]).dt.days
    fiscal = rows[(rows["days"] >= _FY_MIN_DAYS) & (rows["days"] <= _FY_MAX_DAYS)]
    if fiscal.empty:
        return None, "none"
    fy = fiscal.sort_values(["period_end", "filed"]).iloc[-1]
    fy_end: pd.Timestamp = fy["period_end"]
    fy_value = float(fy["value"])
    ytd = rows[
        (rows["period_start"] > fy_end)
        & (rows["period_start"] <= fy_end + timedelta(days=10))
        & (rows["days"] < _FY_MIN_DAYS)
    ]
    if ytd.empty:
        return fy_value, f"FY{fy_end.date()}"
    current = ytd.sort_values(["period_end", "filed"]).iloc[-1]
    prior_end = current["period_end"] - pd.DateOffset(years=1)
    prior = rows[
        ((rows["period_end"] - prior_end).abs().dt.days <= 10)
        & ((rows["days"] - current["days"]).abs() <= 10)
    ]
    if prior.empty:
        return fy_value, f"FY{fy_end.date()}"
    prior_value = float(prior.sort_values("filed")["value"].iloc[-1])
    return fy_value + float(current["value"]) - prior_value, f"TTM{current['period_end'].date()}"


def extract_snapshot(facts: pd.DataFrame) -> Snapshot | str:
    """Build a :class:`Snapshot`, or return why the facts cannot support one."""
    if facts.empty:
        return "no XBRL facts"
    net_income = fix_scale_errors(annual_values(facts, NET_INCOME_CONCEPTS))
    if not net_income:
        return "no annual net income"
    current_assets, bs_date = latest_instant(facts, ("AssetsCurrent",))
    if current_assets is None or bs_date is None:
        return "no current assets (unclassified balance sheet)"
    if bs_date < facts["filed"].max() - timedelta(days=_STALE_BALANCE_SHEET_DAYS):
        return f"stale balance sheet ({bs_date.date()})"
    current_liabilities = instant_at(facts, ("LiabilitiesCurrent",), bs_date)
    equity = instant_at(facts, ("StockholdersEquity",), bs_date)
    equity_incl_nci = instant_at(
        facts, ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",), bs_date
    )
    if equity is None:
        equity = equity_incl_nci
    shares = diluted_shares(facts)
    if current_liabilities is None or equity is None or not shares:
        return "missing current liabilities, equity or share count"

    total_liabilities = instant_at(facts, ("Liabilities",), bs_date)
    if total_liabilities is None:
        total = instant_at(facts, ("LiabilitiesAndStockholdersEquity",), bs_date)
        if total is not None:
            total_liabilities = total - (equity_incl_nci if equity_incl_nci is not None else equity)

    ttm, ttm_basis = ttm_net_income(facts)
    latest_fy = net_income[max(net_income)]
    if ttm is not None and latest_fy and abs(ttm) * _SCALE_ERROR_RATIO < abs(latest_fy):
        ttm *= 1000

    def _or_zero(concepts: tuple[str, ...]) -> float:
        value = instant_at(facts, concepts, bs_date)
        return value if value is not None else 0.0

    return Snapshot(
        net_income=net_income,
        dividends=annual_values(facts, DIVIDEND_CONCEPTS),
        current_assets=current_assets,
        current_liabilities=current_liabilities,
        equity=equity,
        total_liabilities=total_liabilities,
        long_term_debt=_or_zero(
            ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt")
        ),
        short_term_debt=_or_zero(("LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings")),
        goodwill=_or_zero(("Goodwill",)),
        intangibles=_or_zero(
            ("IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet")
        ),
        preferred=_or_zero(("PreferredStockValue",)),
        shares=shares,
        ttm_net_income=ttm,
        balance_sheet_date=bs_date,
        ttm_basis=ttm_basis,
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def _all_positive(values: list[float | None]) -> bool:
    return all(v is not None and v > 0 for v in values)


def dividend_streak(dividends: dict[int, float], last_year: int) -> int:
    """Consecutive fiscal years with a positive dividend, counting back from ``last_year``."""
    streak = 0
    year = last_year
    while dividends.get(year, 0.0) > 0:
        streak += 1
        year -= 1
    return streak


def _finite(x: float) -> float | None:
    return round(x, 4) if math.isfinite(x) else None


def sell_target(category: Category, intrinsic_value: float, epv: float | None) -> tuple[float, str]:
    """Price at which playbook 7.1 trigger 1 says to sell, and how it was set.

    Defensive sells at the Graham Number and Net-Net at full NCAV, both of
    which are already its intrinsic value. Enterprising sells at EPV, which
    can differ from the lower bound it was bought against; without an EPV
    that bound is the only figure left.
    """
    if category == "Enterprising" and epv is not None:
        return epv, "EPV"
    return intrinsic_value, {
        "Defensive": "Graham Number",
        "Enterprising": "buy-side intrinsic value (no EPV)",
        "Net-Net": "NCAV",
    }[category]


def exit_triggers(category: Category, target: float) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Playbook 7.1 sell triggers as (English, Hebrew). Trigger 4 needs a portfolio."""
    english = [
        f"Price is at or above {target:.2f}",
        "Current ratio falls below 1.5, the dividend is cut, or earnings turn negative",
    ]
    hebrew = [
        f"המחיר ב-{target:.2f} ומעלה",
        "היחס השוטף יורד מתחת ל-1.5, הדיבידנד נחתך או שהחברה עוברת להפסד",
    ]
    if category == "Net-Net":
        english.append("Two years pass without reaching the target")
        hebrew.append("עברו שנתיים בלי שהמחיר הגיע ליעד")
    return tuple(english), tuple(hebrew)


def _ratio(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}f}" if math.isfinite(x) else "n/a"


def build_thesis(
    *,
    ticker: str,
    price: float,
    valuation: Valuation,
    status: Status,
    pe3: float,
    pb: float,
    current_ratio: float,
    dividend_years: int,
    epv: float | None,
    anti_patterns: tuple[str, ...],
) -> tuple[str, str]:
    """A (English, Hebrew) thesis stating only what the figures show."""
    v = valuation
    figures = (
        f"P/E {_ratio(pe3)}, P/B {_ratio(pb)}, current ratio {_ratio(current_ratio)}, "
        f"{dividend_years}y dividend streak"
    )
    figures_he = (
        f"P/E {_ratio(pe3)}, ‏P/B {_ratio(pb)}, יחס שוטף {_ratio(current_ratio)}, "
        f"רצף דיבידנדים של {dividend_years} שנים"
    )
    en = [
        f"{ticker} qualifies as {v.category} ({figures}).",
        f"At {price:.2f} against a value of {v.intrinsic_value:.2f} ({v.method}), "
        f"the margin of safety is {v.margin_of_safety:.1%}; 33% is required.",
    ]
    he = [
        f"{ticker} עומדת בקטגוריית {v.category} ({figures_he}).",
        f"במחיר {price:.2f} מול שווי של {v.intrinsic_value:.2f} ({v.method}), "
        f"מרווח הביטחון הוא {v.margin_of_safety:.1%}, והנדרש הוא 33%.",
    ]
    if v.upside_to_target > 0:
        en.append(f"Sell target {v.sell_target:.2f} ({v.upside_to_target:+.1%}).")
        he.append(f"יעד מכירה {v.sell_target:.2f} ({v.upside_to_target:+.1%}).")
    else:
        en.append(f"The price is already above the sell target of {v.sell_target:.2f}.")
        he.append(f"המחיר כבר מעל יעד המכירה של {v.sell_target:.2f}.")

    if status == "BUY":
        en.append("It passes every quantitative gate.")
        he.append("היא עוברת את כל הבדיקות הכמותיות.")
    elif status == "WATCH":
        en.append(f"Not a purchase at this price; it becomes one below {v.max_entry_price:.2f}.")
        he.append(f"לא קנייה במחיר הזה; היא הופכת לקנייה מתחת ל-{v.max_entry_price:.2f}.")
    else:
        rejected = "; ".join(anti_patterns)
        en.append(f"Rejected regardless of price: {rejected}.")
        he.append(f"נפסלת בכל מחיר: {rejected}.")

    if v.sell_target_method != "EPV" and epv is not None and epv < price:
        en.append(f"Caution: earnings power value of {epv:.2f} is below the price.")
        he.append(f"זהירות: ה-EPV של {epv:.2f} נמוך מהמחיר.")
    en.append("Quantitative only: filings, 13F and insider activity were not reviewed.")
    he.append("ניתוח כמותי בלבד: דוחות, 13F ופעילות פנימיים לא נבדקו.")
    return " ".join(en), " ".join(he)


def evaluate(
    ticker: str, snap: Snapshot, price: float, industry: str, rates: MarketRates
) -> AuditResult:
    """Apply playbook sections 4, 10 and 12.1 to one company at one price."""
    if price <= 0:
        raise ValueError(f"{ticker}: price must be positive, got {price}")
    ni = snap.net_income
    last_fy = max(ni)
    ten_years = [ni.get(y) for y in range(last_fy - 9, last_fy + 1)]
    five_years = [ni.get(y) for y in range(last_fy - 4, last_fy + 1)]
    first_three = [ni.get(y) for y in range(last_fy - 9, last_fy - 6)]
    last_three = [ni.get(y) for y in range(last_fy - 2, last_fy + 1)]

    mcap = price * snap.shares
    working_capital = snap.current_assets - snap.current_liabilities
    current_ratio = (
        snap.current_assets / snap.current_liabilities if snap.current_liabilities else math.inf
    )
    total_debt = snap.long_term_debt + snap.short_term_debt
    # A missing year counts as zero earnings, never as a pass.
    avg3_ni = sum(v or 0.0 for v in last_three) / 3
    eps3 = avg3_ni / snap.shares
    bvps = snap.equity / snap.shares
    tbvps = (snap.equity - snap.goodwill - snap.intangibles) / snap.shares
    ncav_ps = (
        (snap.current_assets - snap.total_liabilities - snap.preferred) / snap.shares
        if snap.total_liabilities is not None
        else math.nan
    )
    ttm_eps = snap.ttm_net_income / snap.shares if snap.ttm_net_income is not None else None
    streak = dividend_streak(snap.dividends, last_fy)
    years_on_file = last_fy - min(ni) + 1

    pe3 = price / eps3 if eps3 > 0 else math.inf
    pb = price / bvps if bvps > 0 else math.inf
    pe_ttm = price / ttm_eps if ttm_eps is not None and ttm_eps > 0 else math.inf
    graham_number = (
        math.sqrt(rates.graham_number_coeff * eps3 * bvps) if eps3 > 0 and bvps > 0 else None
    )
    epv: float | None = None
    if _all_positive(five_years):
        eps5 = sum(v for v in five_years if v is not None) / 5 / snap.shares
        epv = eps5 / rates.epv_rate
    growth: float | None = None
    if _all_positive(first_three):
        base = sum(v for v in first_three if v is not None) / 3
        growth = avg3_ni / base - 1

    # ---- Category A — Defensive [4.1]
    defensive: list[str] = []
    if mcap < DEFENSIVE_MIN_MCAP_USD:
        defensive.append("size < $2B")
    if current_ratio < DEFENSIVE_MIN_CURRENT_RATIO:
        defensive.append("current ratio < 2")
    if snap.long_term_debt > working_capital:
        defensive.append("long-term debt > working capital")
    if not _all_positive(ten_years):
        defensive.append("not 10 years of positive earnings")
    if streak < min(DIVIDEND_STREAK_FLOOR_YEARS, years_on_file):
        defensive.append(f"dividend streak {streak}y")
    if growth is None or growth < DEFENSIVE_MIN_GROWTH:
        shown = "n/a" if growth is None else f"{growth * 100:.0f}%"
        defensive.append(f"10y earnings growth {shown} < 33%")
    if pe3 > DEFENSIVE_MAX_PE3:
        defensive.append("P/E (3y avg) > 15")
    if not (pb <= DEFENSIVE_MAX_PB or pe3 * pb <= DEFENSIVE_MAX_PE_X_PB):
        defensive.append("P/B > 1.5 and P/E x P/B > 22.5")

    # ---- Category B — Enterprising [4.2]
    enterprising: list[str] = []
    if current_ratio < ENTERPRISING_MIN_CURRENT_RATIO:
        enterprising.append("current ratio < 1.5")
    if working_capital <= 0 or total_debt > ENTERPRISING_MAX_DEBT_TO_NCA * working_capital:
        enterprising.append("debt > 110% of net current assets")
    if not _all_positive(five_years):
        enterprising.append("loss in the last 5 years")
    if not snap.dividends.get(last_fy):
        enterprising.append("no current dividend")
    four_years_ago = ni.get(last_fy - 4)
    if four_years_ago is None or ni[last_fy] < four_years_ago:
        enterprising.append("earnings below 4 years ago")
    if not (price < ENTERPRISING_MAX_PRICE_TO_NTA * tbvps or pe_ttm < ENTERPRISING_MAX_PE):
        enterprising.append("price >= 1.2x NTA and P/E >= 10")

    # ---- Category C — Net-Net [4.3]
    net_net: list[str] = []
    if not math.isfinite(ncav_ps) or ncav_ps <= 0 or price > NET_NET_MAX_PRICE_TO_NCAV * ncav_ps:
        net_net.append("price > 2/3 NCAV")
    if ttm_eps is None or ttm_eps <= 0:
        net_net.append("TTM EPS <= 0")

    # ---- Anti-patterns [10]
    anti: list[str] = []
    if mcap < ANTI_MIN_MCAP_USD:
        anti.append("#3 market cap < $50M")
    if not _all_positive(ten_years):
        anti.append("#1 no 10 years of positive earnings")
    if not any(v > 0 for v in snap.dividends.values()):
        anti.append("#2 never paid a dividend")
    if total_debt > snap.equity:
        anti.append("#8 debt exceeds book value")
    if (
        snap.equity > 0
        and (snap.goodwill + snap.intangibles) > ANTI_MAX_INTANGIBLES_TO_EQUITY * snap.equity
    ):
        anti.append("#6 intangibles > 50% of equity")
    if any(word in industry.lower() for word in EXCLUDED_INDUSTRY_WORDS):
        anti.append("#10 excluded sector")

    # ---- Intrinsic value and margin of safety [12.1 steps 6-7]
    candidates: list[tuple[Category, float, str]] = []
    if not defensive and graham_number:
        candidates.append(("Defensive", graham_number, "Graham Number"))
    if not enterprising and ttm_eps is not None and ttm_eps > 0:
        enterprising_value = min(10 * ttm_eps, 1.2 * tbvps)
        # Negative tangible book has no value to buy under or sell at, and
        # would read as a huge margin of safety in the formula below.
        if enterprising_value > 0:
            candidates.append(("Enterprising", enterprising_value, "min(10x TTM EPS, 1.2x NTA)"))
    if not net_net:
        candidates.append(("Net-Net", ncav_ps, "NCAV"))

    valuation: Valuation | None = None
    status: Status
    if candidates:
        category, value, method = candidates[0]
        mos = (value - price) / value
        target, target_method = sell_target(category, value, epv)
        valuation = Valuation(
            category=category,
            intrinsic_value=round(value, 2),
            method=method,
            margin_of_safety=round(mos, 4),
            max_entry_price=round(value * (1 - MIN_MARGIN_OF_SAFETY), 2),
            sell_target=round(target, 2),
            sell_target_method=target_method,
            upside_to_target=round((target - price) / price, 4),
        )
        if anti:
            status = "REJECT_ANTI_PATTERN"
        elif mos >= MIN_MARGIN_OF_SAFETY:
            status = "BUY"
        else:
            status = "WATCH"
    else:
        status = "REJECT_ANTI_PATTERN" if anti else "REJECT_NO_CATEGORY"

    thesis_en = thesis_he = ""
    triggers_en: tuple[str, ...] = ()
    triggers_he: tuple[str, ...] = ()
    if valuation is not None:
        thesis_en, thesis_he = build_thesis(
            ticker=ticker,
            price=price,
            valuation=valuation,
            status=status,
            pe3=pe3,
            pb=pb,
            current_ratio=current_ratio,
            dividend_years=streak,
            epv=epv,
            anti_patterns=tuple(anti),
        )
        triggers_en, triggers_he = exit_triggers(valuation.category, valuation.sell_target)

    metrics: dict[str, float | int | str | None] = {
        "market_cap_usd": round(mcap),
        "current_ratio": _finite(current_ratio),
        "working_capital_usd": round(working_capital),
        "long_term_debt_usd": round(snap.long_term_debt),
        "total_debt_usd": round(total_debt),
        "pe_3y": _finite(pe3),
        "pe_ttm": _finite(pe_ttm),
        "pb": _finite(pb),
        "eps_3y_avg": round(eps3, 4),
        "bvps": round(bvps, 4),
        "tangible_bvps": round(tbvps, 4),
        "ncav_per_share": _finite(ncav_ps),
        "graham_number": round(graham_number, 2) if graham_number else None,
        "epv": round(epv, 2) if epv else None,
        "earnings_growth_10y": round(growth, 4) if growth is not None else None,
        "dividend_streak_years": streak,
        "years_on_file": years_on_file,
        "balance_sheet_date": str(snap.balance_sheet_date.date()),
        "ttm_basis": snap.ttm_basis,
    }
    return AuditResult(
        ticker=ticker,
        price=price,
        industry=industry,
        status=status,
        metrics=metrics,
        defensive_failures=tuple(defensive),
        enterprising_failures=tuple(enterprising),
        net_net_failures=tuple(net_net),
        anti_patterns=tuple(anti),
        qualifying=tuple(c[0] for c in candidates),
        valuation=valuation,
        thesis_en=thesis_en,
        thesis_he=thesis_he,
        exit_triggers_en=triggers_en,
        exit_triggers_he=triggers_he,
    )


def audit(
    ticker: str, facts: pd.DataFrame, price: float, industry: str, rates: MarketRates
) -> AuditResult:
    """Extract and evaluate in one step; bad data becomes ``INSUFFICIENT_DATA``."""
    snap = extract_snapshot(facts)
    if isinstance(snap, str):
        return AuditResult(
            ticker=ticker, price=price, industry=industry, status="INSUFFICIENT_DATA", note=snap
        )
    return evaluate(ticker, snap, price, industry, rates)


def is_near_miss(result: AuditResult) -> bool:
    """Worth refreshing from live data: a category is one criterion away."""
    if result.status in ("BUY", "WATCH"):
        return True
    if result.anti_patterns or result.status == "INSUFFICIENT_DATA":
        return False
    return len(result.defensive_failures) == 1 or len(result.enterprising_failures) == 1


__all__ = [
    "AuditResult",
    "MarketRates",
    "Snapshot",
    "Valuation",
    "annual_values",
    "audit",
    "build_thesis",
    "diluted_shares",
    "dividend_streak",
    "evaluate",
    "exit_triggers",
    "extract_snapshot",
    "fix_scale_errors",
    "instant_at",
    "is_near_miss",
    "latest_instant",
    "sell_target",
    "ttm_net_income",
]
