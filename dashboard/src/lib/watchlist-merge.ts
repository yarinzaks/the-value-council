// Merging the two places an agent can be waiting for a name.
//
// A WATCH row in the decision journal is the older source: the tail of
// a ranking, the names that qualified and missed the cut, written while
// the strategy screened. `portfolio.watchlist` is the newer one,
// rewritten whole on every run, and it is where a near miss with a
// price attached lives — the company that clears every test a price
// cannot change and is blocked only by the quotation.
//
// The watchlist page read the journal alone, which left market_core's
// 28 stored names and mohnish_pabrai's 30 unviewable, neither agent
// having ever written a WATCH row, and would have left Graham's own
// list — the one that carries an entry price — the same way.
//
// This module has no runtime imports, only `import type`, which is
// erased. That is what lets `node --test` load it directly: the loader
// it would otherwise live in reads the filesystem and imports
// `./agents` without a file extension, which Node will not resolve.

import type { AgentSlug } from "./types";

/** One agent's interest in one ticker, from either source. */
export interface WatchSignal {
  slug: AgentSlug;
  display_name: string;
  ticker: string;
  rationale: string | null;
  /** ISO date or timestamp. Journal rows carry a full timestamp and
   *  portfolio entries a bare date, so only the date part is compared. */
  when: string;
  /** What the agent would pay. A journal WATCH row carries prose and no
   *  number, so this is null for everything that came from there. */
  entry_price_target: number | null;
  /** True for a `portfolio.watchlist` entry. It wins a same-day tie:
   *  that file is rewritten on every run, so it is the live statement of
   *  what the agent is waiting for, and it is the side with a price. */
  live: boolean;
}

export interface WatchEntry {
  ticker: string;
  agents: {
    slug: AgentSlug;
    display_name: string;
    rationale: string | null;
    entry_price_target: number | null;
  }[];
  most_recent: string;
  /** A WATCH usually has the same criteria as a BUY but didn't make the
   *  top-N. Track how many agents are watching the name to surface
   *  cross-agent agreement. */
  agent_count: number;
}

/** Group signals by ticker, keeping the newest one per agent.
 *
 * Deduplicated per (ticker, agent) rather than summed. Four agents
 * write to both sources, and this page ranks names by how many agents
 * agree on them — counting Dreman twice would have promoted a name one
 * agent is watching above a name two are.
 */
export function mergeWatchSignals(signals: WatchSignal[]): WatchEntry[] {
  const latest = new Map<string, WatchSignal>();
  for (const signal of signals) {
    if (!signal.ticker) continue;
    const key = `${signal.ticker}|${signal.slug}`;
    const current = latest.get(key);
    if (current === undefined) {
      latest.set(key, signal);
      continue;
    }
    const day = signal.when.slice(0, 10);
    const held = current.when.slice(0, 10);
    // Only the date part. A journal timestamp is
    // "2026-10-29T00:00:00+00:00" and a portfolio date is "2026-10-29";
    // compared whole, the longer string sorts later, which would hand
    // every shared day to the journal row and drop the price with it.
    if (day > held || (day === held && signal.live && !current.live)) {
      latest.set(key, signal);
    }
  }

  const byTicker = new Map<string, WatchSignal[]>();
  for (const signal of latest.values()) {
    const list = byTicker.get(signal.ticker) ?? [];
    list.push(signal);
    byTicker.set(signal.ticker, list);
  }

  const entries: WatchEntry[] = [];
  for (const [ticker, list] of byTicker.entries()) {
    list.sort((a, b) => b.when.localeCompare(a.when));
    entries.push({
      ticker,
      agents: list.map((s) => ({
        slug: s.slug,
        display_name: s.display_name,
        rationale: s.rationale,
        entry_price_target: s.entry_price_target,
      })),
      most_recent: list[0].when,
      agent_count: list.length,
    });
  }

  // Surface highest-conviction (most agents agreeing) first, then newest.
  entries.sort((a, b) => {
    if (b.agent_count !== a.agent_count) return b.agent_count - a.agent_count;
    return b.most_recent.localeCompare(a.most_recent);
  });
  return entries;
}
