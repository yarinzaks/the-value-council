# The Council — selection-engine audit

Audit of the four doctrine documents in `~/Documents/Fintest/`
(`THE_VALUE_COUNCIL.md`, `COUNCIL_DATA.md`, `COUNCIL_RUNBOOK.md`,
`COUNCIL_SELECTION.md`) against this repository, 2026-08-14.

Method: five independent audit passes, each finding then handed to an
adversarial verifier instructed to refute it. 13 findings survived. Every
code claim below cites file:line and was read, not inferred. Claims marked
*(estimate)* are not.

**Verdict.** `COUNCIL_SELECTION.md` and the shipped `agents/council/selection.py`
are not two versions of one strategy — they are opposite selection rules that
share a name. Nothing in the selection path survives. The risk machinery
(regime, limits, journal, events) survives almost intact, but it is wired to
the runs that do not trade and disconnected from the one that does.

---

## (A) Contradictions inside the documents — these need a decision

### A1 — BLOCKER. The CLOSE schema's `implied_weights` contradicts the table it cites

`COUNCIL_SELECTION.md` §9.1 line 290: *"This is the table behind
`implied_weights` in the runbook's CLOSE schema."*

At `risk_on_count = 3` the §9.1 table gives statistical **45%**, event **15%**,
cash floor **5%**. `COUNCIL_RUNBOOK.md:126`, same `risk_on_count: 3`, emits
`{"compounder":0.60,"value":0.15,"event":0.20,"cash":0.05}`.

The sleeve names disagree entirely: SELECTION uses statistical / event / core /
cash (§ lines 23-28); the runbook uses compounder / value / event / cash.
`compounder` as a sleeve name occurs **only** at `COUNCIL_RUNBOOK.md:126` across
all four documents. The runbook's event weight (0.20) exceeds §9.1's event
ceiling in **every** regime row.

This is the only wire between the regime dial and a run that emits output, and
it is crossed. The false claim of consistency at line 290 is worse than a silent
gap — it discourages checking.

### A2 — BLOCKER. The Chair's "STATISTICAL at 5%" verdict maps to no sleeve that can accept it

`THE_VALUE_COUNCIL.md:256` (repeated `:445`, operationalised at
`COUNCIL_RUNBOOK.md:253-255`): *"If it clears five of six, it is a Statistical
position at 5%… There is no partial credit."*

But `COUNCIL_SELECTION.md:25` says the Statistical sleeve is picked *"by this
file, mechanically — no LLM judgment"*; §5 sizes every statistical name at
sleeve/20 (2.25%); §6 E8 gives its only exit as `rank_buffer: 40`; and §6's
invariant (lines 17-19, restated 205) requires execution to **reject a BUY
ticket whose exit block does not validate for its sleeve**.

So the Council's most common non-PASS outcome produces a ticket that is
judgment-picked in a judgment-free sleeve, sized at 5% where the formula says
2.25%, requiring a rank-buffer exit for a name that may not be in the top 40 —
or may fail Gate U6 entirely (doctrine `:48` lets the Council buy a financial;
§1 U6 forbids the machine from doing so). Every such ticket is refused by §6's
own invariant. **The doctrine's stated fallback path is unexecutable.**

Second-order: sleeve/20 at the 45% ceiling is 2.25%, and less at every lower
regime ceiling, so §5's headline 5% statistical cap can never bind.

### A3 — BLOCKER. Three places still put a human on the trigger

- `COUNCIL_RUNBOOK.md:38`, system prompt rule 5, prepended to **every** run:
  *"You never move real money. You produce proposals. A human executes."*
- `THE_VALUE_COUNCIL.md:271`, Part 4 floor table: *"−25% from peak: no new
  positions until a human reviews."*
- Against `COUNCIL_SELECTION.md` §6 E1: *"circuit breaker active (−25% from
  peak) | no buys of any kind; exits below still run"* — machine-checkable.

