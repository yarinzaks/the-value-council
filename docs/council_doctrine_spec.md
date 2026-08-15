# The Council — what is still missing

Fill in the eight blanks below and the agent becomes a standalone
investor instead of an average of the other eleven. Answer in plain
words or in numbers; precise numbers are better, because every one of
them becomes a line of code and a test.

## What it already has — do not rebuild these

| Built | What it does |
|---|---|
| Regime dial | 4 FRED signals; fewer than 2 risk-on blocks new entries |
| Position limits | 25% cap at entry, 35% forced trim, 5% cash floor |
| Circuit breaker | −25% drawdown blocks new entries |
| Filings watch | SEC 8-K items and terminal forms veto a name |
| News veto | Bankruptcy / delisting / fraud headlines veto |
| Punch card | 20 entries for its lifetime, 2 per run |
| Journal | Every thesis needs 3 kill criteria before it can open |
| Execution | Same runner, cost model, marks and rebalancing as the eleven |

**All of the above is a veto — it only ever says no.**

## What is missing — the eight blanks

### 1. Universe
Which companies does it even look at?
Examples: all US listings above $500M market cap · S&P 500 only ·
anything with 5 years of filings.

> **Answer:**

### 2. Screen
What makes a company a candidate? Hard rules with numbers.
Examples: P/E below 15 · debt-to-equity under 0.5 · 5 straight
profitable years · price below book value.

> **Answer:**

### 3. Ranking
Two hundred companies pass the screen. Which does it buy first?
Examples: cheapest P/E first · highest return on capital first ·
a combined rank of both.

> **Answer:**

### 4. How many positions
Examples: 10 · 20 · 30. Fewer means more conviction and more risk.

> **Answer:**

### 5. Position sizing
Examples: equal weight · double weight for the top five ·
proportional to how cheap it is.

> **Answer:**

### 6. Exit rule — the most important blank
**It currently has none.** Without it the agent buys and never sells,
and every position becomes permanent.
Examples: sell when it leaves the top 20 · sell after 30% gain ·
sell after 2 years · sell when P/E goes above 25.

> **Answer:**

### 7. Rebalance frequency
Examples: monthly · quarterly · yearly · only when something changes.

> **Answer:**

### 8. Its one sentence
What does this investor believe that the other eleven do not? This
becomes its description on the dashboard and the reason it exists.

> **Answer:**

## After you fill this in

I turn each answer into code and a test, show you the rules for approval,
and only then let it touch the paper book. Nothing gets invented in the
gaps — if an answer is unclear I ask rather than guess.

If the three Fintest documents (`THE_VALUE_COUNCIL.md`, `COUNCIL_DATA.md`,
`COUNCIL_RUNBOOK.md`) already contain any of this, send them instead and
I will build exactly what they say.
