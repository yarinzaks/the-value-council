# Schloss — Diversified Deep-Value Agent

> Second quantitative agent of The Value Council. Implements Walter
> Schloss's "buy below book value, hold many names, rebalance
> annually" approach from [`playbook.md`](playbook.md) Sections 4 and 6.

## Components

| File | Responsibility |
|------|---------------|
| [filters.py](filters.py) | P/B, D/E, history, market-cap, profitability gates. |
| [ranking.py](ranking.py) | Sort survivors by P/B ascending. |
| [deep_value.py](deep_value.py) | `WalterSchloss` strategy — implements `core.backtest.Strategy`. |
| [tests/](tests/) | 55 unit + integration tests. |
| [run_full_validation.py](run_full_validation.py) | Reproduce the validation backtest on real S&P 500 data. |

## How the formula is implemented (matching playbook §4.1)

**Primary cheapness gate**
- **P/B < 0.75** (default; configurable). Slightly stricter than the
  playbook's 0.80 to focus on the deeper-discount segment Schloss
  preferred.
- **Tangible book** approximated as `total_equity` from XBRL. The
  PIT data model doesn't separately track goodwill — most XBRL
  filers report `StockholdersEquity` as the "common stockholders'
  equity" line which excludes minority interests but includes
  goodwill. This is the same simplification used by FactSet's
  standardized P/B feeds.

**Manageable debt**
- **D/E ≤ 1.0** — Schloss's hard rule from his 16 Rules.
- Computed as `total_debt / total_equity`. Falls back to
  `long_term_debt` when total isn't reported.

**Long operating history**
- **≥ 5 years public** at as_of (relaxed from playbook's 15 years).
  The 15-year rule excludes companies that haven't survived a
  recession; with our cache window starting 2008, requiring 15
  years would shrink the 2010-2020 universe to nearly nothing.
  The 5-year window still captures "weathered at least one
  business cycle" since 2010 includes the GFC tail and 2020 has
  COVID.

**Earnings stability (relaxed)**
- Most recent net income > 0. Schloss tolerated occasional losses
  but a low-P/B name with negative earnings is usually a
  deteriorating business, not a contrarian bargain.

**Market cap floor**
- $300M minimum. Schloss did invest in small-caps but our
  survivorship-bias-free universe still requires liquidity for
  realistic backtesting.

## Documented decisions

### D1 — `total_equity` as tangible book proxy
The XBRL `StockholdersEquity` concept is the standard reporting line.
Stripping goodwill+intangibles would require additional concept
mappings (`Goodwill`, `IntangibleAssetsNetExcludingGoodwill`) which
add code without changing the rank order materially for the kinds
of statistical-cheapness names Schloss bought (industrial cyclicals,
financials at troughs, asset-rich conglomerates).

### D2 — No multi-year price-low filter
Schloss bought stocks "near 52-week lows," but applying that as a
hard filter requires fetching 252 prior trading days of price data
per candidate per rebalance — expensive at S&P 500 scale. The
annual rebalance cadence partially substitutes: stocks that have
recently spiked won't pass the P/B < 0.75 gate anyway, since their
price has already moved up.

### D3 — No "familiar industry" filter
Schloss avoided foreign stocks and industries he didn't understand
(crypto, pre-revenue biotech). Our universe is already restricted to
the S&P 500, which by definition contains "familiar" US large-/mid-caps.
The fundamental filters (D/E, profitability, market cap) catch the
worst-of-the-worst industry distress.

### D4 — Take cheapest N, equal-weight
Schloss didn't formally rank. He bought any stock meeting the
criteria. For a fixed-N portfolio at a backtest cadence, "the N
cheapest by P/B that pass the filters" is the natural rule. Ties
broken by lower D/E (prefer less leverage when equally cheap).

### D5 — Annual rebalance via runner config
The playbook says "hold until ~50% gain or fundamentals deteriorate."
Annual rebalance is a coarser approximation but consistent with the
project's standard cadence and Schloss's actual pace (his average
holding period was 3-4 years; annual rotation is more frequent than
he was, but allows the strategy to react to mean reversion in P/B).

## Test count

```
agents/schloss/tests/   55 tests
  test_filters.py       28 tests
  test_ranking.py       11 tests
  test_deep_value.py    14 tests
  test_integration.py    2 tests (incl. end-to-end backtest)

Total project tests: 304 passing
```

## Running a validation backtest

```python
from datetime import date
from core.backtest import (
    BacktestRunner, RunnerConfig, PercentageCost, write_report,
)
from core.backtest.point_in_time import PointInTimeLoader
from core.backtest.universe import load_universe
from core.data.edgar_cache import EdgarCache
from core.data.fundamentals_fetcher import (
    CachedEdgarAdapter, FundamentalsFetcher, FundamentalsFetcherConfig,
)
from agents.schloss import WalterSchloss

cache = EdgarCache()
fetcher = FundamentalsFetcher(
    cache=cache, client=None,
    config=FundamentalsFetcherConfig(populate_cache_on_miss=False),
)
adapter = CachedEdgarAdapter(fetcher=fetcher)
pit = PointInTimeLoader(adapter=adapter)

cfg = RunnerConfig(
    start_date=date(2010, 1, 4),
    end_date=date(2024, 12, 31),
    initial_cash=10_000.0,
    rebalance_freq="annual",
    cost_model=PercentageCost(0.001),
    use_universe=True,
    use_fundamentals=True,
)
runner = BacktestRunner(cfg, pit_loader=pit, universe=load_universe())
strategy = WalterSchloss(portfolio_size=100, max_pb=0.75)
result = runner.run(strategy)
write_report(result)
```
