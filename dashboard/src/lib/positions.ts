// One row per stock an agent has engaged with, instead of one row per
// decision per day.
//
// Why this exists
// ---------------
//
// The journal was a flat feed of every decision row ever written. For
// Benjamin Graham that is 1,240 rows across 66 days, and it reads as
// chaos: ASGN appears 68 times, three times on a single date.
//
// Those are not 68 purchases. A decision row is the agent's verdict for
// that day — "ASGN is a buy under Graham's criteria" — re-emitted on
// every run because the thesis does not change daily. Every one carries
// shares=null and price=null, because none of them was a trade.
//
// Read as a lifecycle instead, the same data says something useful and
// short: Graham first flagged ASGN on 2026-05-06, has reaffirmed it
// every trading day since, holds it now, and is up X%. Sixty-eight
// repetitions is not noise — it is the conviction, and it is exactly
// what distinguishes a value investor from a trader.
//
// What the data can and cannot say
// --------------------------------
//
// Executed trades were being erased daily until 2026-08-07: the
// close-of-day run overwrote each morning's snapshot with an empty
// trade list, so 68 of 68 days recorded none. Collection starts from
// TRADE_HISTORY_STARTS_AT below.
//
// So for now the buy date comes from the open position's own
// entry_date, which is reliable, and a position that was opened and
// closed before that date leaves no trace at all. Anything derived from
// trades is marked with `evidence` so the UI can say which it is rather
// than presenting a guess as a fact.

import type { AgentSlug, DecisionRow, LivePosition } from "@/lib/types";

/** First date the system reliably records executed trades.
 *
 *  Before this, `save_snapshot` overwrote each day's record when the
 *  close-of-day run passed an empty trade list, so no execution
 *  survived. An empty trade history for an earlier date means the
 *  record was destroyed, not that nothing happened — and that
 *  distinction has to be visible, or a future reader concludes the
 *  agents sat idle for three months.
 */
export const TRADE_HISTORY_STARTS_AT = "2026-08-07";

/** How the buy date was established. */
export type Evidence =
  /** From the open position itself — exact. */
  | "position"
  /** From a recorded trade — exact. */
  | "trade"
  /** Earliest decision row; the agent wanted it then, but the fill may
   *  have been later. Everything before TRADE_HISTORY_STARTS_AT falls
   *  back to this. */
  | "first_flagged";

export type Lifecycle =
  /** Currently in the portfolio. */
  | "held"
  /** Was flagged, is not held now, and no exit was recorded — the
   *  record may simply predate trade collection. */
  | "closed"
  /** Flagged repeatedly and never bought: on the watchlist, or it never
   *  made the top N. */
  | "never_opened";

export interface PositionStory {
  ticker: string;
  agent: AgentSlug;
  lifecycle: Lifecycle;
  /** When the agent first said yes to this name. */
  firstFlagged: string;
  /** Most recent verdict. */
  lastFlagged: string;
  /** Distinct dates the agent reaffirmed the thesis. The headline
   *  number: "held to for 47 trading days". */
  daysAffirmed: number;
  /** When it entered the book, when known. */
  openedAt: string | null;
  openedEvidence: Evidence | null;
  entryPrice: number | null;
  currentPrice: number | null;
  pnlPct: number | null;
  weightPct: number | null;
  /** Criteria that carried the most recent decision. */
  criteriaMet: string[];
  /** What the agent said it is waiting for to sell. */
  exitTrigger: string | null;
  /** Newest first — the full paper trail, for the detail view. */
  timeline: DecisionRow[];
}

const BUY_KINDS = new Set(["BUY", "FILL", "ADD"]);

function dayOf(timestamp: string): string {
  return timestamp.slice(0, 10);
}

/**
 * Collapse an agent's decision rows and open positions into one story
 * per ticker, newest activity first.
 *
 * Ordering puts held positions above closed ones and closed above
 * never-opened, then by recency — what you own is what you care about.
 */
