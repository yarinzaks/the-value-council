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

**No monotone premium across the bands.** Ranks 201-225, carrying much
more volatility, returned the same 12.07%. So within the quiet half of
the distribution, being quieter does not pay more.

What *is* monotone is the Sharpe: 1.304 at the quiet end against 0.990
in the middle. Which is the published finding — the low-volatility
anomaly is a risk-adjusted effect before it is a return effect.

This looked at first like it emptied the result out. Round 6 shows it
does not: every band here sits in the lower half of the volatility
distribution, so all of them capture some of the effect, and all of
them beat a book with no selection at all. The flatness is between
bands, not between selection and none.

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

---

## Round 6 — the control that had to be run

Rounds 3 and 4 left a suspicion worth taking seriously. The quietest 25
returned 12.06% and ranks 201-225 returned 12.07% — two bands with
nothing in common, landing on the same number, both above the
benchmark. That pattern is what a *mechanical* effect looks like: an
equal-weighted book of liquid US stocks carries a mid-cap tilt against
a cap-weighted index, and equal weight beat cap weight over much of
this period. If that were the whole story, none of the factors would be
contributing anything.

So: equal-weight the entire investable universe, with no signal at all.

| | CAGR% | vol% | Sharpe | maxDD% |
|---|---|---|---|---|
| equal-weight universe (1,517 names, no signal) | 10.02 | 15.75 | 0.636 | -20.18 |
| low idiosyncratic vol, top 25 | **12.06** | **9.25** | **1.304** | **-8.09** |
| benchmark (SPY, cap-weighted) | 10.75 | — | — | — |

The suspicion was wrong. The naive equal-weight book **underperforms**
the index and carries half again its volatility and a 20% drawdown.
Selecting on idiosyncratic volatility adds two points of return over
it while cutting volatility by 40% and drawdown by 60%.

That reconciles round 4 as well: every band from 1 to 225 sits in the
lower half of the volatility distribution, so all of them capture some
of the effect. What separates them is the Sharpe, and that *is*
monotone — 1.304 at the quiet end against 0.990 in the middle.

**The selection is real.** Running the control was the only way to know
that, and it is the single result in this file that a reader should
check first.

**Twenty-four designs scored.**

---

# The holdout

Opened once, on 2019-01-01 → 2026-08-08, after the design was frozen
and committed. Benchmark over that window: **16.01%**.

| design | dev CAGR% | holdout CAGR% | dev rank | holdout rank |
|---|---|---|---|---|
| quality + mom + low ivol | 12.39 | 11.52 | 1 | 8 |
| **low idio vol** — *the one that was chosen* | **12.06** | **3.55** | **2** | **12** |
| value + quality + mom + low ivol | 11.62 | 11.04 | 3 | 9 |
| mom + low ivol | 11.56 | 10.44 | 4 | 10 |
| mom + low ivol + not extended | 11.50 | 13.32 | 5 | 7 |
| low total vol | 9.27 | 3.81 | 6 | 11 |
| quality | 8.70 | 16.33 | 7 | 6 |
| value | 8.16 | 22.81 | 8 | 2 |
| value + quality | 7.74 | 19.99 | 9 | 3 |
| value + quality + mom | 5.19 | 18.98 | 10 | 4 |
| value + mom | 2.80 | 23.88 | 11 | 1 |
| momentum 6-1 | -1.29 | 3.34 | 12 | 13 |
| momentum 12-1 | -4.52 | 17.04 | 13 | 5 |

## The chosen design failed, significantly

3.55% against a benchmark of 16.01% — **-12.50 points of alpha, t = -2.27**.
That is one of only two rows in the whole exercise to clear a
t-statistic of 2 in absolute value, and it clears it in the wrong
direction. Not "failed to beat the market": measurably worse than it.

Year by year, the strategy against the index: -27.6, -16.7, -4.4, -0.9,
-16.7, +2.2, -9.3, -12.3. One good year in eight.

## The finding that matters more than the agent

**The rank correlation between the development window and the holdout
is -0.440.**

Choosing the best design on eight years of data was *worse than
choosing at random* for the next seven. The two designs ranked 11th and
13th on 2011-2018 came 1st and 5th on 2019-2026. The one ranked 2nd
came 12th.

This is not a statement about these thirteen designs. It is a statement
about the method every agent in this project rests on — including the
ten named investors, whose numbers come from a single window with no
out-of-sample test at all. An eight-year in-sample fit has no
demonstrated power to pick a strategy for the next seven years, and
this exercise is the measurement that says so.

## Why the chosen design failed, as far as can be told

