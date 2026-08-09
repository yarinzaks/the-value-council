# Eleventh agent — the search log

Every design scored on the development window, in the order it was
tried, with the number it produced. Nothing is omitted, including the
ideas that failed and the two results that turned out to be artefacts.

The count matters as much as the winner. Scoring thirty designs and
reporting the best is not the same as scoring one and having it work,
even when the winning number is identical — with thirty draws from
noise, the best of them looks good by construction. This file is the
denominator.

**Window:** 2011-01-01 → 2018-12-31, quarterly rebalance, top-25
equal-weight unless stated. Universe: US common equity, one listing per
issuer, median daily dollar volume ≥ $1M. Costs 10bp per side charged
against drifted turnover. **Benchmark (SPY): 10.75% CAGR.**

The holdout (2019-01-01 → 2026-08-08) was not read while any of this
was being decided.

---

## Round 1 — the seven price-only designs, as registered

| design | CAGR% | vol% | Sharpe | maxDD% | alpha% | t |
|---|---|---|---|---|---|---|
| low idiosyncratic vol | **12.06** | 9.25 | **1.304** | **-8.09** | +1.66 | 0.45 |
| momentum + low ivol | 11.56 | 11.61 | 0.996 | -13.86 | +1.16 | 0.48 |
| momentum + low ivol + not extended | 11.50 | 13.92 | 0.826 | -18.33 | +1.10 | 0.58 |
| low total vol | 9.27 | 8.98 | 1.032 | -8.67 | -1.13 | -0.40 |
| momentum 6-1 | -1.29 | 26.17 | -0.049 | -47.62 | -11.69 | -1.23 |
| not extended (1-month reversal) | -2.09 | 27.83 | -0.075 | -38.19 | -12.49 | -1.18 |
| momentum 12-1 | -4.52 | 28.61 | -0.158 | -55.82 | -14.92 | -1.57 |

Three things came out of this that shaped everything after.

**Idiosyncratic volatility beats total volatility** by 2.8 points, which
is what the low-risk literature says: the effect sits in the residual,
not in beta or in total variance.

**Concentrated momentum is a disaster** — worse than any other design
tried, with a 55.8% drawdown. Not a data problem; see round 3.

**Adding momentum to low-ivol makes it worse** on return and much worse
on risk. Two good ideas do not necessarily combine.

## Round 2 — the three registered variations, on the winner

| design | CAGR% | Sharpe | maxDD% | alpha% |
|---|---|---|---|---|
| low ivol, 25 names, equal (base) | **12.06** | 1.304 | -8.09 | +1.66 |
| low ivol, 25, inverse-volatility weighted | 11.83 | 1.267 | -8.27 | +1.44 |
| low ivol, 100, equal | 11.54 | 1.229 | -8.40 | +1.14 |
| low ivol, 50, equal | 11.06 | 1.175 | -8.91 | +0.67 |
| low ivol, 25 + trend overlay | 9.13 | 1.038 | -8.08 | -1.27 |
| low ivol, 50 + trend overlay | 7.78 | 0.898 | -8.92 | -2.61 |

**None of the three improved on the base.** That is worth stating
plainly: a search that keeps finding improvements is usually finding
noise, and this one did not.

The trend overlay cost 2.9 points of return and bought nothing, for a
reason that is obvious in hindsight — a book whose worst drawdown is
8% has nothing to be protected from. The overlay exists to avoid large
drawdowns, and there were none to avoid.

## Round 3 — is concentration what breaks momentum?

The MAX effect (Bali, Cakici & Whitelaw 2011) says stocks with extreme
recent returns *underperform* by about 1% a month. Top-25 of ~1,400 is
the 98th percentile — the lottery-ticket population — while the momentum
literature tests deciles. If that is the explanation, removing the
extreme tail should recover the factor monotonically.

| momentum portfolio | CAGR% | maxDD% |
|---|---|---|
| top 25 (98th percentile) | -4.52 | -55.8 |
| top 100 | 4.88 | -29.2 |
| top 140 (a decile) | 5.44 | -25.4 |
| **ranks 26-125 (skip the tail)** | **7.79** | **-24.3** |
| ranks 71-170 | 7.40 | -24.5 |

It does. Dropping 25 names adds 12.3 points of return and halves the
drawdown. The prediction held, which is the strongest evidence in this
file that the mechanism is understood rather than curve-fitted.

**Momentum still loses to the benchmark** in its best form. It is not
the return engine here.

## Round 4 — does low-ivol have a bad tail too?

Same question, other direction: are the very quietest names a distinct
population — bond proxies driven by rates rather than by the low-risk
effect?

| band by ascending idiosyncratic vol | CAGR% | Sharpe |
|---|---|---|
| quietest 25 | 12.06 | **1.304** |
| quietest 50 | 11.06 | 1.175 |
| ranks 26-50 | 9.87 | 0.995 |
| ranks 51-75 | 11.82 | 1.288 |
| ranks 101-125 | 10.67 | 1.064 |
| ranks 201-225 | **12.07** | 0.990 |

**No monotone premium.** Ranks 201-225, carrying much more volatility,
returned the same 12.07%. So the 12.06% is not being produced by
quietness.

What *is* monotone is the Sharpe: 1.304 at the quiet end against 0.990
in the middle of the distribution. Which is exactly the published
finding — the low-volatility anomaly is a risk-adjusted effect, not a
raw-return one. **For an objective stated as maximum return, low
volatility offers nothing.**

## Round 5 — regime switching

Daniel & Moskowitz show momentum crashes are partly forecastable: they
happen in panic states, after market declines and in high volatility.
So rather than moving to cash when the index is below trend, hold the
aggressive book while the trend is up and the defensive one while it is
down.

| design | CAGR% | maxDD% |
|---|---|---|
| low ivol always (base) | **12.06** | -8.09 |
| trend up → momentum, trend down → low ivol | -2.05 | -46.0 |
| trend up → momentum+low ivol, down → low ivol | 11.12 | -13.9 |

Momentum fails in uptrends too, so there is no regime in which
switching into it pays. A clean negative result.

---

## Two results that were artefacts

Both looked like the best row in the table when they appeared.

**A 1.51 Sharpe with a 6.8% drawdown.** The low-volatility screen was
returning `MER-PK`, `BAC-PL`, `C-PN`, `AXS-PE` — preferred series, which
are bonds wearing equity tickers. They barely move, so they sweep the
top of any volatility ranking. Fixed by applying the project's existing
common-equity filter.

**46.84% a year at 1,030% volatility.** The reversal screen had found
`TIE`, whose series in this database is two securities spliced together:
a foreign listing near $11,000 with one bar at $16.51 inside it — the
price Precision Castparts actually paid for Titanium Metals. That single
bar is a -99.85% move in and a +66,526% move out. One position at a 4%
weight turned a quarter into +2,900%. With the bar removed the design
returns -2.09%.

## Designs scored so far

Seven registered price-only designs, three registered variations, five
momentum bands, six volatility bands, two regime switches. **Twenty-three
so far**, before the fundamental legs.

Nothing here clears a t-statistic of 2. The best is 0.58.
