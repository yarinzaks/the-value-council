# Backtest Engine — Architecture & Decisions

> Production-quality backtesting for The Value Council. This document
> records the architectural decisions and the trade-offs behind them
> so future readers (and future agents) can extend the engine without
> guessing at intent.

**Last updated:** 2026-04-27 (initial build)

---

## Goals & non-goals

### Goals
- **Point-in-time correctness.** When the engine asks "what was AAPL's
  EPS on 2015-03-15?", it returns the value as it was reported on that
  date — not a value that became known later through restatement.
- **Survivorship-bias-free.** Backtests over 2008-2010 must include
  Lehman, WaMu, Bear Stearns, etc. — companies that no longer exist.
- **Realistic frictions.** Transaction costs default to 10 bps per
  side; configurable to zero for theoretical comparison.
- **Reproducibility.** Re-running the same backtest with the same
  inputs produces identical results.
- **Extensibility.** A `Strategy` abstract base class so each Council
  member's playbook can be implemented as a plug-in.

### Non-goals (v1)
- **Bid-ask slippage modeling.** We do not have intraday quote data
  on the free tier. We assume execution at the day's close.
- **Tax modeling.** Paper portfolio operating in pre-tax terms.
- **Currency conversion.** US-only universe in v1; Israel/TASE later.
- **Intra-day execution.** Daily granularity only.
- **Borrowing / margin / shorting.** Long-only, cash-funded.
- **Corporate actions beyond splits/dividends.** yfinance handles
  these via adjusted close; we do not build an independent corporate
  actions database.

---

## Module map

```
core/backtest/
├── __init__.py
├── transaction_costs.py   # Cost models (per-share, per-trade, %)
├── metrics.py             # CAGR, Sharpe, Sortino, MDD, hit rate, IR
├── universe.py            # Historical S&P 500 constituents
├── data_loader.py         # Price data + SQLite cache
├── point_in_time.py       # EDGAR filing-date-aware financials
├── portfolio.py           # NAV-tracking backtest portfolio
├── strategy_runner.py     # Strategy ABC + execution loop
└── reporting.py           # CSV, matplotlib, human summary
```

---

## Key design decisions

### D1. Historical S&P 500 constituents — Wikipedia

**Decision:** Source historical S&P 500 membership from two Wikipedia
pages parsed via `pandas.read_html`:
- `https://en.wikipedia.org/wiki/List_of_S%26P_500_companies` — current
  constituents and the change log table
- The change log lists every addition/removal with date, ticker, and
  the company being replaced

We reconstruct membership at any historical date by starting from the
current list and walking the change log backward.

**Why not** a paid CRSP or S&P licensed dataset? Out of scope for free
tier. **Why not** scraping multiple sources? Wikipedia is curated, well
maintained, and the change log is structured. The known limitation:
Wikipedia's change log goes back to roughly 2000; for backtests prior
to that we will need a different source (deferred).

**Cache:** Parsed constituents serialized to
`data/cache/sp500_constituents.json` keyed by month-end date.

### D2. Survivorship bias — yfinance for delisted tickers

**Decision:** Use yfinance for both live and delisted tickers. yfinance
retains historical prices for many delisted/acquired companies (LEH,
WM, BSC, etc.). When a ticker has no data for a date, the engine logs
a warning and treats the position as exited at the last available
price — which is the realistic outcome of a delisting / bankruptcy
event for a long-only investor.

**Known limitation:** Some tickers reused after delisting (e.g., a new
company taking the WM ticker) can produce data confusion. We mitigate
by anchoring lookups to a ticker's CIK where possible.

### D3. Point-in-time financials — EDGAR filing dates

**Decision:** Use `edgartools` to fetch 10-K and 10-Q filings. For each
`(ticker, as_of_date)` query:
1. List all filings for that ticker with form ∈ {10-K, 10-Q}.
2. Filter to those whose `filing_date <= as_of_date`.
3. Take the most recent.
4. Parse standardized financials via `edgartools.Financials`.

This guarantees the agent only sees data that was publicly known on
`as_of_date`. Restatements that happened later are ignored (as
intended).

**Cache:** Filing data cached in `data/cache/edgar_filings.sqlite`
keyed by `(ticker, accession_number)`.

**Known limitation:** Some smaller companies have sparse XBRL data
or non-standard tagging. We surface what edgartools can extract and
return `None` for missing fields. The portfolio runner is required
to handle missing data gracefully (skip the candidate or fall back).

### D4. Price data — yfinance + SQLite

**Decision:** All daily price/volume data flows through yfinance,
cached in `data/cache/prices.sqlite`. Schema: `(ticker, date, open,
high, low, close, adj_close, volume)`. Writes are upserts.

