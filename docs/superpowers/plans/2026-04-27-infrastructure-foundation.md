# The Value Council — Infrastructure Foundation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete project infrastructure for The Value Council — directory layout, configuration, logging, data source clients, scoring functions, LLM client, portfolio manager, screening engine, connection tests, agent scaffolding, and documentation. No agent-specific logic yet.

**Architecture:** A layered Python 3.12 package. `core/` holds all reusable infrastructure (config, logging, data sources, scoring, LLM, portfolio, screener). `agents/` holds 10 per-investor folders, currently empty placeholders. `data/` and `dashboard/` are placeholders for later sessions. State persists as JSON files; Gemini handles reasoning; multiple data sources are unified behind a single fetcher with caching and fallback.

**Tech Stack:** Python 3.12, pydantic-settings, loguru, tenacity, requests, yfinance, google-generativeai, edgartools, pandas, pytest, Pydantic v2.

---

## File Structure

```
the-value-council/
├── README.md                    # Project overview, quickstart
├── requirements.txt             # Pinned dependencies
├── pyproject.toml               # Modern project config
├── .env.example                 # All env vars with placeholders
├── .gitignore                   # Python + secrets + OS + IDE
│
├── core/
│   ├── __init__.py
│   ├── config.py                # Settings via pydantic-settings
│   ├── logger.py                # loguru console + rotating file
│   ├── exceptions.py            # Custom exception hierarchy
│   │
│   ├── data/
│   │   ├── __init__.py          # Exports StockSnapshot model
│   │   ├── models.py            # Pydantic models: StockSnapshot, Quote, Fundamentals, NewsItem
│   │   ├── base.py              # DataSource ABC with retry decorator
│   │   ├── yfinance_source.py
│   │   ├── fmp_source.py
│   │   ├── edgar_source.py
│   │   ├── finnhub_source.py
│   │   ├── alpha_vantage_source.py
│   │   ├── marketaux_source.py
│   │   ├── tase_source.py
│   │   └── unified_fetcher.py   # Cache + fallback + enrich()
│   │
│   ├── scoring/
│   │   ├── __init__.py
│   │   ├── piotroski.py         # F-Score 0-9
│   │   ├── altman.py            # Z-Score
│   │   ├── beneish.py           # M-Score
│   │   └── graham_number.py
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── gemini_client.py     # Wrapper with retry, JSON parsing
│   │   └── prompts.py           # Prompt templates
│   │
│   ├── portfolio/
│   │   ├── __init__.py
│   │   ├── manager.py           # Portfolio class
│   │   └── decision_log.py      # Append-only JSONL log
│   │
│   ├── screener/
│   │   ├── __init__.py
│   │   └── engine.py            # Generic rule-based screener
│   │
│   └── tests/
│       ├── __init__.py
│       ├── test_connections.py  # Sanity: hit each API with one call
│       ├── test_models.py
│       ├── test_scoring.py
│       └── test_portfolio.py
│
├── agents/
│   ├── _base.py                 # Stub base agent class
│   └── {graham,buffett,lynch,greenblatt,klarman,schloss,marks,fisher,neff,dreman}/
│       ├── playbook.md          # "# [Name] — Playbook (TBD)"
│       ├── screener_rules.py    # Comment-only stub
│       └── portfolio.json       # Initial state
│
├── data/
│   ├── universe_us.json         # Empty placeholder
│   ├── universe_il.json         # Empty placeholder
│   └── decisions.jsonl          # Empty (created on first write)
│
├── dashboard/
│   └── README.md                # Placeholder
│
├── .github/
│   └── workflows/
│       └── README.md            # Placeholder
│
├── docs/
│   ├── architecture.md
│   └── playbook_template.md
│
└── logs/                        # Created automatically by logger
```

---

## Phase 1: Project Scaffolding

### Task 1.1: Create directory tree

- [ ] **Step 1: Create all directories**

```bash
cd /Users/yarinzaks/Documents/The-Value-Council
mkdir -p core/data core/scoring core/llm core/portfolio core/screener core/tests
mkdir -p agents/{graham,buffett,lynch,greenblatt,klarman,schloss,marks,fisher,neff,dreman}
mkdir -p data dashboard .github/workflows docs logs
```

- [ ] **Step 2: Verify**

Run: `find . -type d | sort`
Expected: All 31+ directories present.

