// The live ranking over a chosen window.
//
// The ranking on the overview sorted by return since seed and nothing
// else, so it answered one question — who is ahead overall — and the
// founder's other questions ("who had the best week", "who is ahead
// since the August fix") had no page. This is the window math; the
// table that shows it is components/LiveRanking.tsx.
//
// A window's return is measured between two snapshots: the last one on
// or before the window's start, and the newest. Measuring between
// snapshots rather than against `cumulative_return_pct` is what lets the
// same table rank a week and six months with one rule.

/** The two fields a window needs. A `DailySnapshot` satisfies it, and
 *  so does the `[date, nav]` pair the overview sends to the browser —
 *  thirteen books of full snapshots would be half a megabyte of page. */
export interface NavPoint {
  date: string;
  nav: number;
}

export type RankingPeriod = "1W" | "1M" | "3M" | "FIX" | "ALL";

export const RANKING_PERIODS: readonly RankingPeriod[] = ["1W", "1M", "3M", "FIX", "ALL"];

/** The first session after the 30-day holding floor shipped.
 *
 *  Before commit 2b53e99a6 (2026-08-07) a name that slipped one rank for
 *  a day was sold and bought back when it returned: 75 of Graham's 87
 *  sales to that date were of a position younger than a month. Returns
 *  earned under that rule are not the doctrines' returns, so the
 *  ranking offers the window that starts the morning after. */
export const FIX_DATE = "2026-08-08";

export interface RankedRow {
  agent: string;
  /** Snapshot the window opens on. */
  startDate: string;
  startNav: number;
  endDate: string;
  endNav: number;
  pnlUsd: number;
  returnPct: number;
  /** Sessions inside the window, counting both ends. */
  sessions: number;
  /** True when the book has no snapshot old enough for the window, so
   *  the window opened on its first snapshot instead. The row is still
   *  ranked — a book younger than the window is a book with that much
   *  history — but the page says so. */
  clipped: boolean;
}

function periodDays(p: RankingPeriod): number | null {
  switch (p) {
    case "1W":
      return 7;
    case "1M":
      return 30;
    case "3M":
      return 90;
    case "FIX":
    case "ALL":
      return null;
  }
}

function minusDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/** The date the window opens on, given its newest snapshot. */
export function windowStart(period: RankingPeriod, endDate: string): string | null {
  if (period === "ALL") return null;
  if (period === "FIX") return FIX_DATE;
  return minusDays(endDate, periodDays(period) as number);
}

/** One book over the window, or null when it has no snapshots.
 *
 *  `seed` is the cash the book was opened with. The ALL window measures
 *  from it rather than from the first snapshot, because a first snapshot
 *  is already a day of market — Neff's reads $10,004 — and the figure
 *  has to agree with the `cumulative_return_pct` on the book's own page. */
export function rankOne(
  agent: string,
  snapshots: readonly NavPoint[],
  period: RankingPeriod,
  seed?: number,
): RankedRow | null {
  const sorted = snapshots
    .filter((s) => Number.isFinite(s.nav) && s.nav > 0)
    .slice()
    .sort((a, b) => a.date.localeCompare(b.date));
  if (sorted.length === 0) return null;
  const end = sorted[sorted.length - 1];
  const from = windowStart(period, end.date);

  // The window opens on the last snapshot on or before `from`, so a
  // 7-day window that lands on a weekend opens on the Friday close —
  // the value the book actually had going into the week. With nothing
  // that old, it opens on the first snapshot and says so.
  let start = sorted[0];
  let clipped = false;
  if (from === null) {
    start = sorted[0];
  } else {
    const onOrBefore = sorted.filter((s) => s.date <= from);
    if (onOrBefore.length > 0) {
      start = onOrBefore[onOrBefore.length - 1];
    } else {
      clipped = true;
    }
  }
  const sessions = sorted.filter((s) => s.date >= start.date && s.date <= end.date).length;
  const startNav =
    period === "ALL" && seed !== undefined && Number.isFinite(seed) && seed > 0
      ? seed
      : start.nav;
  return {
    agent,
    startDate: start.date,
    startNav,
    endDate: end.date,
    endNav: end.nav,
    pnlUsd: end.nav - startNav,
    returnPct: ((end.nav - startNav) / startNav) * 100,
    sessions,
    clipped,
  };
}

/** Every book over the window, best first, minus the ones `exclude`
 *  names — the page passes `isControl` from lib/agents, so a control
 *  book is never placed against books with months more market behind
 *  them. A book with no snapshots is left out rather than ranked at
 *  zero. */
export function rankOverWindow(
  snapshotsByAgent: ReadonlyMap<string, readonly NavPoint[]>,
  period: RankingPeriod,
  exclude: (agent: string) => boolean = () => false,
  seedOf: (agent: string) => number | undefined = () => undefined,
): RankedRow[] {
  const rows: RankedRow[] = [];
  for (const [agent, snaps] of snapshotsByAgent) {
    if (exclude(agent)) continue;
    const row = rankOne(agent, snaps, period, seedOf(agent));
    if (row) rows.push(row);
  }
  return rows.sort((a, b) => b.returnPct - a.returnPct || a.agent.localeCompare(b.agent));
}