**Why SQLite over Parquet?** SQLite handles the (ticker, date) primary
key with no extra ceremony; simpler queries, no schema-migration
headaches at our scale (~5M rows for 1500 tickers × 15 years).

**Use adj_close for return calculations.** yfinance's adjusted close
already incorporates splits and dividends, so we compound returns
without separate corporate-action handling.

### D5. Transaction costs — 10 bps default, configurable

**Decision:** Default cost model is `PercentageCost(0.001)` — 10 basis
points per side. Applied at order execution, deducted from cash for
buys (cost = shares × price × (1 + bps)) and from proceeds for sells
(net = shares × price × (1 - bps)).

**Two additional models** for sensitivity testing:
- `ZeroCost()` — theoretical baseline
- `PerShareCost(c)` — flat $0.005/share, e.g. for IBKR-style accounts

The runner accepts any `CostModel` ABC implementor.

### D6. Strategy interface — `Strategy` ABC

**Decision:** Every strategy implements:

```python
class Strategy(ABC):
    @abstractmethod
    def select(
        self,
        as_of: date,
        universe: list[str],
        prices: PriceLookup,
        fundamentals: FundamentalsLookup,
    ) -> dict[str, float]:
        """Return target weights {ticker: weight} where weights sum to ≤ 1.
        Cash is implicit (1 - sum(weights))."""
```

Two reference implementations included:
- `BuyAndHoldSPY` — sanity check; always 100% SPY
- `EqualWeightUniverse` — 1/N across the current universe; rebalanced
  monthly

Each Council member's playbook will eventually be a `Strategy`
subclass; that work is out of scope for this build.

### D7. Rebalancing cadence — monthly by default

**Decision:** The runner ticks at month-ends (last trading day of each
month). At each tick: ask the strategy for target weights, compute
current weights from current prices, generate orders to close the gap,
execute orders applying transaction costs, snapshot NAV.

**Why monthly?** Matches the cadence of fundamental data updates
(quarterly filings smoothed). Daily rebalancing produces overfitted
signals on a free-tier data stack; monthly is the realistic frequency
for a value investor.

Configurable via `RunnerConfig.rebalance_freq` to weekly or quarterly.

### D8. Storage layout

```
data/
├── cache/
│   ├── prices.sqlite              # all OHLCV + adj_close
│   ├── edgar_filings.sqlite       # parsed financials
│   ├── sp500_constituents.json    # universe snapshots
│   └── universe_changes.json      # raw change log
└── backtest_results/
    ├── <run_id>/
    │   ├── nav.csv                # daily NAV time series
    │   ├── orders.csv             # every executed trade
    │   ├── annual_returns.csv     # year-by-year returns
    │   ├── summary.json           # all metrics
    │   ├── summary.txt            # human-readable
    │   └── drawdown.png           # matplotlib chart
```

### D9. Error handling philosophy

- **Missing prices on a rebalance day** → log warning, treat affected
  position as held flat (no rebalance).
- **Missing fundamentals for a candidate** → strategy decides
  (skip/fallback). The engine does not silently substitute zeros.
- **Universe mismatch** (ticker requested but not in universe at
  as_of_date) → raise `UniverseError`. The strategy is responsible for
  asking only for valid tickers.
- **Network failures** → tenacity retries (already configured at the
  data-source layer).

### D10. Testing strategy

Each module ships with pytest tests. Two are mandatory beyond unit
tests:

**Point-in-time correctness test.** Mock the EDGAR adapter to return
two filings: one filed 2015-01-30 (Q4 2014 10-K), one filed 2015-04-25
(Q1 2015 10-Q). Query as of 2015-03-15. Assert we receive the Q4 2014
data, not the Q1 2015 data that wasn't yet public.

**Survivorship test.** Build a universe at 2008-09-30 and assert
Lehman Brothers (LEH) is in it; build a universe at 2008-12-31 and
assert it is not.

**Sanity test.** Buy-and-hold SPY 2010-01-04 to 2024-12-31. Compare
to actual SPY total return over the same period (~470% per public
data sources). Engine result must be within 0.5% of the actual return.

---

## Open trade-offs (deferred)

- **Wikipedia constituent gaps.** The change log is mostly complete
  post-2000 but has occasional missing entries. We log gaps but do
  not attempt to fix them in v1.
- **Dividend tax treatment.** Adjusted close already reflects net
  dividends; we do not separately model dividend income or tax
  withholding.
- **Dropped tickers in cache.** When a company is acquired, its CIK
  may be retired. We keep the cached data forever — it never becomes
  invalid — but new lookups against the dead ticker fail. Acceptable.

---

## Versioning

This is **v1** of the backtest engine. v2 will add:
- Israel TASE universe
- Slippage modeling (when bid-ask data becomes available)
- Multi-strategy ensembles (running all 10 Council members
  simultaneously and tracking each)