export function buildPositionStories(
  agent: AgentSlug,
  decisions: DecisionRow[],
  positions: LivePosition[],
): PositionStory[] {
  const held = new Map(positions.map((p) => [p.ticker, p]));
  const byTicker = new Map<string, DecisionRow[]>();

  for (const d of decisions) {
    const rows = byTicker.get(d.ticker);
    if (rows) rows.push(d);
    else byTicker.set(d.ticker, [d]);
  }

  // A position can be held without a decision row, if its buy predates
  // the retained logs. It still deserves a card.
  for (const p of positions) {
    if (!byTicker.has(p.ticker)) byTicker.set(p.ticker, []);
  }

  const stories: PositionStory[] = [];
  for (const [ticker, rowsRaw] of byTicker) {
    const rows = [...rowsRaw].sort((a, b) =>
      b.timestamp.localeCompare(a.timestamp),
    );
    const position = held.get(ticker);
    const buys = rows.filter((r) => BUY_KINDS.has(r.decision));
    const affirmed = new Set(buys.map((r) => dayOf(r.timestamp)));
    const newest = rows[0] ?? null;
    const oldest = rows[rows.length - 1] ?? null;

    let lifecycle: Lifecycle;
    if (position) lifecycle = "held";
    else if (buys.length > 0) lifecycle = "closed";
    else lifecycle = "never_opened";

    let openedAt: string | null = null;
    let openedEvidence: Evidence | null = null;
    if (position) {
      openedAt = position.entry_date;
      openedEvidence = "position";
    } else if (oldest && buys.length > 0) {
      openedAt = dayOf(buys[buys.length - 1].timestamp);
      openedEvidence = "first_flagged";
    }

    stories.push({
      ticker,
      agent,
      lifecycle,
      firstFlagged: oldest ? dayOf(oldest.timestamp) : (position?.entry_date ?? ""),
      lastFlagged: newest ? dayOf(newest.timestamp) : (position?.entry_date ?? ""),
      daysAffirmed: affirmed.size,
      openedAt,
      openedEvidence,
      entryPrice: position?.entry_price ?? newest?.entry_price ?? null,
      currentPrice: position?.current_price ?? null,
      pnlPct: position?.pnl_pct ?? null,
      weightPct: position?.weight_pct ?? null,
      criteriaMet: newest?.criteria_met ?? [],
      exitTrigger: newest?.exit_trigger ?? null,
      timeline: rows,
    });
  }

  const rank: Record<Lifecycle, number> = {
    held: 0,
    closed: 1,
    never_opened: 2,
  };
  stories.sort((a, b) => {
    if (rank[a.lifecycle] !== rank[b.lifecycle]) {
      return rank[a.lifecycle] - rank[b.lifecycle];
    }
    return b.lastFlagged.localeCompare(a.lastFlagged);
  });
  return stories;
}

/** Headline counts for the summary strip above the cards. */
export interface StoryTotals {
  held: number;
  closed: number;
  neverOpened: number;
  /** Decision rows the cards replace — the "1,240 became 41" figure. */
  decisionRows: number;
  /** Longest unbroken conviction in the book. */
  longestHeld: { ticker: string; days: number } | null;
}

export function summarise(stories: PositionStory[]): StoryTotals {
  let longest: { ticker: string; days: number } | null = null;
  let rows = 0;
  for (const s of stories) {
    rows += s.timeline.length;
    if (!longest || s.daysAffirmed > longest.days) {
      longest = { ticker: s.ticker, days: s.daysAffirmed };
    }
  }
  return {
    held: stories.filter((s) => s.lifecycle === "held").length,
    closed: stories.filter((s) => s.lifecycle === "closed").length,
    neverOpened: stories.filter((s) => s.lifecycle === "never_opened").length,
    decisionRows: rows,
    longestHeld: longest,
  };
}