### Task 1.2: .gitignore

- [ ] **Step 1: Write .gitignore**

Path: `.gitignore`

Contents (full Python + secrets + OS + IDE + Node + build artifacts).

### Task 1.3: .env.example

- [ ] **Step 1: Write .env.example**

Path: `.env.example`

All keys: `GEMINI_API_KEY`, `FMP_API_KEY`, `FINNHUB_API_KEY`, `ALPHA_VANTAGE_KEY`, `MARKETAUX_API_KEY`, `SEC_USER_AGENT`, `LOG_LEVEL`.

### Task 1.4: requirements.txt

- [ ] **Step 1: Write pinned dependencies**

Path: `requirements.txt`

Per spec, plus `pydantic-settings>=2.0.0`, `pytest>=7.4.0`, `pytest-mock>=3.12.0`.

### Task 1.5: pyproject.toml

- [ ] **Step 1: Write modern project config**

Path: `pyproject.toml`

Includes project metadata, ruff config, pytest config, mypy config.

---

## Phase 2: Core Config & Logging

### Task 2.1: core/__init__.py

- [ ] Empty file with version string.

### Task 2.2: core/exceptions.py

- [ ] Custom exception hierarchy: `ValueCouncilError`, `ConfigError`, `DataSourceError`, `RateLimitError`, `LLMError`, `PortfolioError`.

### Task 2.3: core/config.py

- [ ] **Settings class via pydantic-settings**

- Loads `.env` from project root via `pathlib`.
- All API keys typed as `SecretStr`.
- `LOG_LEVEL: str = "INFO"`.
- `SEC_USER_AGENT: str` (required).
- Method `get(key)` returns plaintext.
- Module-level `settings` instance.
- Raises `ConfigError` with helpful message listing all missing keys (not just first).

### Task 2.4: core/logger.py

- [ ] **Loguru setup**

- Strip default handler.
- Console sink: colored, INFO level (configurable).
- File sink: `logs/app.log`, rotation 10 MB, retention 7 days, JSON-friendly format.
- Format: `{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}`.
- Function `get_logger(name)` returns a bound logger.

### Task 2.5: Test config error message

- [ ] **Write test that confirms config raises with helpful message**

`core/tests/test_config.py`: monkeypatch env, assert ConfigError mentions every missing key.

---

## Phase 3: Data Models

### Task 3.1: core/data/models.py

- [ ] **Pydantic v2 models**

