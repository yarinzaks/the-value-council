# Architecture

This document captures the design decisions behind The Value Council
infrastructure. It is for engineers extending the system.

## Layered design

```
┌─────────────────────────────────────────────────────────────────┐
│                          GitHub Actions                          │  Scheduling
└─────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                     agents/<name>/run.py                         │  Per-agent runtime
│  ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌────────────┐  │
│  │playbook │ →  │ screener │ →  │ enrich  │ →  │ LLM memo   │  │
│  │  .md    │    │  rules   │    │ snapshot│    │ + decision │  │
│  └─────────┘    └──────────┘    └─────────┘    └────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────────────┐
        ▼                        ▼                                ▼
┌──────────────────┐   ┌──────────────────┐         ┌──────────────────┐
│  core.screener   │   │   core.data      │         │     core.llm     │
│  (rule engine)   │   │ UnifiedFetcher → │         │  GeminiClient +  │
│                  │   │  multiple APIs   │         │  prompts         │
└──────────────────┘   └──────────────────┘         └──────────────────┘
                                 │                            │
        ┌────────────────────────┴────────────┐               │
        ▼                                      ▼              │
┌──────────────────┐                 ┌──────────────────┐     │
│  core.portfolio  │ ← decisions ←   │   core.scoring   │     │
│  Portfolio +     │                 │  (Piotroski,     │     │
│  DecisionLog     │                 │   Altman, ...)   │     │
└──────────────────┘                 └──────────────────┘     │
        │                                                      │
        ▼                                                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                        State on disk (JSON)                      │
│  agents/<name>/portfolio.json   data/decisions.jsonl  logs/      │
└─────────────────────────────────────────────────────────────────┘
```

## Key decisions

### JSON files as the state store
Picking SQLite or Postgres would buy us transactions and richer
queries, but introduce migrations, drivers, and a backup story.
JSON+JSONL files give us: trivially `git diff`-able state, no schema
ceremony, easy hand-editing, and a portable artifact that the Next.js
dashboard can ship as static data.

When we outgrow this — large concurrent writes or complex aggregations
— we will swap in DuckDB or SQLite without changing the public API of
`Portfolio` and `DecisionLog`.

### Gemini 2.5 Flash on the free tier
Gemini 2.5 Pro left the free tier in 2025; Flash is now the default.
The free tier (1500 requests/day, 15 RPM) is plenty for ten agents
that each evaluate a small handful of candidates per cycle. We
self-throttle in `GeminiClient` (4-second minimum interval) to stay
safely inside the envelope even when multiple agents run back-to-back.

If we want Pro's longer reasoning for the hardest decisions, we will
switch the model in `GeminiClient(model="gemini-2.5-pro")` and enable
billing on the project; the rate limiter is configurable.

### Multiple data sources behind one fetcher
No single free-tier provider gives us everything: yfinance is broad
but flaky; FMP has the best fundamentals; Finnhub is fastest for
real-time quotes; Alpha Vantage adds sentiment-tagged news; MarketAux
reaches non-financial press; EDGAR is authoritative for filings.

`UnifiedFetcher` hides this fan-out: callers ask for a quote, and we
try sources in priority order, falling back when one fails. The
1-hour in-process cache prevents redundant calls across agents in a
single workflow run.

### Israeli market is a first-class but optional citizen
TASE Open API is the right official source, but it requires OAuth and
an enterprise tier for fundamentals. We support it where credentials
exist, fall back to yfinance with the `.TA` suffix otherwise, and the
fetcher routes Israeli tickers correctly without callers caring.

### Scoring functions take raw numbers, not models
`piotroski_f_score`, `altman_z_score`, etc. take dataclasses of raw
floats — not :class:`Fundamentals` objects. This keeps them
deterministic, easy to unit test, and reusable when fed from sources
that don't fit the project's :class:`Fundamentals` shape.

## Data flow for a single trading cycle

1. **Workflow trigger.** A GitHub Action (cron) invokes the agent's
   entry point.
2. **Universe load.** Agent reads `data/universe_us.json` /
   `universe_il.json` — pre-built lists of tradeable tickers.
3. **Screen.** Agent applies playbook-specific filters via
   `ScreenerEngine` against snapshots fetched in batch.
4. **Enrich.** For each surviving candidate, `UnifiedFetcher.enrich()`
   pulls the full picture — quote, fundamentals, news, recent filings,
   insider transactions.
5. **Memo.** Each candidate goes through `GeminiClient.generate_investment_memo`
   with the full playbook as the system prompt.
6. **Decide & trade.** BUY/SELL memos hit `Portfolio.buy()` /
   `Portfolio.sell()`, mutating per-agent JSON and appending to the
   global decision log.
7. **Persist.** Portfolio saves; logs flush; the workflow uploads the
   JSON state as a commit so the dashboard can render it.

## Error handling philosophy

* **Fail fast at startup** — missing keys raise `ConfigError` listing
  every problem at once, not one at a time.
* **Fail soft at runtime** — a single broken data source must not
  bring down the agent. `UnifiedFetcher` catches per-source errors and
  falls forward.
* **Retries are bounded** — 3 attempts with exponential backoff,
  guarded by tenacity. After that we surface the error.
* **Decision logging is durable** — every trade flushes to disk
  immediately. A crash mid-cycle never loses recorded decisions.

## What this foundation deliberately does *not* include

- **Agent-specific screener rules.** Playbooks are stubs.
- **Universe construction.** `universe_us.json` and `universe_il.json`
  are empty arrays; the next session will populate them.
- **The dashboard.** Empty Next.js folder; comes after the first agent
  is producing decisions.
- **GitHub Actions workflows.** Placeholder; comes after the first
  agent runs end-to-end locally.
