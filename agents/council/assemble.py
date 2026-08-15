"""Turn cached filings and prices into what the doctrine's modules read.

The universe rules, the four gates, the composite rank and the exit
engine are all written as pure functions over value objects, so they can
be tested without a network, a cache or a clock. This is the module that
fills those objects in, and it is the only place in the selection path
that touches storage.

One pass per company
--------------------

Every field for one ticker comes out of a single :meth:`load_facts`
call. The obvious alternative — asking
:class:`~core.data.fundamentals_fetcher.FundamentalsFetcher` for one
field at a time — re-reads the same parquet file a dozen times per
company, and at three and a half thousand companies that is the
difference between a screen and an overnight job.

Point-in-time throughout
------------------------

Nothing filed after ``as_of`` is read, prior-year figures are taken with
a second ``as_of`` a year earlier rather than by indexing backwards
through a list, and every derived ratio inherits that bound. The
``filed``-not-``end`` rule lives in the TTM assembler and the instant
picker below; nothing here works around it.

What cannot be computed stays None
----------------------------------

A missing input arrives at the gates as ``None``, and the gates read
that as UNKNOWN, which fails. That chain is the whole safety property:
there is no default anywhere in this module that would let a company
through on data nobody has.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from agents.council.rank import RankInputs
from agents.council.screen import Financials
from agents.council.universe import UniverseInputs
from core.data.edgar_cache import EdgarCache
from core.data.edgar_facts import XbrlFact
from core.data.fundamentals_fetcher import CONCEPT_MAP, MAX_FACT_AGE_DAYS, expected_units
from core.data.listings import in_sp500_on, is_major_us_listing
from core.data.sic_codes import industry_for, sic_for
from core.data.ttm import trailing_twelve_months
from core.logger import get_logger
from core.scoring.piotroski import PiotroskiInputs, piotroski_f_score

logger = get_logger("agents.council.assemble")

#: A year, for prior-period comparisons. Deliberately 365 rather than a
#: calendar year: the comparison only has to land in the prior fiscal
#: year, and every filer's year boundary sits somewhere different.
ONE_YEAR_DAYS: int = 365

#: Three years, for the share-count CAGR in Gate C.
THREE_YEARS_DAYS: int = 3 * 365

#: Effective tax rates outside this band are the artefacts of a loss
#: year, a one-off settlement or a valuation-allowance release rather
#: than a rate the business will pay. Clamping keeps one strange quarter
#: from turning a NOPAT into nonsense.
TAX_RATE_BOUNDS: tuple[float, float] = (0.0, 0.50)

#: Fallback when the effective rate cannot be computed at all. The US
#: federal statutory rate; a company with no readable tax line is more
#: honestly taxed at the statutory rate than at zero.
DEFAULT_TAX_RATE: float = 0.21


def _instant(
    facts: Sequence[XbrlFact], field: str, as_of: date
) -> float | None:
    """The freshest balance-sheet value for ``field`` known at ``as_of``.

    Mirrors :meth:`FundamentalsFetcher.get_field` for stock concepts —
    whole chain evaluated, freshest survivor wins, age-bounded — but
    against facts already in memory. Preference in the chain is not
    recency: a concept a filer stopped tagging years ago must not
    outrank one it still tags today.
    """
    chain = CONCEPT_MAP.get(field)
    if not chain:
        return None
    units = set(expected_units(field))
    cutoff = as_of - timedelta(days=MAX_FACT_AGE_DAYS)
    wanted = {(ns, concept) for ns, concept in chain}

    best: XbrlFact | None = None
    for f in facts:
        if (f.namespace, f.concept) not in wanted:
            continue
        if f.unit not in units or f.filed > as_of or f.period_end < cutoff:
            continue
        if best is None or f.period_end > best.period_end or (f.period_end == best.period_end and f.filed > best.filed):
            best = f
    return None if best is None else best.value


def _ttm(facts: Sequence[XbrlFact], field: str, as_of: date) -> float | None:
    chain = CONCEPT_MAP.get(field)
    if not chain:
        return None
    result = trailing_twelve_months(
        facts,
        as_of,
        concepts=[concept for _, concept in chain],
        units=expected_units(field),
    )
    return None if result is None else result.value


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def effective_tax_rate(
    facts: Sequence[XbrlFact], as_of: date
) -> float:
    """Tax expense over pre-tax income, clamped, with a statutory fallback.

    A loss year produces a negative or absurd ratio, and a
    valuation-allowance release can produce a negative rate in a
    profitable one. Neither is the rate the business pays on the next
    dollar, which is what NOPAT wants.
    """
    tax = _ttm(facts, "income_tax_expense", as_of)
    pretax = _ttm(facts, "pretax_income", as_of)
    if tax is None or pretax is None or pretax <= 0:
        return DEFAULT_TAX_RATE
    low, high = TAX_RATE_BOUNDS
    return min(high, max(low, tax / pretax))


def return_on_invested_capital(
    facts: Sequence[XbrlFact], as_of: date
) -> float | None:
    """NOPAT over invested capital, or ``None``.

    Invested capital is equity plus interest-bearing debt less cash —
    the capital the business actually put to work, rather than the
    accounting total that counts a cash pile as though it were a plant.
    A company whose invested capital computes to zero or below returns
    ``None`` rather than an enormous number: that is a balance sheet the
    ratio does not describe, not an infinitely profitable one.
    """
    ebit = _ttm(facts, "operating_income", as_of)
    equity = _instant(facts, "total_equity", as_of)
    debt = _instant(facts, "long_term_debt", as_of)
    cash = _instant(facts, "cash_and_equivalents", as_of)
    if ebit is None or equity is None:
        return None
    invested = equity + (debt or 0.0) - (cash or 0.0)
    if invested <= 0:
        return None
    return (ebit * (1.0 - effective_tax_rate(facts, as_of))) / invested


def f_score(facts: Sequence[XbrlFact], as_of: date) -> int | None:
    """Piotroski's nine, or ``None`` when a component cannot be read.

    Every input is taken twice, a year apart, with the prior figure
    bounded by its own ``as_of`` so it is what a reader knew then rather
    than what was later restated.
    """
    prior_as_of = as_of - timedelta(days=ONE_YEAR_DAYS)

    net_income = _ttm(facts, "net_income", as_of)
    cfo = _ttm(facts, "operating_cash_flow", as_of)
    revenue = _ttm(facts, "revenue", as_of)
    revenue_prior = _ttm(facts, "revenue", prior_as_of)
    gross = _ttm(facts, "gross_profit", as_of)
    gross_prior = _ttm(facts, "gross_profit", prior_as_of)
    net_income_prior = _ttm(facts, "net_income", prior_as_of)

    assets = _instant(facts, "total_assets", as_of)
    assets_prior = _instant(facts, "total_assets", prior_as_of)
    ltd = _instant(facts, "long_term_debt", as_of)
    ltd_prior = _instant(facts, "long_term_debt", prior_as_of)
    ca = _instant(facts, "current_assets", as_of)
    cl = _instant(facts, "current_liabilities", as_of)
    ca_prior = _instant(facts, "current_assets", prior_as_of)
    cl_prior = _instant(facts, "current_liabilities", prior_as_of)
    shares = _instant(facts, "shares_outstanding", as_of)
    shares_prior = _instant(facts, "shares_outstanding", prior_as_of)

    gross_margin = _ratio(gross, revenue)
    gross_margin_prior = _ratio(gross_prior, revenue_prior)
    turnover = _ratio(revenue, assets)
    turnover_prior = _ratio(revenue_prior, assets_prior)
    roa_prior = _ratio(net_income_prior, assets_prior)
    current_ratio = _ratio(ca, cl)
    current_ratio_prior = _ratio(ca_prior, cl_prior)

    required = (
        net_income,
        cfo,
        assets,
        assets_prior,
        current_ratio,
        current_ratio_prior,
        shares,
        shares_prior,
        gross_margin,
        gross_margin_prior,
        turnover,
        turnover_prior,
        roa_prior,
    )
    if any(v is None for v in required):
        return None

    score, _breakdown = piotroski_f_score(
        PiotroskiInputs(
            net_income=net_income,  # type: ignore[arg-type]
            operating_cash_flow=cfo,  # type: ignore[arg-type]
            total_assets_current=assets,  # type: ignore[arg-type]
            total_assets_prior=assets_prior,  # type: ignore[arg-type]
            # Debt-free filers stop tagging the concept entirely, and
            # the criterion asks whether leverage FELL. Reading both
            # sides as zero keeps a company that has never borrowed from
            # failing a leverage test it cannot fail.
            long_term_debt_current=ltd or 0.0,
            long_term_debt_prior=ltd_prior or 0.0,
            current_ratio_current=current_ratio,  # type: ignore[arg-type]
            current_ratio_prior=current_ratio_prior,  # type: ignore[arg-type]
            shares_outstanding_current=shares,  # type: ignore[arg-type]
            shares_outstanding_prior=shares_prior,  # type: ignore[arg-type]
            gross_margin_current=gross_margin,  # type: ignore[arg-type]
            gross_margin_prior=gross_margin_prior,  # type: ignore[arg-type]
            asset_turnover_current=turnover,  # type: ignore[arg-type]
            asset_turnover_prior=turnover_prior,  # type: ignore[arg-type]
            return_on_assets_prior=roa_prior,  # type: ignore[arg-type]
        )
    )
    return score


def shares_cagr(facts: Sequence[XbrlFact], as_of: date) -> float | None:
    """Compound annual growth in the share count over three years.

    Negative for a buyback, which Gate C must not penalise.
    """
    now = _instant(facts, "shares_outstanding", as_of)
    then = _instant(
        facts, "shares_outstanding", as_of - timedelta(days=THREE_YEARS_DAYS)
    )
    if now is None or then is None or then <= 0 or now <= 0:
        return None
    return float((now / then) ** (1.0 / 3.0)) - 1.0


@dataclass
class Assembled:
    """One company, in the three shapes the doctrine's modules read."""

    ticker: str
    universe: UniverseInputs
    financials: Financials
    rank: RankInputs