Not a bug — the engine and the harness agree on the development window
(12.50% against 12.06%, benchmark 11.06% against 10.75%).

The low-volatility premium was absent from 2019-2026, and the period's
index return came overwhelmingly from high-volatility mega-cap growth —
exactly the names a low-volatility screen is built to exclude. The
screen also drifted: on 2011-2018 it held insurers, payroll processors
and staples; by 2026 seventeen of twenty-five names were utilities and
REITs, an interest-rate position taken during a rate shock. A sector
cap would have limited that, and the project already has one in
`agents/dreman/diversification.py`. Leaving it out was a choice made
for simplicity on the development window, and it was the wrong one.

## What is deliberately not being done about it

Value + momentum returned 23.88% on the holdout. Switching to it now
would be selecting on the holdout, which is the entire failure mode
this split existed to prevent, and it would produce a number with no
evidence behind it whatsoever. The holdout has been spent. Any design
chosen from that column is in-sample now.

A second honest test needs data neither window has seen — which, on a
price history that begins in 2010, means waiting.

## One row here is unreliable

`not extended` shows 229.58% volatility on the holdout, so the
spike-and-return filter is not catching whatever produced it. The
number is not to be used. It is left in the table rather than deleted
because removing rows that embarrass the method is how a search log
stops being one.

---

# Walk-forward: does *choosing* work?

The single split condemned one design. It did not settle whether the
method itself works, because one split is one draw. So the choice was
made repeatedly across the whole history — rank every design on the
trailing two years, hold the winner for one, repeat — which produces
the track record of the procedure a person reading a leaderboard
actually follows.

Contaminated row excluded: `not extended` still carries a bad print in
the 2019-2026 stretch (a 692% best window), and leaving it in let the
procedure select a broken design. Both figures are given.

| approach | CAGR% | maxDD% |
|---|---|---|
| select the trailing winner | **9.39** | **-45.82** |
| hold all twelve designs equally | 12.08 | -30.25 |
| **the index** | **14.44** | **-23.93** |

*(with the contaminated design left in, selecting returns -1.10% with a
-74.48% drawdown.)*

**The index wins on both return and risk.** Selecting costs five points
a year and doubles the drawdown. Diversifying across all twelve costs
two and a half points and still carries more drawdown than the index.

## And the overlay, one last time

The trend rule was dismissed earlier for a specific reason — a book
whose worst drawdown is 8% has nothing to protect. The diversified book
draws down 30%, which is exactly the case the rule exists for. So it
was worth one more test.

| approach | CAGR% | maxDD% | vol% |
|---|---|---|---|
| diversified | 12.07 | -30.25 | 20.15 |
| diversified + trend | 5.14 | **-33.51** | 16.29 |
| index | 13.99 | -23.93 | 14.92 |
| index + trend | 7.89 | -19.98 | 13.16 |

It halves the return and makes the diversified book's drawdown *worse*.
The cause is structural rather than bad luck: this project rebalances
quarterly, so a ten-month trend rule is evaluated four times a year and
can be up to three months late. It sells after the fall and buys after
the recovery. The literature that supports trend following evaluates it
monthly, and the deployment frequency here is what removes its edge.

---

# What the whole exercise concluded

Nothing tested beats holding the index.

* Thirteen designs on a sealed out-of-sample window: the chosen one lost
  by 12.50 points at t = -2.27.
* Rank correlation between the two windows: **-0.440**. Choosing on
  eight years of history was worse than choosing at random.
* Following the trailing winner across fifteen years: 9.39% against the
  index's 14.44%, with twice the drawdown.
* Diversifying across every design instead: 12.08%, still short.
* A trend overlay at this rebalance frequency: destroys returns in every
  configuration tried.

The finding is not that these particular factors are bad. It is that a
single historical window has no demonstrated power to choose between
them, and that every agent in this project — the ten named investors
included — rests on exactly one window with no out-of-sample test.

---

# Correction: two bugs invalidated part of the above

## The fundamentals panel covered only half the history

It was built for 2011-2018 first, and the holdout run over 2019-2026
was scored against it. The join forward-fills, correctly, so that the
months between quarterly filings hold the last known numbers — and
unbounded, it carried every 2018 filing forward for seven years without
failing or warning. "Earnings yield" became 2018 EBIT over today's
enterprise value: a long-horizon reversal signal wearing a value label.

**Every value and quality figure in the first holdout table was void.**
The price-only rows, including the chosen design's 3.55%, were not
affected.

Fixed by bounding the carry at 400 days — enough for a company to miss
a quarter or file late, since a screen that drops the unpunctual is
selecting on punctuality, and not enough to value a company nobody has
heard from in years.

