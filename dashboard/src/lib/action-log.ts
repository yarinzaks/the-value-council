// One agent's actions, in the order they happened.
//
// What the page had, and what it was missing
// ------------------------------------------
//
// The agent page showed its last ten *decisions* — a flat list off the
// journal, newest first, each with a verdict chip and a sentence. That
// answers "what does it think right now". It does not answer the
// question the founder asked: when did this happen, and what happened
// before it.
//
// A name's life here has a shape. It is noticed and goes on the
// watchlist; some weeks later the trigger is met and it is bought;
// later still it is trimmed, or it leaves the target list and is sold.
// Four events, each with a date, and the page carried no view in which
// they sat in order.
//
// Two sources, deliberately
// -------------------------
//
// Executions come from the trade ledger, because that is the record of
// what was actually filled — shares, price, realised P&L — and the
// journal's own FILL row carries a rank and nothing else. Watchlist
// events come from the journal, because nothing is executed when a name
// is merely noticed and the ledger has no row for it.
//
// Merging them is what makes the order readable. Sorting by date alone
// is not enough: a buy and the watch that preceded it can land on one
// day, and a day's events have to read in the order they occurred
// rather than the order two files happened to be opened in.

// Structural types rather than imports from `./data`, which reaches for
// `node:fs` and so cannot be pulled into a client component. Both are a
// subset of the real shapes, which is all this file reads — and it keeps
// the module a leaf with no dependencies and a test that needs no
// fixtures on disk.
export interface TradeLike {
  date: string;
  ticker: string;
  side: string;
  shares: number;
  price: number;
  realized_pnl_usd?: number | null;
}

export interface DecisionLike {
  ticker: string;
  decision: string;
  timestamp: string;
  target_price?: number | null;
  rationale?: string | null;
}

export type ActionKind = "watch" | "unwatch" | "buy" | "sell" | "trim" | "add";

export interface AgentAction {
  /** Calendar date, YYYY-MM-DD. */
  date: string;
  kind: ActionKind;
  ticker: string;
  /** Whole shares for an execution; null for a watchlist event. */
  shares: number | null;
  /** Fill price, or the price being waited for. Null when neither. */
  price: number | null;
  /** Realised P&L on a sale, in dollars. Null elsewhere — a buy
   *  realises nothing, and printing 0.00 reads as a break-even trade. */
  realized: number | null;
  /** The agent's own reason, as the journal wrote it — English, for
   *  the component to translate. Empty when the record carried none. */
  why: string;
}

/** Within one day, the order events actually occur in.
 *
 *  A name is noticed before it is bought and sold before it is
 *  forgotten, so when two land on the same date this is the order that
 *  reads correctly. Sorting by date alone put a sale above the purchase
 *  that preceded it roughly one day in five.
 */
const WITHIN_DAY: Record<ActionKind, number> = {
  unwatch: 0,
  watch: 1,
  buy: 2,
  add: 3,
  trim: 4,
  sell: 5,
};

/** The journal verdicts that mean "this name is being waited for". */
const WATCH_DECISIONS = new Set(["WATCH"]);

/** …and the ones that mean it has stopped being waited for. */
const UNWATCH_DECISIONS = new Set(["REJECT"]);

function day(timestamp: string): string {
  return String(timestamp ?? "").slice(0, 10);
}

/** A ledger side and share count, as one of our kinds.
 *
 *  `held` is the position's share count *before* the trade, which is
 *  what tells a partial sale from a full one and an opening purchase
 *  from a top-up. Without it a trim reads as a sale of the whole line.
 */
function executionKind(row: TradeLike, heldBefore: number): ActionKind {
  const side = String(row.side ?? "").toUpperCase();
  if (side === "BUY") return heldBefore > 0 ? "add" : "buy";
  return heldBefore > row.shares + 1e-9 ? "trim" : "sell";
}

/**
 * Every action one agent took, newest first.
 *
 * ``limit`` caps the result after ordering, so the newest events
 * survive rather than whichever source had more rows.
 */
export function buildActionLog(
  trades: readonly TradeLike[],
  decisions: readonly DecisionLike[],
  options: { limit?: number } = {},
): AgentAction[] {
  const { limit = 60 } = options;
  const out: AgentAction[] = [];

  // ---- executions, oldest first so the running position is right ----
  const held = new Map<string, number>();
  const ordered = [...trades].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  for (const row of ordered) {
    const ticker = String(row.ticker ?? "");
    if (!ticker) continue;
    const before = held.get(ticker) ?? 0;
    const kind = executionKind(row, before);
    const side = String(row.side ?? "").toUpperCase();
    held.set(ticker, side === "BUY" ? before + row.shares : before - row.shares);
    out.push({
      date: day(row.date),
      kind,
      ticker,
      shares: row.shares,
      price: row.price,
      realized: side === "BUY" ? null : (row.realized_pnl_usd ?? null),
      why: "",
    });
  }

  // ---- watchlist events, one per (ticker, day) ----------------------
  // The journal writes a WATCH row on every run a name stays on the
  // list, which on a six-week wait is thirty identical rows. Only the
  // first is an event; the rest are the absence of one.
  const seenWatch = new Set<string>();
  const journalOrdered = [...decisions].sort((a, b) =>
    a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0,
  );
  for (const row of journalOrdered) {
    const verdict = String(row.decision ?? "").toUpperCase();
    const ticker = String(row.ticker ?? "");
    if (!ticker) continue;
    if (WATCH_DECISIONS.has(verdict)) {
      if (seenWatch.has(ticker)) continue;
      seenWatch.add(ticker);
      out.push({
        date: day(row.timestamp),
        kind: "watch",
        ticker,
        shares: null,
        price: row.target_price ?? null,
        realized: null,
        // The journal's rationale, as written. It is English, and the
        // component runs it through translateRationale — the same path
        // every other journal string on this site takes.
        why: row.rationale ?? "",
      });
    } else if (UNWATCH_DECISIONS.has(verdict) && seenWatch.has(ticker)) {
      seenWatch.delete(ticker);
      out.push({
        date: day(row.timestamp),
        kind: "unwatch",
        ticker,
        shares: null,
        price: null,
        realized: null,
        why: "",
      });
    }
  }

  out.sort((a, b) => {
    if (a.date !== b.date) return a.date < b.date ? 1 : -1; // newest first
    return WITHIN_DAY[b.kind] - WITHIN_DAY[a.kind];
  });
  return out.slice(0, limit);
}

/** The log grouped into days, in the same order. */
export function byDay(actions: readonly AgentAction[]): [string, AgentAction[]][] {
  const groups = new Map<string, AgentAction[]>();
  for (const action of actions) {
    const bucket = groups.get(action.date);
    if (bucket) bucket.push(action);
    else groups.set(action.date, [action]);
  }
  return [...groups.entries()];
}