```python
class Quote(BaseModel):
    ticker: str
    price: float
    currency: str
    timestamp: datetime
    volume: int | None = None
    market_cap: float | None = None

class Fundamentals(BaseModel):
    ticker: str
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    ps_ratio: float | None = None
    debt_to_equity: float | None = None
    roe: float | None = None
    roa: float | None = None
    eps: float | None = None
    book_value_per_share: float | None = None
    free_cash_flow: float | None = None
    revenue: float | None = None
    net_income: float | None = None
    total_debt: float | None = None
    total_equity: float | None = None
    current_ratio: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    dividend_yield: float | None = None
    payout_ratio: float | None = None
    fiscal_year: int | None = None

class NewsItem(BaseModel):
    title: str
    url: str
    published_at: datetime
    source: str
    sentiment: float | None = None  # -1..1
    summary: str | None = None

class StockSnapshot(BaseModel):
    ticker: str
    quote: Quote | None = None
    fundamentals: Fundamentals | None = None
    news: list[NewsItem] = []
    sources: list[str] = []  # Which sources contributed
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

### Task 3.2: core/data/base.py

- [ ] **DataSource ABC**

- `name` class attribute.
- Abstract `get_quote(ticker)` and `get_fundamentals(ticker)`.
- `_retry` decorator helper using tenacity (3 retries, exp backoff, log on retry).
- `_log_call(method, ticker)` helper.

### Task 3.3: core/data/__init__.py

- [ ] Re-export models and base.

### Task 3.4: Test models

- [ ] `test_models.py`: instantiate, serialize round-trip, validate constraints.

---

## Phase 4: Data Sources

For each source: file with a class, `get_quote` and `get_fundamentals` (and source-specific extras), normalized return values, retry, logging, custom errors.

### Task 4.1: yfinance_source.py

- [ ] `YFinanceSource(DataSource)` — uses yfinance, no API key required, returns `Quote` and `Fundamentals`. Handles `.TA` suffix for Israeli stocks.

### Task 4.2: fmp_source.py

- [ ] `FMPSource` — REST calls to `financialmodelingprep.com/api/v3/`. Endpoints: `/quote`, `/profile`, `/ratios-ttm`, `/key-metrics-ttm`, `/income-statement`, `/balance-sheet-statement`, `/cash-flow-statement`, `/earning_call_transcript`, `/stock-screener`. Rate-limit-aware.

### Task 4.3: edgar_source.py

- [ ] `EdgarSource` — uses edgartools. Methods: `get_filings(ticker, form_type)`, `get_latest_10k(ticker)`, `get_13f_holdings(cik)`, `get_form4_insider_transactions(ticker)`. Sets user agent from settings.

### Task 4.4: finnhub_source.py

- [ ] `FinnhubSource` — REST. `get_quote`, `get_company_news`, `get_basic_financials`. Header-based auth.

### Task 4.5: alpha_vantage_source.py

- [ ] `AlphaVantageSource` — REST. `get_quote` (GLOBAL_QUOTE), `get_company_overview`, `get_news_sentiment`. Handles 5-call/min throttle with sleep.

### Task 4.6: marketaux_source.py

- [ ] `MarketauxSource` — REST. `get_news(ticker, limit)`. Maps result to `NewsItem` list with sentiment.

### Task 4.7: tase_source.py

- [ ] `TaseSource` — REST against `openapi.tase.co.il`. `get_quote(ticker)`, `get_securities_list()`. OAuth client_credentials flow (TASE requires it). For now stub the auth call but implement structure cleanly.

---

## Phase 5: Unified Fetcher

### Task 5.1: core/data/unified_fetcher.py

- [ ] **UnifiedFetcher**

- Constructs all source instances on init (catches per-source ConfigError to allow partial setup).
- In-memory cache: `dict[(method, ticker), tuple[result, expires_at]]`, TTL 1 hour.
- `get_quote(ticker)`: try yfinance → fmp → finnhub → alpha_vantage. Returns first success.
- `get_fundamentals(ticker)`: try fmp → yfinance → finnhub.
- `get_news(ticker)`: aggregate from marketaux + finnhub + alpha_vantage, dedupe by URL.
- `enrich(ticker)`: returns full `StockSnapshot` combining quote + fundamentals + news + (US only) latest 10-K excerpt + insider transactions. Tracks which sources succeeded in `sources`.
- All methods log call counts and cache hit rates.

---

## Phase 6: Scoring

### Task 6.1: scoring/piotroski.py

- [ ] **Piotroski F-Score (0-9)**

Function `piotroski_f_score(fundamentals_current, fundamentals_prior) -> tuple[int, dict]` returning score and per-criterion breakdown. 9 binary criteria across profitability (4), leverage/liquidity (3), operating efficiency (2).

### Task 6.2: scoring/altman.py

- [ ] **Altman Z-Score**

`altman_z_score(working_capital, retained_earnings, ebit, market_cap, sales, total_assets, total_liabilities) -> float`. Manufacturing variant.

### Task 6.3: scoring/beneish.py

- [ ] **Beneish M-Score**

`beneish_m_score(...) -> float`. Threshold > -1.78 = potential earnings manipulator. Eight ratios.

### Task 6.4: scoring/graham_number.py

- [ ] **Graham Number**

`graham_number(eps, book_value_per_share) -> float | None`. Returns `sqrt(22.5 * eps * bvps)` if both positive.

### Task 6.5: Tests for scoring

- [ ] `test_scoring.py`: deterministic test cases with known inputs/outputs for each.

---

## Phase 7: LLM

### Task 7.1: core/llm/prompts.py

- [ ] **Prompt templates**

`INVESTMENT_MEMO_SYSTEM` — establishes role.
`INVESTMENT_MEMO_USER_TEMPLATE` — `.format(playbook=, stock_data=, portfolio_state=)` produces a prompt requesting JSON output with the schema in the spec.
Schema is documented as a constant `INVESTMENT_MEMO_SCHEMA`.

### Task 7.2: core/llm/gemini_client.py

- [ ] **GeminiClient**

- Constructor configures `google.generativeai` with API key.
- Model: `gemini-2.5-pro`.
- `generate_investment_memo(playbook, stock_data, portfolio_state) -> dict`.
- Uses tenacity retry on transient errors.
- Self-throttles with a token bucket: 1500/day, ~1/min.
- Parses `response.text`, extracts JSON (handles markdown fences), validates against schema using a Pydantic `InvestmentMemo` model.
- Raises `LLMError` on persistent failure.

---

## Phase 8: Portfolio

### Task 8.1: core/portfolio/decision_log.py

- [ ] **DecisionLog**

`DecisionLog(path: Path)` — append-only writer. `append(entry: dict)` — adds `timestamp`, writes one JSON line, flushes. Reader: `read_all() -> list[dict]`.

### Task 8.2: core/portfolio/manager.py

- [ ] **Portfolio class**

```python
class Position(BaseModel):
    ticker: str
    shares: float
    avg_cost: float
    opened_at: datetime