def assemble_one(
    ticker: str,
    as_of: date,
    *,
    facts: Sequence[XbrlFact],
    price: float | None,
    median_dollar_volume: float | None,
    momentum_12_1: float | None,
    ten_year_yield: float | None = None,
    insider_cluster: bool = False,
) -> Assembled:
    """Build every value object for one company.

    Args:
        facts: The company's cached XBRL facts, loaded once by the
            caller so a universe sweep reads each parquet a single time.
        price: Last close at ``as_of``.
        median_dollar_volume: 63-session median, for U5.
        momentum_12_1: Total return from t-252 to t-21, for the rank's
            M component.
        ten_year_yield: DGS10 as a decimal, for Gate A's rate guard.
        insider_cluster: Section 3's tiebreak.
    """
    shares = _instant(facts, "shares_outstanding", as_of)
    market_cap = None if (price is None or shares is None) else price * shares

    cash = _instant(facts, "cash_and_equivalents", as_of)
    sti = _instant(facts, "short_term_investments", as_of)
    debt = _instant(facts, "long_term_debt", as_of)
    equity = _instant(facts, "total_equity", as_of)
    goodwill = _instant(facts, "goodwill", as_of)
    intangibles = _instant(facts, "intangible_assets", as_of)
    assets = _instant(facts, "total_assets", as_of)

    # A filer that tags neither goodwill nor intangibles plausibly
    # carries neither, so absence reads as zero here -- but only once
    # equity itself is readable, which is the thing that would otherwise
    # be invented.
    tangible_book = (
        None
        if equity is None
        else equity - (goodwill or 0.0) - (intangibles or 0.0)
    )

    net_cash = (
        None if cash is None else cash + (sti or 0.0) - (debt or 0.0)
    )
    net_debt = None if net_cash is None else -net_cash

    ebit = _ttm(facts, "operating_income", as_of)
    cfo = _ttm(facts, "operating_cash_flow", as_of)
    capex = _ttm(facts, "capex", as_of)
    net_income = _ttm(facts, "net_income", as_of)
    # Capex is tagged as a positive outflow, so free cash flow subtracts
    # it. Absent capex is not read as zero: a company that reports no
    # capital spending at all is more likely unreadable than assetless.
    fcf = None if (cfo is None or capex is None) else cfo - capex

    enterprise_value = (
        None if (market_cap is None or net_cash is None) else market_cap - net_cash
    )

    latest_filing = max((f.filed for f in facts if f.filed <= as_of), default=None)
    quarters = len(
        {
            (f.period_start, f.period_end)
            for f in facts
            if f.filed <= as_of and f.period_start is not None
        }
    )
    files_10k = any(f.form == "10-K" and f.filed <= as_of for f in facts)

    sic = sic_for(ticker)

    universe = UniverseInputs(
        ticker=ticker,
        major_us_listing=is_major_us_listing(ticker),
        files_10k=files_10k or None,
        quarters_of_fundamentals=quarters or None,
        latest_filing=latest_filing,
        price=price,
        median_dollar_volume_63d=median_dollar_volume,
        sic=sic,
        market_cap=market_cap,
        in_sp500=in_sp500_on(ticker, as_of),
    )

    financials = Financials(
        ticker=ticker,
        market_cap=market_cap,
        enterprise_value=enterprise_value,
        ebit_ttm=ebit,
        cfo_ttm=cfo,
        fcf_ttm=fcf,
        net_income_ttm=net_income,
        total_assets=assets,
        tangible_book=tangible_book,
        net_cash=net_cash,
        net_debt=net_debt,
        shares_cagr_3y=shares_cagr(facts, as_of),
        ten_year_yield=ten_year_yield,
    )

    rank = RankInputs(
        ticker=ticker,
        ebit_to_ev=_ratio(ebit, enterprise_value),
        fcf_to_ev=_ratio(fcf, enterprise_value),
        net_cash_to_market_cap=_ratio(net_cash, market_cap),
        roic=return_on_invested_capital(facts, as_of),
        f_score=f_score(facts, as_of),
        momentum_12_1=momentum_12_1,
        sic2=industry_for(ticker),
        insider_cluster=insider_cluster,
    )

    return Assembled(
        ticker=ticker, universe=universe, financials=financials, rank=rank
    )


