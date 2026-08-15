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

const portfolios = [];
const portfolioDir = join(DATA_DIR, "portfolios");

for (const file of listJson(portfolioDir)) {
  const book = readJson(join(portfolioDir, file));
  if (!book) continue;
  const agent = book.agent ?? file.replace(/\.json$/, "");

  // Positions, trimmed to what a reader would see on the agent's card.
  const positions = (book.positions ?? []).map((p) => ({
    ticker: p.ticker,
    shares: p.shares,
    entry_price: p.entry_price,
    entry_date: p.entry_date,
    current_price: p.current_price,
    pnl_pct: p.pnl_pct,
    weight_pct: p.weight_pct,
    why: p.why_en,
  }));

  const snapshotDir = join(DATA_DIR, "snapshots", agent);
  const snapshots = listJson(snapshotDir)
    .slice(-SNAPSHOT_DAYS)
    .map((f) => readJson(join(snapshotDir, f)))
    .filter(Boolean)
    .map((s) => ({ date: s.date, nav: s.nav, cash: s.cash }));

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
    }))
    .slice(-DECISIONS_PER_AGENT);

  portfolios.push({
    agent,
    nav: book.total_nav,
    cash: book.cash,
    invested: book.invested,
    return_pct: book.cumulative_return_pct,
    initial_cash: book.initial_cash,
    inception: book.inception_date,
    last_open_run: book.last_open_run,
    position_count: positions.length,
    positions,
    watchlist: (book.watchlist ?? []).map((w) => ({
      ticker: w.ticker,
      why: w.why_en,
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