class Portfolio:
    def __init__(self, agent_name: str, initial_cash_usd: float = 10_000): ...
    def buy(self, ticker, shares, price, rationale: dict) -> None: ...
    def sell(self, ticker, shares, price, rationale: dict) -> None: ...
    def current_value(self, price_lookup: Callable[[str], float]) -> float: ...
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "Portfolio": ...
    def save(self) -> None: ...   # to agents/{name}/portfolio.json
    @classmethod
    def load(cls, agent_name: str) -> "Portfolio": ...
```

All buy/sell actions append to global `data/decisions.jsonl` AND per-agent history.

### Task 8.3: Test portfolio

- [ ] `test_portfolio.py`: buy, sell, partial sell, oversell raises, valuation with mock prices, round-trip serialization.

---

## Phase 9: Screener Engine

### Task 9.1: core/screener/engine.py

- [ ] **ScreenerEngine**

- Filter primitives: `Filter(field: str, op: str, value: Any)` with ops `>`, `<`, `>=`, `<=`, `==`, `!=`, `in`, `not_in`, `between`.
- `apply(snapshot: StockSnapshot, filters: list[Filter]) -> bool`.
- `screen(snapshots: Iterable[StockSnapshot], filters: list[Filter]) -> list[StockSnapshot]`.
- `Filter` field can be dotted path, e.g., `fundamentals.pe_ratio`.

---

## Phase 10: Connection Test

### Task 10.1: core/tests/test_connections.py

- [ ] **Sanity check script**

Runnable as `python -m core.tests.test_connections`. For each of 6 APIs (Gemini, FMP, Finnhub, Alpha Vantage, MarketAux, EDGAR — yfinance no key, TASE OAuth deferred):
- Try one call (e.g., quote for AAPL or news for AAPL).
- Print `✓ {name} OK` (green) or `✗ {name} FAILED: {reason}` (red).
- ANSI colors via simple constants.
- Exit 0 if all pass, 1 otherwise.

If `.env` is missing, prints clear error and exits 2.

---

## Phase 11: Agent Scaffolding

### Task 11.1: agents/_base.py

- [ ] Stub `Agent` ABC with `name`, `playbook_path`, abstract `run()` — to be filled later.

### Task 11.2: For each of 10 agents

- [ ] **Create three files in `agents/{name}/`:**

- `playbook.md` — single line: `# {Name} — Playbook (TBD)` (with proper capitalized name).
- `screener_rules.py` — single line: `# Screening rules for {name} — TBD`.
- `portfolio.json` — `{"initialized": false, "cash_usd": 10000, "positions": [], "history": []}`.

---

## Phase 12: Documentation & Final Files

### Task 12.1: README.md

- [ ] Complete README with project description, status, quickstart, tech stack, structure, disclaimer.

### Task 12.2: docs/architecture.md

- [ ] Architecture decisions: layers, data flow, why JSON, why Gemini free tier, source priority order, caching.

### Task 12.3: docs/playbook_template.md

- [ ] All 12 sections with quality bar comments.

### Task 12.4: dashboard/README.md and .github/workflows/README.md

- [ ] Placeholders.

### Task 12.5: data/universe_us.json and data/universe_il.json

- [ ] Empty array placeholders: `[]`.

---

## Phase 13: Final Verification

### Task 13.1: Run connection test

- [ ] **Run `python -m core.tests.test_connections`**

Expected: Fails because no `.env`. Verify error message is helpful and lists missing keys.

### Task 13.2: Show tree and onboarding

- [ ] Print final tree (`find . -type f | sort`) and write the "what to do next" instructions for the user.