def assemble_universe(
    tickers: Sequence[str],
    as_of: date,
    *,
    cache: EdgarCache,
    prices: dict[str, float | None],
    dollar_volumes: dict[str, float],
    momentum: dict[str, float | None],
    ten_year_yield: float | None = None,
) -> list[Assembled]:
    """Assemble every company the caller hands over.

    A ticker whose facts cannot be read is skipped rather than carried
    as an empty shell: it would fail every gate anyway, and counting it
    in the universe would make the section-1 health check read a data
    outage as a shrinking market.
    """
    out: list[Assembled] = []
    unreadable = 0
    for ticker in tickers:
        try:
            facts = cache.load_facts(ticker)
        except Exception as exc:
            logger.debug(f"{ticker}: facts unreadable — {exc}")
            unreadable += 1
            continue
        if not facts:
            unreadable += 1
            continue
        out.append(
            assemble_one(
                ticker,
                as_of,
                facts=facts,
                price=prices.get(ticker),
                median_dollar_volume=dollar_volumes.get(ticker),
                momentum_12_1=momentum.get(ticker),
                ten_year_yield=ten_year_yield,
            )
        )
    if unreadable:
        logger.info(
            f"{as_of}: {unreadable} of {len(tickers)} tickers had no readable "
            "facts and were left out of the universe"
        )
    return out