`THE_VALUE_COUNCIL.md:464` (Part 10) already scopes its human gate correctly —
*"Real money stays behind a **real money** rule"* — and does not reach a paper
demo. Rule 5 and the Part 4 breaker row carry no such scoping. In an autonomous
paper configuration a −25% drawdown means the agent waits for a reviewer who
does not exist, and rule 5 in the always-prepended prompt is the single most
likely cause of a run refusing to act at all.

### A4 — MAJOR. The daily run points at a section that does not contain the dial

`COUNCIL_RUNBOOK.md:96`: *"Read the regime dial (Part 3): the four FRED
series."* Part 3 of the doctrine (`THE_VALUE_COUNCIL.md:166-202`) is "The
hunting grounds". `THE_VALUE_COUNCIL.md` contains **zero** occurrences of
"regime", "dial", "FRED", "risk_on", or any of the four series names. The series
live in `COUNCIL_DATA.md:193-200`; the sleeve ceilings live only in
`COUNCIL_SELECTION.md:281-287`.

### A5 — MAJOR. §8's self-check forbids the strategy currently shipping

`COUNCIL_SELECTION.md` §8: *"a proposed trade that cannot be tied to one of its
three edges — forced selling, complexity, time — belongs to one of the other
eleven, not to this one."*

`agents/council/selection.py:17-18` states the inverse: *"it owns no opinion
about a company that its members have not already formed."* A rule that buys
only what three of the other eleven already hold selects, by construction,
**exclusively** for trades §8 assigns to the other eleven.

### A6 — MINOR. Cash floor: constant vs regime-dependent

