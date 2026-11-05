// Publish what the assistant needs to answer questions about this site.
//
// The dashboard is a static export: every number is baked into HTML at
// build time, which is unreadable to anything but a browser. The chat
// endpoint runs as a Cloudflare Pages Function and needs the same facts
// as data, so this writes them once, here, from the same tree the pages
// were built from. One file, fetched per request from the site's own
// assets — no second source of truth to drift.
//
// What goes in: the roster, each agent's book, and the reasons behind
// recent trades. What stays out: anything not already rendered on a page
// the reader can open. The assistant should never be able to tell them
// something the site itself would not.

import { readFileSync, readdirSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const DATA_DIR = process.env.VALUE_COUNCIL_DATA_DIR;
const OUT = process.argv[2];

if (!DATA_DIR || !OUT) {
  console.error("usage: VALUE_COUNCIL_DATA_DIR=… export-chat-context.mjs <out.json>");
  process.exit(1);
}

/** Snapshots are one file per day; only the recent tail is worth carrying. */
const SNAPSHOT_DAYS = 30;

/** Decision files to open, newest first. */
const DECISION_DAYS = 14;

/** Decisions to keep per agent. The cap is on ROWS, not files: one
 *  file can hold thirty fills, and capping files alone produced a 1MB
 *  context — most of it rationale text nobody would ask about. */
const DECISIONS_PER_AGENT = 25;

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

function listJson(dir) {
  try {
    return readdirSync(dir)
      .filter((f) => f.endsWith(".json"))
      .sort();
  } catch {
    return [];
  }
}

const round2 = (n) => Math.round(n * 100) / 100;

/** An exit rule that denies the position exists.
 *
 *  The analyst layer was run on names the book already holds and wrote
 *  as though it held none — fixed upstream in core/llm/held_context.py,
 *  but every row on disk predates that. 30 of the 201 holdings that
 *  carry a rule carry one of these: peter_lynch/URBN holds 4 shares and
 *  its newest BUY row says "Not applicable until a position is taken".
 *
 *  Handing that to the assistant as the sell plan for a held name would
 *  have it tell the reader their position is not open. Withheld, and
 *  reported as no rule recorded, which is the honest answer.
 */
const DENIES_THE_POSITION =
  /not applicable|until a position|if bought|no position|do not initiate|stay on watch/i;

/** The rule the strategy recorded for a name, or null. */
function exitRuleFor(row, { held }) {
  const rule = row?.exit_trigger;
  if (!rule) return null;
  if (held && DENIES_THE_POSITION.test(rule)) return null;
  return rule;
}

const portfolios = [];
const portfolioDir = join(DATA_DIR, "portfolios");

for (const file of listJson(portfolioDir)) {
  const book = readJson(join(portfolioDir, file));
  if (!book) continue;
  const agent = book.agent ?? file.replace(/\.json$/, "");

  // The newest BUY row per ticker, over the WHOLE journal rather than
  // the recent tail below: a name bought in May and still held has no
  // row in the last fourteen days, and its plan is in that May row.
  // Only 88 of the 240 holdings have any decision row in the context.
  const allDecisionsDir = join(DATA_DIR, "decisions", agent);
  const newestBuy = new Map();
  for (const f of listJson(allDecisionsDir)) {
    const rows = readJson(join(allDecisionsDir, f));
    if (!Array.isArray(rows)) continue;
    for (const d of rows) {
      if (d?.decision !== "BUY" || !d.ticker) continue;
      const prev = newestBuy.get(d.ticker);
      if (!prev || (d.timestamp ?? "") > (prev.timestamp ?? "")) {
        newestBuy.set(d.ticker, d);
      }
    }
  }

  // Positions, trimmed to what a reader would see on the agent's card —
  // plus what the strategy recorded about ending the position, which
  // this map used to drop and the assistant was asked about.
  const positions = (book.positions ?? []).map((p) => {
    const row = newestBuy.get(p.ticker);
    const rule = exitRuleFor(row, { held: true });
    // The agent's own valuation of the share. NOT a price it is waiting
    // to pay — the action log called it that and named $67.40 on a
    // stock trading at $24.97.
    const value = row?.target_price ?? null;
    return {
      ticker: p.ticker,
      shares: p.shares,
      entry_price: p.entry_price,
      entry_date: p.entry_date,
      current_price: p.current_price,
      pnl_pct: p.pnl_pct,
      weight_pct: p.weight_pct,
      why: p.why_en,
      ...(rule ? { exit_rule: rule } : {}),
      ...(value != null ? { intrinsic_value_usd_per_share: value } : {}),
    };
  });

  const snapshotDir = join(DATA_DIR, "snapshots", agent);
  const snapshots = listJson(snapshotDir)
    .slice(-SNAPSHOT_DAYS)
    .map((f) => readJson(join(snapshotDir, f)))
    .filter(Boolean)
    .map((s) => ({ date: s.date, nav: s.nav, cash: s.cash }));

  // The ledger: every executed trade, with the realized P&L that used
  // to be computed at the sale and thrown away. Without it the assistant
  // was asked which stocks produced Graham's +24.55% and could only say
  // it did not know — which was true, and was the bug.
  const ledgerDir = join(DATA_DIR, "trades", agent);
  const ledger = listJson(ledgerDir).flatMap(
    (f) => readJson(join(ledgerDir, f)) ?? [],
  );

  const realizedByTicker = {};
  let reconstructedCount = 0;
  for (const row of ledger) {
    if (row.source === "reconstructed") reconstructedCount += 1;
    if (!row.realized_pnl_usd) continue;
    realizedByTicker[row.ticker] =
      (realizedByTicker[row.ticker] ?? 0) + row.realized_pnl_usd;
  }
  const contributors = Object.entries(realizedByTicker)
    .sort((a, b) => b[1] - a[1])
    .map(([ticker, realized_usd]) => ({
      ticker,
      realized_usd: round2(realized_usd),
    }));

  // The identity: NAV = initial + realized + unrealized + dividends - costs.
  // realized is solved for rather than summed from the ledger, so it stays
  // exact however much history the ledger is missing — and the gap between
  // it and what the ledger can name is reported, not hidden.
  const costBasis = (book.positions ?? []).reduce(
    (sum, p) => sum + p.shares * p.entry_price,
    0,
  );
  const unrealized = (book.invested ?? 0) - costBasis;
  const realized =
    (book.total_nav ?? 0) -
    (book.initial_cash ?? 0) -
    unrealized -
    (book.cumulative_dividends ?? 0) +
    (book.cumulative_costs ?? 0);
  const attributedTotal = contributors.reduce((n, c) => n + c.realized_usd, 0);

  const return_breakdown = {
    initial_cash: round2(book.initial_cash ?? 0),
    realized: round2(realized),
    unrealized: round2(unrealized),
    dividends: round2(book.cumulative_dividends ?? 0),
    costs: round2(book.cumulative_costs ?? 0),
    nav: round2(book.total_nav ?? 0),
    attributed_total: round2(attributedTotal),
    unattributed: round2(realized - attributedTotal),
    // Counts, not a flag. `reconstructedCount === ledger.length` meant
    // one caught-up row claimed the whole ledger was recorded at
    // execution, and 10 of the 13 books have one.
    reconstructed_rows: reconstructedCount,
    ledger_rows: ledger.length,
    note:
      "realized is derived from the accounting identity and is exact. " +
      "attributed is what the trade ledger can name. unattributed is " +
      "realized minus attributed, and its SIGN matters: positive means " +
      "closed result no stock can be named for, from trades that closed " +
      "before the ledger existed; negative means the named trades sum to " +
      "MORE than the book's own closed result, so a reconstructed amount " +
      "is overstated and the book's total is the one to rely on. " +
      "reconstructed_rows of the ledger_rows named trades were inferred " +
      "from committed books, not recorded at execution, so they are " +
      "approximate.",
  };

  const decisionDir = join(DATA_DIR, "decisions", agent);
  const decisions = listJson(decisionDir)
    .slice(-DECISION_DAYS)
    .flatMap((f) => readJson(join(decisionDir, f)) ?? [])
    .map((d) => ({
      ticker: d.ticker,
      decision: d.decision,
      date: (d.timestamp ?? "").slice(0, 10),
      entry_price: d.entry_price,
      rationale: d.rationale,
      // Thirteen fields on the row, five kept, and the two the reader
      // asks about were among the eight dropped. Not filtered here: a
      // decision row is a dated record of what was said that day, and
      // "not applicable until a position is taken" was true of a WATCH.
      ...(d.target_price != null
        ? { intrinsic_value_usd_per_share: d.target_price }
        : {}),
      ...(d.exit_trigger ? { exit_rule: d.exit_trigger } : {}),
    }))
    .slice(-DECISIONS_PER_AGENT);

  portfolios.push({
    agent,
    nav: book.total_nav,
    cash: book.cash,
    invested: book.invested,
    return_pct: book.cumulative_return_pct,
    initial_cash: book.initial_cash,
    dividend_floor: book.dividend_floor_date,
    return_breakdown,
    realized_contributors: contributors.slice(0, 15),
    closed_trades: ledger
      .filter((t) => t.side === "SELL")
      .slice(-40)
      .map((t) => ({
        date: t.date,
        ticker: t.ticker,
        shares: t.shares,
        price: t.price,
        realized_usd: t.realized_pnl_usd,
        source: t.source ?? "executed",
      })),
    last_open_run: book.last_open_run,
    position_count: positions.length,
    positions,
    watchlist: (book.watchlist ?? []).map((w) => ({
      ticker: w.ticker,
      why: w.why_en,
      // What would make it buy, and the price it waits for. Every live
      // watchlist entry carries the trigger and this map dropped it, so
      // the assistant could say a name was watched and not why or for
      // what. This IS an entry price, unlike the field above.
      ...(w.entry_trigger ? { entry_trigger: w.entry_trigger } : {}),
      ...(w.entry_price_target != null
        ? { entry_price_target: w.entry_price_target }
        : {}),
    })),
    snapshots,
    decisions,
  });
}

const context = {
  // Stamped by the build, so the assistant can say how fresh this is
  // rather than implying it is live.
  generated_at: new Date().toISOString(),
  agent_count: portfolios.length,
  portfolios,
};

writeFileSync(OUT, JSON.stringify(context));

const positions = portfolios.reduce((n, p) => n + p.position_count, 0);
console.log(
  `chat context: ${portfolios.length} agents, ${positions} positions, ` +
    `${(JSON.stringify(context).length / 1024).toFixed(0)}KB → ${OUT}`,
);

if (portfolios.length === 0) {
  console.error("error: no portfolios found — the assistant would know nothing");
  process.exit(1);
}
