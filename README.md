# The Value Council

> Ten AI agents, each modeled after a legendary value investor, managing
> independent paper-money portfolios according to that investor's
> documented methodology.

The Council runs autonomously: agents screen US and Israeli stocks,
deliberate using the Gemini API, and log every decision to JSON. A
separate Next.js dashboard (later phase) visualizes results across the
ten portfolios so you can compare how Graham's deep-value screener
behaves against Buffett's quality lens or Lynch's GARP heuristic in
real market conditions.

> **Status:** Infrastructure ready. Agent-specific logic (playbooks,
> screening rules) is being built one agent at a time.

> **Disclaimer:** This is a research and educational project. Nothing
> here is investment advice. All trading is paper-money simulation.

## The Roster

| Slug | Investor | Style |
|------|----------|-------|
| `graham` | Benjamin Graham | Deep value, net-nets, margin of safety |
| `buffett` | Warren Buffett | Quality + economic moats |
| `lynch` | Peter Lynch | GARP (growth at a reasonable price) |
| `greenblatt` | Joel Greenblatt | Magic Formula |
| `klarman` | Seth Klarman | Margin of safety + special situations |
| `schloss` | Walter Schloss | Diversified deep value |
| `marks` | Howard Marks | Cycles, contrarian positioning |
| `fisher` | Ken Fisher | Low Price-to-Sales |
| `neff` | John Neff | Low P/E + dividend yield |
| `dreman` | David Dreman | Contrarian low multiples |

## Quick Start

```bash
# 1. Clone & install
git clone <this-repo>
cd the-value-council
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure secrets
cp .env.example .env
# ...edit .env, fill in API keys (see below)

# 3. Verify all APIs respond
python -m core.tests.test_connections

# 4. Run unit tests
pytest
```

### API keys you will need

| Service | Free tier? | Where to get one |
|---------|-----------|------------------|
| Google Gemini | Yes (1500/day) | https://aistudio.google.com/apikey |
| Financial Modeling Prep | Yes | https://site.financialmodelingprep.com |
| Finnhub | Yes (60/min) | https://finnhub.io/register |
| Alpha Vantage | Yes (5/min) | https://www.alphavantage.co/support/#api-key |
| MarketAux | Yes | https://www.marketaux.com |
| SEC EDGAR | No key — but identifying User-Agent required | n/a |
| TASE Open API (optional) | Yes | https://openapi.tase.co.il |

## Tech Stack

- **Python 3.12** with type hints throughout
- **Gemini 2.5 Flash** for LLM reasoning (`google-generativeai`)
- **yfinance** for global market data
- **Financial Modeling Prep** for fundamentals + screener + transcripts
- **edgartools** for SEC filings (10-K, 10-Q, 8-K, 13F, Form 4)
- **Finnhub** for real-time quotes & news
- **Alpha Vantage** for news with sentiment
- **MarketAux** for news from 5,000+ sources
- **TASE Open API** for Israeli market data (optional)
- **Pydantic v2** + **pydantic-settings** for typed config and models
- **loguru** for structured logging
- **tenacity** for resilient HTTP retries
- **pytest** for tests
- **JSON files** for state — no database
- **GitHub Actions** for scheduling (added later)
- **Next.js + Tailwind** for dashboard (added later)

## Folder Structure

```
the-value-council/
├── core/             # Reusable infrastructure: config, logging, data sources,
│                     # scoring, LLM, portfolio, screener
├── agents/           # One folder per investor (playbook, rules, portfolio)
├── data/             # Universe files + global decision log
├── dashboard/        # Next.js dashboard (placeholder)
├── docs/             # Architecture & playbook template
└── .github/          # GitHub Actions workflows (placeholder)
```

See [docs/architecture.md](docs/architecture.md) for the full layered
design and rationale.

## Contributing playbooks

Every agent's playbook follows the canonical 12-section template at
[docs/playbook_template.md](docs/playbook_template.md). The quality bar
is high — each section has a minimum-evidence requirement (e.g., at
least 5 direct quotes with primary-source citations in the Investment
Philosophy section).