Part 4 says a flat 5%; §9.1 says 5 / 5 / 10 / 15 / 20 by `risk_on_count`.
Reconcilable (§9.1: "cash floors are reached by not buying, never by forced
selling"), but `limits.py:50 MIN_CASH = 0.05` implements Part 4's reading alone.

### A7 — MINOR (docs already mostly resolve this). Punch-card scope

Part 7 already exempts the mechanical sleeve. §4 already exempts Event ("no
punch-card cost") but is **silent on the Statistical row**. One sentence closes
it. The code half is a real defect — see E4.

### A8 — MINOR. E6's trim target

§6 E6 says trim to **25%**; Part 4 says only "trim above 35%" without a target.
§6 is the more specific rule and the doctrine should be amended to match.

---

## (B) What the documents require that this repo cannot compute today

### B0 — BLOCKER (verified first-hand). The fundamentals data is frozen at April 2026

`edgar_filings.sqlite` holds 245,934 filings across 6,017 tickers, but monthly
counts collapse after April:

| Month | Filings |
|---|---|
| 2026-02 | 2,809 |
| 2026-03 | 2,337 |
| 2026-04 | 693 |
| 2026-05 | 70 |
| 2026-06 | 0 |
| 2026-07 | 4 |
| 2026-08 | 1 |

All 8,290 parquet files in `fundamentals_cache/` share a single mtime of
2026-04-28. Prices are current to 2026-08-07.

A universe-wide screen run today would rank on Q1-2026-and-earlier fundamentals
against August prices. **Root cause not yet determined** — it may be that only
the ~250 tickers in the live working set are ever queried, so the rest never
refresh. Either way one prefetch sweep is required before any gate or rank can
be trusted, and the staleness must be an explicit health check, not a silent
default.

### B1 — BLOCKER. There is no TTM anywhere, and Gates A/B/C and the rank are all TTM-denominated

`core/data/fundamentals_fetcher.py:164-176` puts revenue, net_income,
operating_income, operating_cash_flow and capex in `_FLOW_CONCEPTS`, and
`:183 ANNUAL_DURATION_DAYS = (330, 400)` filters flows to **annual periods
only**. There is no quarterly-sum assembler and no Q4 derivation (`FY − 9M`,
`COUNCIL_DATA.md` trap #5) anywhere in `core/data/` or `core/research/`.

Blocked: `EV/EBIT ≤ 8 with EBIT_TTM > 0`; `net debt / EBIT_TTM ≤ 3`; `FCF_TTM`;
`CFO_TTM > 0`; `(NI_TTM − CFO_TTM)/assets`; `pct(EBIT/EV)` and `pct(FCF/EV)` in
the §3 rank.

Substituting the latest annual figure runs up to ~15 months stale under
`MAX_FACT_AGE_DAYS = 550` (`fundamentals_fetcher.py:191`) — precisely the
failure `core/research/factors.py:41-59` documents as having produced
meaningless 22.81% / 23.88% holdout numbers.

**Work:** a TTM layer over the existing concept chains — four trailing quarters,
Q4 derived only when FY and 9M share a fiscal-year start, 53-week years
tolerated. ~1-2 days plus a fixture suite. Highest-leverage missing piece.

### B2 — BLOCKER. `net cash` is systematically understated

`CONCEPT_MAP` (`fundamentals_fetcher.py:57-140`) maps `cash_and_equivalents`
(three tags) but has **no** entry for short-term investments. Gate A path 2 is
`cash + short-term investments − total debt ≥ 25% of mcap`, and net-cash-rich
small caps park most of the balance in `ShortTermInvestments` /
`AvailableForSaleSecuritiesDebtSecuritiesCurrent` / `MarketableSecuritiesCurrent`.

`_compute_total_debt` (`:333-383`) already handles `COUNCIL_DATA.md` trap #4
correctly (AAPL cross-validated to 84.297bn). **Work: one new concept chain.**

### B3 — BLOCKER. U8 (not an S&P 500 member) has no data source here

`COUNCIL_DATA.md` Tier 0 names two files; neither exists in this repo. Only
`data_bundled/company_tickers.json` (10,398 entries — `cik_str`, `ticker`,
`title`) and `data_bundled/company_sic.json` are present. The only S&P-500
artefacts are hardcoded validation lists
(`agents/greenblatt/run_validation.py:53`). **Work: bundle one CSV, ~1 hour.**
It is also the only free survivorship-bias fix `COUNCIL_DATA.md` names.

### B4 — MAJOR. U1 (NYSE/NASDAQ/AMEX common stock) has no exchange field

`company_tickers.json` carries no exchange. `core/data/ticker_filter.py` gives
share-class heuristics and a baby-bond deny list — a good filter, but not an
exchange test, and it will not exclude OTC. **Work: switch to SEC's
`company_tickers_exchange.json`, join on CIK.**

### B5 — MAJOR. U6 fails ~20% of the universe as UNKNOWN

`data_bundled/company_sic.json` holds 8,290 entries (120 null) against 10,398
tickers, so ~2,200 names have no SIC. Under §2's rule ("a gate that cannot be
computed FAILS") they drop silently, and §3's sector cap cannot place them.
`core/data/edgar_facts.py:264 get_sic_for_ticker` can backfill;
`core/data/sic_codes.py:46,:51` is the right consumer API and is **imported by
nothing in `agents/council/`**.

### B6 — MAJOR. Gate D is a quarter implemented and wholly mis-scoped

- *8-K 4.02 within 24 months*: `agents/council/events.py:74-100` has the item map
  (4.02, 1.03, 3.01, 2.01, 4.01, 2.06, 2.02) and `TERMINAL_FORMS` (25, 25-NSE,
  15-12B, 15-12G). But `DEFAULT_LOOKBACK_DAYS = 10` (`:57`) and `scan()` is
  called only on **held** tickers. Pre-trade Gate D needs 24 months over ~2,000
  candidates — ~2,000 `submissions.json` fetches at ≤8 req/s, roughly 4 minutes
  per sweep, cacheable.
- *going-concern language* and *material weakness*: **not implemented at all.**
  Both need 10-K text; `core/data/edgar_source.py:106 get_latest_10k` returns a
  `FilingExcerpt`, which is a starting point, not a detector.
- *NT 10-K / NT 10-Q within 12 months*: not implemented, but trivial — the forms
  are in the submissions stream `events.py` already parses.

### B7 — MAJOR. The §3 rank needs a cross-sectional panel; the live path is per-ticker

`core/live/runner.py:_load_fundamentals` builds a `{ticker: Fundamentals}` dict
of vendor-shaped scalars. The panel machinery is research-time only
(`core/research/fundamentals_panel.py`, `ProcessPoolExecutor`), and its `FIELDS`
(lines 59-71) omit goodwill, intangibles, current assets/liabilities and gross
profit. Component by component:

| Component | State |
|---|---|
| **V** | blocked on B1/B2. `factors.py:168 enterprise_value` and `:152 market_capitalisation` are correct and reusable |
| **Q — ROIC** | not computable from EDGAR here. No NOPAT / invested-capital derivation. `Fundamentals.roic` is populated only by vendor TTM (`fmp_source.py:104`) — neither point-in-time nor free-tier |
| **Q — F-score** | `core/scoring/piotroski.py:38` is clean, but needs `gross_margin_current/prior` and `asset_turnover`, and **`gross_profit` has no `CONCEPT_MAP` entry**. Two of nine criteria uncomputable without a new chain |
| **M** | available and correct — `core/backtest/data_loader.py:613 trailing_return(lookback_months=12, skip_months=1)` is exactly t−252→t−21 |
| **M hygiene** | `core/research/splits.py` self-computes split factors (the 224× fix), but the live path uses `get_adj_close` (yfinance, vendor-adjusted), not the self-computed factors Tier 2 prescribes. Partial |
| **Insider tiebreak** | `edgar_source.py:114 get_form4_insider_transactions` exists, but `InsiderTransaction` (`models.py:127-138`) has **no 10b5-1 flag and no open-market distinction**; grep for `10b5` returns nothing repo-wide. Not computable as written |

### B8 — MAJOR. The whole Event sleeve is uncomputable today

**ATR(14)** — required by E3, §9.4 and Part 6's stop table. Grep for `atr` /
`true_range`: **zero implementations.** Small to build from OHLC.
**§9.3 SUE** — needs 12 clean quarters of revenue; blocked on B1.
**Form 10** — not in `events.py`'s form map; it is in the submissions stream.
**Index deletion**, **post-bankruptcy emergence** — no data source.
**December tax-loss** — computable from prices today.

### B9 — Universe size sanity check *(estimate)*

§1 predicts ~2,000 names and calls the count itself a health check.
`FullMarketUniverse.constituents_at` already implements U2/U3 and the runner
logs its size daily (`core/live/runner.py:542`). Measured coverage on disk:
6,601 tickers in the universe index, 6,017 with filings, 5,651 with OHLCV,
8,290 with cached XBRL facts. Run the gates once and compare against ~2,000
before trusting any of them.

---

## (C) Existing code that `COUNCIL_SELECTION.md` supersedes — delete

1. **`agents/council/selection.py` — the entire file (250 lines).** Its rule is
   agreement-of-three (`:59 MIN_AGREEMENT = 3`, `:23`, `:187-191`), which §1-§5
   replace end to end and §8 explicitly excludes. Also dead: `agreement()`,
   the candidate sort, `MAX_ENTRIES_PER_RUN = 2` (`:72` — incompatible with a
   20-name sleeve; filling it would take ten runs), and
   `each = min(MAX_POSITION_AT_ENTRY, investable / len(book))` (`:242`).
2. **`news_veto()` and `CRITICAL_HEADLINE_TERMS`** (`selection.py:81-93,
   135-147`). §10 forbids exactly this: *"Sentiment / news-flow scores. Nothing
   in the research supports them, and the 8-K stream is already the clean event
   feed."* `COUNCIL_DATA.md` repeats it. Check other adapters before deleting
   `_news_service()` at `core/live/runner.py:288+`.
3. **`agents/council/tests/test_selection.py` — all 234 lines.**
4. **`agents/council/strategy.py:42-65 read_books()`**, the `books_reader`
   constructor arg (`:86`), `books = self.books_reader()` (`:109`), and the
   import at `:29` — the entire books-off-disk input path.
5. **`core/live/agent_adapter.py:616-632 _agreement_counts`**,
   `CouncilLive.entry_trigger` (`:556`), and the hardcoded rationale strings at
   `:570-578` and `:596-604`.
6. **`core/live/runner.py:275-280`** — the "Last on purpose. It decides on what
   the others hold" comment and the ordering it justifies.
7. **`limits.py:47 ILLIQUID_ADV_USD = 5_000_000.0`** and the ADV-class logic in
   `check_illiquid` (`:172-198`). §9.6 replaces it: *"'Illiquid' is not an ADV
   class; it is days to exit."* On a $10k book a 2.25% position is $225 and
   clears a $600k-ADV name in one session; the current constant calls that name
   illiquid and caps the aggregate at 20%.
8. **`limits.py:122 note="trim to 35%"`** → E6 says trim to **25%**.
9. **Stale docstrings that now assert the opposite of what runs:**
   `scripts/run_council.py:9-14` (*"stays in cash until a human approves a
   position"*) and `agents/council/__init__.py:28-30` (*"Nothing in this package
   trades"*). Both contradicted by `core/live/runner.py:281-286`, which registers
   `CouncilLive` as the twelfth trading adapter.
10. **`dashboard/src/lib/agents.ts:169-176`** — the description sells vetoes
    only. Under §8 it should be the one sentence at `COUNCIL_SELECTION.md` §8.
    Cosmetic; last.

---

## (D) Existing code that survives unchanged — do not rebuild

**`agents/council/regime.py` (354 lines) — survives whole.** Four FRED series,
`Regime.risk_on_count` (`:121`), unreadable never counted as risk-on. Direct
input to §9.1; needs only a consumer.

**`agents/council/journal.py` (376 lines) — survives whole.** `Thesis` refuses
to construct without three kill criteria (`:58`, `:113-119`); `punches_used()`
is derived, never stored (`:247-253`), and already counts **only**
`Classification.UNDERSTANDING` — the code already implements the punch-card
scoping A7 asks the docs to state. `calibrate`/`shrink` is the Brier plus
shrinkage arithmetic Seat 6 sizing needs. Two additive changes: an `EVENT`
member on `Classification` (`:65-67`), and an `exit` block field per §6's JSON.

**`agents/council/events.py` (360 lines) — survives whole; it is the E2 engine.**
Additive only: Form 10, NT 10-K/NT 10-Q, and a longer lookback for Gate D.

**`agents/council/limits.py` — structure and six of seven numbers survive.**
`LimitCheck`/`LimitState` with UNKNOWN never silently a pass matches §2's
"UNKNOWN is not a pass". Verified against Part 4: `:33` 0.25 ✅, `:37` 0.35 ✅,
`:41` 0.45 ✅, `:44` 0.20 ✅, `:50` 0.05 ✅ (see A6), `:55` −0.25 ✅. Only `:47`
dies.

**`agents/council/runs.py`** — the `Book` snapshot, `write_result`, and the
heartbeat/close skeleton survive as the reporting layer.

**Data layer, all correct and reusable:**
- `core/backtest/data_loader.py:472 median_dollar_volume(sessions=63)` —
  **exactly** U5, median-not-mean rationale included.
- `:613 trailing_return` — exactly §3's M window. `:442 price_extremes`,
  `:292 dividends_between`, `:348 get_history` for ATR.
- `core/backtest/full_market_universe.py` — U2/U3, PIT membership, persisted index.
- `core/data/fundamentals_fetcher.py` — ordered concept chains resolved **per
  period** (trap #1), `filed`-not-`end` semantics, `_compute_total_debt`
  (trap #4, cross-validated), the flow/stock split, non-USD rejection at
  `:190-200`. The hardest part of Tier 1, and it is done.
- `core/research/splits.py` — `detect_splits`, `cumulative_factors`,
  `adjusted_shares`: the 224× fix, in the form §3 requires.
- `core/research/factors.py` — `market_capitalisation` (turnover-guarded),
  `enterprise_value` (`MIN_EV_TO_MARKET_CAP`), `MAX_FILING_STALENESS_DAYS = 400`
  (identical to U3), `_latest_on_or_before` backwards-only join.
- `core/scoring/piotroski.py`, `core/data/sic_codes.py:46,:51`,
  `core/data/edgar_facts.py:264`, `core/data/edgar_source.py:114`.

**`core/live/runner.py` execution machinery survives:** dividend settlement
before the diff (`:659-663`), refusal to sell at a stale mark (`:687-699`),
fractional sizing with `MIN_TRADE_USD = 1.0`, cost model, snapshots, watchlist.
**`core/backtest/strategy_runner.py:123-167 Strategy`** — the
`select(..., held=)` signature is sufficient to express every §6 exit. No
interface change needed there.

---

## (E) Architectural changes to the Strategy / runner rails

### E1 — BLOCKER. Forced exits lose to the 30-day holding floor

`core/live/runner.py:115 DEFAULT_MIN_HOLDING_DAYS = 30`, applied at `:682-686`
to **every** adapter with no carve-out. §6 E2 says *"sell next session — no
council, no discussion"*, and E3's trailing stop is equally time-critical. Today
an 8-K 4.02 on a 12-day-old position is ignored for 18 more days. The floor is
good policy — it was written against 246 round-trips of churn — so it needs an
exemption channel, not removal.
**Fix:** `ScanResult` carries `forced_exits: list[str]`, honoured before the age
check.

### E2 — BLOCKER. The filings veto is never supplied on the only path that trades

`core/live/runner.py:281-286` constructs `CouncilLive(TheCouncil(news_service=…,
regime_reader=read_regime))` — **no `filings_reader`**.
`agents/council/strategy.py:121-127` therefore leaves `flagged = {}` on every run
and passes an empty dict into `propose()` at `:148`. `events.scan` reaches
production only through `scripts/run_council.py --mode heartbeat|close`, and
both trade nothing (`runs.py:139 "forced_exits_proposed"`). Meanwhile the
dashboard rationale asserts *"filings and news all clear"*
(`agent_adapter.py:570-578`). Every filings veto in the package is dead code on
the live rails, with no human backstop.

### E3 — BLOCKER. The circuit breaker is not an input to the decision

`propose()` (`selection.py:150-160`) takes `as_of, books, held, risk_on_dials,
entries_remaining, filings_flagged, news_for, min_agreement` — **no nav, no
peak_nav, no drawdown, no breaker flag**. The breaker is evaluated only in
`runs.heartbeat` (`runs.py:105-107`), which returns a dict; peak NAV lives in
`data/council/state.json` via `run_council.py:_peak_nav`. The agent can be 30%
below peak and still open positions.
**Fix:** the new entry point takes `nav`/`peak_nav` (or a precomputed
`breaker_active: bool`) and returns zero entries while still emitting E2-E9 exits.

### E4 — BLOCKER. The punch card never decrements

`TheCouncil.__init__` takes `entries_used: int = 0` (`strategy.py:80`);
`runner.py:281-286` passes it nothing, so `entries_remaining` is a full 20 on
**every** run (`strategy.py:147`). `Journal.record` is called nowhere outside
tests; `Journal()` is instantiated in production only at
`scripts/run_council.py:145`. The lifetime ceiling is unenforced in both
directions.
**Fix:** derive from `Journal.punches_used()` at construction, and charge only
Core entries.

### E5 — BLOCKER. Going flat is inexpressible

`runner.py:662`: `if scan.targets:` guards the entire sell loop, so an empty
target dict reads as "no trade", not "liquidate". Under E1 (breaker) plus E2
(sell all) the Council can legitimately want 100% cash and cannot say so.
**Fix:** an explicit `flat_is_intentional` flag on `ScanResult`, or fold it into
E1's forced-exit channel.

### E6 — MAJOR. No run types on the only path that trades

`DailyRunner.run` (`runner.py:533`) is invoked per calendar day with no notion of
run type. Mapping against §7: HEARTBEAT → `runs.heartbeat` ✅; CLOSE →
`runs.close` + `run_mark_to_market` (`:434`) ✅; COUNCIL → `DailyRunner.run`, but
daily rather than weekly and with no cooling-off; READING, REVIEW, CALIBRATION →
**nothing**. So §7's monthly "publish the would-be list, trade nothing" has no
artefact, the Feb/May/Aug/Nov calendar does not exist, and E4's 5-day deadman and
E7's 8-REVIEW time stop are denominated in a run no code path produces. Part 7
calls the run-type table *"what stops a high-frequency schedule from becoming a
high-turnover strategy"* — and the one trading path has no run type.
**Fix:** a run-type parameter on `DailyRunner`, a rebalance-calendar predicate
from `as_of`, a persisted published-list artefact for the one-run-earlier rule,
and a REVIEW record type.

### E7 — MAJOR. Sizing is not sleeve-aware

`limits.entry_allowed(weight)` (`:262-271`) knows one cap, 25%. §5 needs
Statistical 2.25% entry / 5% cap, Event 3% / 4%, Core Kelly / 25% with a 35%→25%
trim. None of 0.05, 0.03, 0.04 appears in `limits.py`.
**Fix:** add `MAX_POSITION_STATISTICAL`, `EVENT_ENTRY_SIZE`,
`MAX_POSITION_EVENT`; make `entry_allowed` take a sleeve.

### E8 — MAJOR. The 45% cluster cap is unenforced and poisons the operator signal

`check_clusters` reads `p.cluster` and emits UNKNOWN for anything unlabelled
(`limits.py:127-170`). `Book.clusters` defaults to `{}` (`runs.py:69`) and
`scripts/run_council.py load_book` constructs `Book(nav, cash, peak_nav,
positions, adv)` — **never passing `clusters`**. Every position is unlabelled on
every real run, so `runs.py:127 all_clear = not breached and not flagged and not
unknown` can never be true once the book holds anything. A limit that always
answers UNKNOWN is not a limit, and a permanently noisy signal is how a real
breach gets ignored.
**Fix:** §3's own proxy — 2-digit SIC division via `core/data/sic_codes.py`, max
5 of 20 per division, with the 45% weight cap as the second test. Keep UNKNOWN
for genuinely unresolvable SICs.

### E9 — MAJOR. §6's exit invariant has no enforcement point

Grep of `agents/council/*.py` for `rank_buffer`, `trail_stop`, `fair_value`,
`punch_slot`: nothing outside `journal.py`'s thesis schema. `TheCouncil.select`
returns target weights and inspects no exit block. `kill_criteria` is written by
`journal.py` and consumed by nothing (`runs.py:110-120` renders it as
`"state": "NOT_EVALUATED"`). The repo's own history is the cost: `events.py:5-12`
records Thermon at a dead price for 70 days and ASGN/EFOR for 53, both found by
hand.
**Fix:** reject any target-weight *increase* for a ticker whose journal entry
lacks a sleeve-valid exit block, checked in `CouncilLive._collect_targets` or the
runner. Cheap, and it is what converts §6 from prose into a guarantee.

### E10 — MAJOR. The §3 rank needs a live cross-sectional panel

See B7. Reuse `FundamentalsFetcher` (not `fundamentals_panel`'s ProcessPool
quarterly build) behind a one-pass universe sweep, cached per rebalance date,
honouring `filed ≤ rebalance_date` (§9.7).

### E11 — MINOR. The rebalance band will trade the statistical sleeve daily

`DEFAULT_REBALANCE_BAND = 0.25` (`runner.py:101`) on a 2.25% target trips at
±0.56pp of NAV, and `_rebalance` runs on every `DailyRunner.run`. §7 wants
statistical trades only at the quarterly rebalance. Gate the band by run type
along with E6.

### E12 — MINOR. `TheCouncil.select` ignores `prices` and `fundamentals`

`strategy.py:99-160` uses `universe` only as a tradeability mask and never reads
the other two. Under the new engine both become primary inputs — no interface
change, just an implementation that uses what it is handed.

---

## The decisions only the user can make

1. **Selection rule: §1-§6, or the shipped consensus-of-three?**
   → **§1-§6.** Consensus makes the twelfth agent a linear combination of the
   other eleven and is the one rule §8 explicitly excludes. If consensus stays
   instead, §1-§6 and §8 must be struck from the document — do not leave both live.

2. **`implied_weights`: rewrite the runbook, or promote "compounder" to a real sleeve?**
   → **Rewrite `COUNCIL_RUNBOOK.md:126`** to
   `{"statistical":0.45,"core":<observed>,"event":0.15,"cash":0.05}` and point
   Run 2 step 2 at §9.1 by section number. "compounder" appears as a sleeve name
   nowhere else in 1,490 lines of doctrine.

3. **Five-of-six: a fourth `COUNCIL_STATISTICAL` sleeve (5%, kill criteria +
   8-quarter time stop), or downgrade the verdict to PASS?**
   → **Fourth sleeve row** in §4 and §5. It preserves "no partial credit", gives
   the ticket an exit block that validates, and matches
   `THE_VALUE_COUNCIL.md:365`'s stop table. Downgrading deletes the doctrine's
   stated middle outcome.

4. **Autonomy: amend `COUNCIL_RUNBOOK.md:38` rule 5 and `THE_VALUE_COUNCIL.md:271`
   to the machine-checkable E1, or keep the human gate?**
   → **Amend both.** Part 10 already scopes the human gate to real money; these
   two do not, and rule 5 is prepended to every run.

5. **Breaker release: recovery above −20%, N sessions elapsed, or both?**
   → **Both** (drawdown back above −20% **and** ≥20 sessions since the breach),
   journaled. One condition alone either releases on a one-day bounce or holds the
   book hostage to a flat tape.

6. **TTM: build the quarterly-sum assembler first, or ship v1 on annual figures?**
   → **Build it first.** Every number in §2 and §3 is TTM-denominated; annual
   figures run up to 15 months stale, which is exactly the failure
   `core/research/factors.py:41-59` documents.

7. **U8: bundle the point-in-time S&P 500 membership CSV, or drop the
   index-exclusion test?**
   → **Bundle it.** One CSV, and the only free survivorship-bias fix
   `COUNCIL_DATA.md` names.

8. **Statistical entries and the punch card: confirm zero cost in §4?**
   → **Yes, state it explicitly.** Part 7 already exempts the mechanical sleeve
   and `journal.py:247-253` already charges only UNDERSTANDING; §4's Statistical
   row is the only silent place. Without the sentence an implementer burns all 20
   punches on the first rebalance.

9. **Cash floor: Part 4's constant 5%, or §9.1's regime-dependent 5/5/10/15/20?**
   → **§9.1**, with Part 4's 5% restated as the absolute hard floor beneath it.
   `limits.py:50` becomes a function of `risk_on_count`.

10. **Illiquidity: days-to-exit (§9.6) or the $5M ADV class (`limits.py:47`)?**
    → **Days-to-exit.** On a $10k book the ADV constant flags most of the
    small-cap universe as illiquid; §9.6 was written to make the same rule
    correct at $10k and $10M.

11. **Event sleeve in v1, or defer?**
    → **Defer.** ATR(14), Form 10, index deletions and SUE are all missing (B8),
    and the sleeve is 0-15% of the book. Ship Statistical + Core with the event
    ceiling at 0% and the statistical start at 45%.

12. **`min_holding_days = 30`: exempt forced exits only, or lower the floor?**
    → **Exempt forced exits only** (E2/E3 and any E9 trim). The floor is doing
    real work against churn for all twelve agents; lowering it for one re-opens
    the failure it was written to close.
