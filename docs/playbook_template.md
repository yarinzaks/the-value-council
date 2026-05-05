# [Investor Name] — Playbook

> Replace the bracketed name and remove all "QUALITY BAR" callouts
> before merging. Keep the heading numbering — the LLM and the
> validation script both rely on it.

## 1. Personal Background

<!-- QUALITY BAR: 200-400 words. Birth/education/family insofar as it
shaped investment philosophy. Cite at least one biographical source
(book, archival profile, authorized biography). -->

## 2. Career Journey

<!-- QUALITY BAR: Timeline form. Major roles, firms founded or led,
peak AUM, and the inflection points (e.g., for Buffett: partnership →
Berkshire → permanent capital). Include years. -->

## 3. Investment Philosophy

<!-- QUALITY BAR: At least 5 direct quotes from primary sources
(annual letters, books, interviews). Each quote must include a
citation: source name, year, and page or URL. The philosophy section
is what the LLM actually channels — it must read like the investor's
own voice. -->

## 4. Quantitative Methodology

<!-- QUALITY BAR: Concrete, machine-checkable. List every numeric
threshold (e.g., "P/E < 15", "current ratio > 2", "F-Score >= 8")
with the rationale. These map directly into screener rules. Include
the rejection threshold for each, not just the preferred range. -->

## 5. Qualitative Methodology

<!-- QUALITY BAR: At least 5 explicit qualitative criteria
(e.g., "wide moat", "honest management", "circle of competence")
with operational definitions — how does the agent decide whether a
company has a "wide moat"? What does the LLM look for in the 10-K
or transcript text? -->

## 6. Portfolio Management Rules

<!-- QUALITY BAR: Position sizing rules (max % per name, max sector
concentration, currency exposure), rebalancing cadence, and cash
policy (does the investor hold cash? how much, and when?). -->

## 7. Exit Rules

<!-- QUALITY BAR: At least 4 numbered exit triggers, each
machine-checkable where possible (e.g., "trim when P/E exceeds 1.5x
sector median"). Include both fundamental triggers and stop-loss /
time-based triggers. -->

## 8. Sector Preferences

<!-- QUALITY BAR: Sectors the investor prefers, avoids, or is neutral
on, with the reasoning. This drives the universe filter — agents
should not even screen names in sectors they reject. -->

## 9. Famous Trades

<!-- QUALITY BAR: At least 3 case studies. Include: ticker, year of
entry, thesis at entry (paraphrased from primary sources), exit year
and rationale, realized return. These give the LLM pattern matches
for novel situations. -->

## 10. Anti-Patterns

<!-- QUALITY BAR: At least 5 explicit "things this investor refuses
to do" with citations (e.g., "never short", "never invest in
technology I don't understand", "never use leverage above X"). The
anti-pattern list is as important as the affirmative criteria. -->

## 11. Adaptation to the Modern Era

<!-- QUALITY BAR: Most of these investors built their reputations
pre-2000. This section captures how their philosophy adapts (or
doesn't) to current realities: software businesses, asset-light
firms, low interest rates, retail meme cycles, options flow,
high-frequency markets. -->

## 12. LLM Persona Instructions

<!-- QUALITY BAR: Direct instructions to the LLM about voice, style,
and decision discipline. Example: "Speak in clipped, plain English.
Never use jargon. If a metric is missing, say so explicitly. Refuse
to BUY when any non-negotiable criterion in section 4 fails — even
if everything else is attractive." -->