// ---------------------------------------------------------------------------
// What the agent is watching
// ---------------------------------------------------------------------------
//
// A daily log of "still holding" is not a record, it is noise — 68 rows
// saying the same thing. What is worth showing is movement, and what
// the agent is waiting for.
//
// The criteria strings already carry both halves: "P/E=7.34 (≤ 15.0)"
// is the current reading and the level that would break it. Parsed out
// they become the thing a reader can learn from — not "Graham likes
// cheap stocks" but "this one sells if P/E doubles from here, and it is
// currently using 49% of that room".

export interface WatchCondition {
  /** "P/E", "current ratio". */
  label: string;
  value: number;
  /** "≤" means the value must stay at or below `threshold`. */
  op: "≤" | "≥";
  threshold: number;
  /** 0..1.5 — how much of the allowed room is used; 1.0 is at the
   *  limit. Null when the entry carries no comparison at all. */
  used: number | null;
  /** The original string, for criteria with no number
   *  ("positive trailing net income"). */
  raw: string;
}

const CRITERION = /^(.+?)=(-?[\d.]+)\s*\((≤|≥)\s*(-?[\d.]+)\)$/;

/** Parse `criteria_met` strings into conditions with headroom. */
export function parseConditions(criteria: string[]): WatchCondition[] {
  const out: WatchCondition[] = [];
  for (const raw of criteria) {
    const m = CRITERION.exec(raw.trim());
    if (!m) {
      out.push({
        label: raw,
        value: Number.NaN,
        op: "≤",
        threshold: Number.NaN,
        used: null,
        raw,
      });
      continue;
    }
    const [, label, valueStr, opStr, thresholdStr] = m;
    const value = Number(valueStr);
    const threshold = Number(thresholdStr);
    const op = opStr as "≤" | "≥";
    // Fraction of the permitted room consumed. A ceiling uses
    // value/threshold; a floor inverts it, so in both directions a
    // higher number means closer to breaking.
    let used: number | null = null;
    if (Number.isFinite(value) && Number.isFinite(threshold) && threshold !== 0 && value !== 0) {
      const ratio = op === "≤" ? value / threshold : threshold / value;
      used = Math.max(0, Math.min(1.5, ratio));
    }
    out.push({ label, value, op, threshold, used, raw });
  }
  return out;
}

/** One entry in the movements list — an event, not a daily heartbeat. */
export interface Movement {
  date: string;
  kind: "opened" | "reopened" | "exited" | "changed";
  note: string | null;
}

/**
 * Reduce a timeline to the days something actually happened.
 *
 * Rows are one per run, so a position held 68 trading days produces 68
 * of them. Only the first, and any day the verdict changed, are events.
 * The rest are the same answer restated and belong nowhere on screen.
 */
export function movements(story: PositionStory): Movement[] {
  const rows = [...story.timeline].reverse(); // oldest first
  if (rows.length === 0) return [];
  const out: Movement[] = [
    { date: dayOf(rows[0].timestamp), kind: "opened", note: rows[0].rationale ?? null },
  ];
  // Track whether we are in or out, so a buy after a sell reads as a
  // re-entry rather than as "changed its view". CRMD showed three
  // exits and one opening, which left a reader asking where the stock
  // was bought back — the answer was there, mislabelled.
  let prev = rows[0].decision;
  let holding = BUY_KINDS.has(rows[0].decision);
  for (const r of rows.slice(1)) {
    if (r.decision === prev) continue;
    prev = r.decision;
    const isBuy = BUY_KINDS.has(r.decision);
    const isSell = r.decision === "SELL" || r.decision === "EXIT";
    let kind: Movement["kind"];
    if (isSell) {
      kind = "exited";
      holding = false;
    } else if (isBuy && !holding) {
      kind = "reopened";
      holding = true;
    } else {
      kind = "changed";
    }
    out.push({ date: dayOf(r.timestamp), kind, note: r.rationale ?? null });
  }
  return out.reverse(); // newest first
}