## Market capitalisations that could not be true

`PKG` files a share count a thousand times too large — 94.1 billion
shares against an actual 95 million or so — which computes to a
$6,276bn company. Being the largest thing in the universe, it took a
41% weight in a capitalisation-weighted book and turned "hold the 25
biggest US companies" into **-0.94% a year with a 42% drawdown**.
`JAGX` computes to $1,649,620bn on a price of $9,627,188, which is what
a series looks like after enough reverse splits.

The tell is not the size but the trading: across 104,676 observations
the median company turns over 0.72% of its value a day and the 1st
percentile still manages 0.058%. A capitalisation contradicted by its
own dollar volume is now refused. With the gate in place the same
design returns **+11.33%** on the development window.

---

# Both windows, corrected, all twenty-one scoreable designs

| design | dev CAGR% | holdout CAGR% | beat both? |
|---|---|---|---|
| **biggest 25 (cap-weighted)** | **11.33** | **20.74** | **yes** |
| biggest 25 (equal) | 10.60 | 18.55 | no (dev by 0.15) |
| quality + mom + low ivol | 12.39 | 15.14 | no |
| value + quality + mom + low ivol | 11.49 | 15.97 | no |
| mom + low ivol | 11.56 | 10.44 | no |
| low ivol, cap-weighted | 10.14 | 15.57 | no |
| low total vol | 9.27 | 3.81 | no |
| quality | 8.70 | 7.36 | no |
| value | 8.16 | 22.08 | no |
| value + quality | 7.74 | 16.96 | no |
| value + quality + mom | 5.35 | 21.91 | no |
| value + mom | 2.80 | 20.11 | no |
| momentum 6-1 | -1.29 | 3.34 | no |
| **low idio vol** — *the one first chosen* | **12.06** | **3.55** | no |
| momentum 12-1 | -4.52 | 17.04 | no |
| *benchmark* | *10.75* | *16.01* | — |

**One design out of twenty-one cleared the benchmark in both.**

# Full period, 2011-2026, contaminated rows excluded

| approach | CAGR% | maxDD% | t vs index |
|---|---|---|---|
| **biggest 25 (cap-weighted)** | **17.67** | -27.93 | **+1.82** |
| quality + mom + low ivol | 15.25 | -25.38 | +0.39 |
| *the index* | *14.44* | *-23.93* | — |
| hold every design equally | 12.81 | -26.14 | -0.45 |
| select the trailing winner | 7.81 | -42.66 | -1.40 |
| low idio vol | 7.38 | -40.95 | -2.15 |

The winner beat the index in 12 of 16 years, in 60.7% of quarters and
in 70.4% of rolling two-year windows, with a median two-year excess of
+5.72% and turnover of 6% a quarter.

## What it is, and what it is not

It is not stock picking. It holds the largest companies and sizes them
by what they are worth. Its edge over the S&P 500 is being *more*
concentrated in the biggest names than the index is — the top five are
around 59% of the book — and that paid because mega-caps led for
fifteen years. There is no anomaly being harvested and no mispricing
found. The documented size premium favours small over large; this is
the opposite trade, justified only by the period it was measured in.

And by the time it was identified, both windows had been read. It is
not an out-of-sample result. The strongest true statement available is
that it was consistent across two very different regimes and has no
parameter fitted to either.

---

# What happened to the two agents that are in the tree but not on the council

Three strategy packages sit under `agents/` from this work. Only one is
wired into the runner and the dashboard. The other two are kept as
record, and this section exists so that a reader who finds them does not
mistake either for something the project stands behind.

**`agents/market_core` — shipping.** The design that beat the index in
both windows. Everything above is about it.

**`agents/quiet` — measured, and it failed.** The low-idiosyncratic-
volatility book. It won the development window at 12.06% against
10.75%, was frozen, and returned 3.55% against 16.01% on data it had
not seen: -12.50 points at t = -2.27. Its own docstring carries the
number. It is the single most useful artefact of this exercise, because
it is what choosing on one window looks like when the choice is wrong.

**`agents/composite` — never measured.** A value-profitability-momentum
composite written *before* `core.research` existed, and the reason this
branch is called `feat/factor-composite-agent`. It was overtaken by the
harness rather than beaten by it: no row in any table above is its
result, and no backtest run of it exists. Its docstring now says so.
Its factor-scoring code and tests pass and are left in place; the
argument in its docstring — that an eleventh *value* investor would add
a correlated opinion rather than information — is what motivated the
whole search, and it still holds. What did not survive was the
assumption that a three-factor composite would therefore be the answer.

No further work is planned on either. Reviving one means running it
through `core.research` on both windows first.
