// Data loader — reads real backtest results and decision logs from
// the project's data/ tree. No mock data anywhere.
//
// All functions are server-only (use Node fs). Components calling them
// must be Server Components or use them inside getStaticProps-style
// loaders. The dashboard is a Next.js 14 app-router app, so most pages
// are server components by default.

import { promises as fs } from "node:fs";
import path from "node:path";
import { parse as parseCsvSync } from "csv-parse/sync";

import { AGENTS, metaFor } from "./agents";
import type {
  AgentDailyDelta,
  AgentLatestRun,
  AgentSlug,
  AnnualReturn,
  BacktestSummary,
  CouncilLive,
  CouncilOverview,
  DailySnapshot,
  DecisionKind,
  DecisionRow,
  LivePortfolio,
  NavRow,
} from "./types";

// Resolve the data root via env var, falling back to ~/Library so the
// dashboard never has to read from ~/Documents (which is gated by
// macOS TCC for non-interactive processes like `node`).
//
// Resolution order:
//   1. VALUE_COUNCIL_DATA_DIR env var (explicit override).
//   2. ~/Library/Application Support/value-council/ (default — TCC-safe).
//   3. <project_root>/data/ (legacy fallback for dev / CI).
function resolveDataRoot(): string {
  if (process.env.VALUE_COUNCIL_DATA_DIR) {
    return process.env.VALUE_COUNCIL_DATA_DIR;
  }
  const home = process.env.HOME;
  if (home) {
    const lib = path.join(home, "Library", "Application Support", "value-council");
    return lib;
  }
  return path.resolve(process.cwd(), "..", "data");
}

const DATA_ROOT = resolveDataRoot();
const BACKTEST_DIR = path.join(DATA_ROOT, "backtest_results");
const DECISIONS_DIR = path.join(DATA_ROOT, "decisions");
const PORTFOLIOS_DIR = path.join(DATA_ROOT, "portfolios");
const SNAPSHOTS_DIR = path.join(DATA_ROOT, "snapshots");
const COMPANY_NAMES_PATH = path.join(DATA_ROOT, "cache", "company_names.json");

// --------------------------------------------------------------------------
// Backtest run discovery
// --------------------------------------------------------------------------

/** List subdirectories in data/backtest_results/ — each is one run. */
async function listRunDirs(): Promise<string[]> {
  try {
    const entries = await fs.readdir(BACKTEST_DIR, { withFileTypes: true });
    return entries.filter((e) => e.isDirectory()).map((e) => e.name);
  } catch {
    return [];
  }
}

/** Filename format: <strategy>_<YYYYMMDDTHHMMSS>_<hash>. Newest wins. */
function pickLatestForAgent(slug: AgentSlug, all: string[]): string | null {
  const matches = all.filter((d) => d.startsWith(`${slug}_`));
  if (matches.length === 0) return null;
  return matches.sort().at(-1) ?? null;
}

// --------------------------------------------------------------------------
// File loaders
// --------------------------------------------------------------------------

async function readJson<T>(file: string): Promise<T> {
  const txt = await fs.readFile(file, "utf-8");
  return JSON.parse(txt) as T;
}

async function readAnnualCsv(file: string): Promise<AnnualReturn[]> {
  const txt = await fs.readFile(file, "utf-8");
  const rows = parseCsvSync(txt, { columns: true, skip_empty_lines: true });
  return rows.map((r: Record<string, string>) => ({
    year: Number(r.year),
    strategy_return_pct: Number(r.strategy_return_pct),
    benchmark_return_pct: Number(r.benchmark_return_pct),
    alpha_pct: Number(r.alpha_pct),
  }));
}

async function readNavCsv(file: string): Promise<NavRow[]> {
  const txt = await fs.readFile(file, "utf-8");
  const rows = parseCsvSync(txt, { columns: true, skip_empty_lines: true });
  return rows.map((r: Record<string, string>) => ({
    date: r.date,
    nav: Number(r.nav),
    benchmark_nav: Number(r.benchmark_nav),
  }));
}

// --------------------------------------------------------------------------
// Public API — composable functions used by pages
// --------------------------------------------------------------------------

/** Load the latest run for a given agent. Returns null if no run on disk. */
export async function loadAgentLatest(slug: AgentSlug): Promise<AgentLatestRun | null> {
  const runs = await listRunDirs();
  const latest = pickLatestForAgent(slug, runs);
  if (!latest) return null;

  const meta = metaFor(slug);
  if (!meta) return null;

  const runDir = path.join(BACKTEST_DIR, latest);
  const [summary, annual_returns, nav] = await Promise.all([
    readJson<BacktestSummary>(path.join(runDir, "summary.json")),
    readAnnualCsv(path.join(runDir, "annual_returns.csv")),
    readNavCsv(path.join(runDir, "nav.csv")),
  ]);

  return {
    slug,
    display_name: meta.display_name,
    description: meta.description,
    run_id: latest,
    summary,
    annual_returns,
    nav,
  };
}

/** Load the latest run for all configured agents that have one on disk. */
export async function loadCouncilOverview(): Promise<CouncilOverview> {
  const results = await Promise.all(
    AGENTS.map((a) => loadAgentLatest(a.slug)),
  );
  const agents = results.filter((r): r is AgentLatestRun => r !== null);

  if (agents.length === 0) {
    return {
      agents: [],
      council_cagr_pct: 0,
      benchmark_cagr_pct: 0,
      council_alpha_pct: 0,
    };
  }

  const councilCagr =
    agents.reduce((s, a) => s + a.summary.strategy_metrics.cagr_pct, 0) /
    agents.length;
  // Use the first run's benchmark CAGR as the headline number — windows
  // differ slightly across runs but SPY's CAGR over comparable windows
  // is similar. (The compare page lets the user inspect each run individually.)
  const benchmarkCagr = agents[0].summary.benchmark_metrics.cagr_pct;
  const councilAlpha = councilCagr - benchmarkCagr;

  return {
    agents,
    council_cagr_pct: councilCagr,
    benchmark_cagr_pct: benchmarkCagr,
    council_alpha_pct: councilAlpha,
  };
}

// --------------------------------------------------------------------------
// Decision journal
// --------------------------------------------------------------------------

export interface JournalQuery {
  /** Restrict to a single agent. Omit to scan all agents. */
  agent?: AgentSlug;
  /** Restrict to specific decision types. */
  decisions?: DecisionKind[];
  /** Maximum rows to return — default 500 for sane page sizes. */
  limit?: number;
}

/** Read all decision logs for an agent. */
async function loadDecisionsForAgent(
  slug: AgentSlug,
): Promise<DecisionRow[]> {
  const dir = path.join(DECISIONS_DIR, slug);
  let files: string[];
  try {
    files = await fs.readdir(dir);
  } catch {
    return [];
  }
  const decisions: DecisionRow[] = [];
  for (const f of files.filter((x) => x.endsWith(".json")).sort()) {
    try {
      const rows = await readJson<DecisionRow[]>(path.join(dir, f));
      decisions.push(...rows);
    } catch {
      // Tolerate corrupt files — they should never appear in production
      // but a partial write during a backtest run shouldn't break the UI.
    }
  }
  return decisions;
}

/** Cross-agent decision journal, newest first. */
export async function loadJournal(query: JournalQuery = {}): Promise<DecisionRow[]> {
  const slugs: AgentSlug[] = query.agent
    ? [query.agent]
    : AGENTS.map((a) => a.slug);
  const all = (
    await Promise.all(slugs.map((s) => loadDecisionsForAgent(s)))
  ).flat();

  let filtered = all;
  if (query.decisions && query.decisions.length > 0) {
    const set = new Set(query.decisions);
    filtered = filtered.filter((d) => set.has(d.decision));
  }
  filtered.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
  if (query.limit) filtered = filtered.slice(0, query.limit);
  return filtered;
}

// --------------------------------------------------------------------------
// Watchlist — derived from latest WATCH decisions across agents
// --------------------------------------------------------------------------

export interface WatchEntry {
  ticker: string;
  agents: { slug: AgentSlug; display_name: string; rationale: string | null }[];
  most_recent: string;
  /** A WATCH usually has the same criteria as a BUY but didn't make the
   *  top-N. Track how many agents are watching the name to surface
   *  cross-agent agreement. */
  agent_count: number;
}

export async function loadWatchlist(): Promise<WatchEntry[]> {
  const slugs = AGENTS.map((a) => a.slug);
  const watches = (
    await Promise.all(
      slugs.map(async (s) =>
        (await loadDecisionsForAgent(s)).filter((d) => d.decision === "WATCH"),
      ),
    )
  ).flat();

  // Keep only most-recent WATCH per (ticker, agent) pair.
  const latest = new Map<string, DecisionRow>();
  for (const w of watches) {
    const key = `${w.ticker}|${w.agent}`;
    const cur = latest.get(key);
    if (!cur || w.timestamp > cur.timestamp) latest.set(key, w);
  }

  // Group by ticker.
  const byTicker = new Map<string, DecisionRow[]>();
  for (const w of latest.values()) {
    const list = byTicker.get(w.ticker) ?? [];
    list.push(w);
    byTicker.set(w.ticker, list);
  }

  const entries: WatchEntry[] = [];
  for (const [ticker, list] of byTicker.entries()) {
    list.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
    entries.push({
      ticker,
      agents: list.map((d) => ({
        slug: d.agent as AgentSlug,
        display_name: metaFor(d.agent as AgentSlug)?.display_name ?? d.agent,
        rationale: d.rationale,
      })),
      most_recent: list[0].timestamp,
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

// --------------------------------------------------------------------------
// Live paper-trading portfolios
// --------------------------------------------------------------------------

/** Load one agent's persisted live portfolio. Returns null if missing. */
export async function loadLivePortfolio(slug: AgentSlug): Promise<LivePortfolio | null> {
  const file = path.join(PORTFOLIOS_DIR, `${slug}.json`);
  try {
    return await readJson<LivePortfolio>(file);
  } catch {
    return null;
  }
}

// ----- Daily snapshots --------------------------------------------------

export async function loadSnapshots(slug: AgentSlug): Promise<DailySnapshot[]> {
  const dir = path.join(SNAPSHOTS_DIR, slug);
  let files: string[];
  try {
    files = await fs.readdir(dir);
  } catch {
    return [];
  }
  const out: DailySnapshot[] = [];
  for (const f of files.filter((x) => x.endsWith(".json")).sort()) {
    try {
      out.push(await readJson<DailySnapshot>(path.join(dir, f)));
    } catch {
      /* tolerate corrupt files */
    }
  }
  return out;
}

/** Latest snapshot + previous = today's-activity delta. */
export async function loadAgentDelta(slug: AgentSlug): Promise<AgentDailyDelta | null> {
  const snaps = await loadSnapshots(slug);
  if (snaps.length === 0) return null;
  const today = snaps[snaps.length - 1];
  const prev = snaps.length >= 2 ? snaps[snaps.length - 2] : null;
  const nav_change_usd = prev ? today.nav - prev.nav : 0;
  const nav_change_pct = prev && prev.nav > 0 ? (nav_change_usd / prev.nav) * 100 : 0;
  return { today, prev, nav_change_usd, nav_change_pct };
}

/** Load only the most recent N snapshots for an agent. */
export async function loadRecentSnapshots(
  slug: AgentSlug,
  n: number,
): Promise<DailySnapshot[]> {
  const snaps = await loadSnapshots(slug);
  return snaps.slice(-n);
}

// ----- Company-name resolver -------------------------------------------

let _companyNamesCache: Record<string, string> | null = null;

export async function loadCompanyNames(): Promise<Record<string, string>> {
  if (_companyNamesCache) return _companyNamesCache;
  try {
    _companyNamesCache = await readJson<Record<string, string>>(COMPANY_NAMES_PATH);
  } catch {
    _companyNamesCache = {};
  }
  return _companyNamesCache;
}

/** Load all four agents' live portfolios + compute council aggregate. */
export async function loadCouncilLive(): Promise<CouncilLive> {
  const all = await Promise.all(AGENTS.map((a) => loadLivePortfolio(a.slug)));
  const portfolios = all.filter((p): p is LivePortfolio => p !== null);
  const council_nav = portfolios.reduce((s, p) => s + p.total_nav, 0);
  const council_cash = portfolios.reduce((s, p) => s + p.cash, 0);
  const council_invested = portfolios.reduce((s, p) => s + p.invested, 0);
  const council_initial_cash = portfolios.reduce((s, p) => s + p.initial_cash, 0);
  const council_pnl_usd = council_nav - council_initial_cash;
  const council_return_pct =
    council_initial_cash > 0 ? (council_pnl_usd / council_initial_cash) * 100 : 0;
  return {
    portfolios,
    council_nav,
    council_cash,
    council_invested,
    council_initial_cash,
    council_return_pct,
    council_pnl_usd,
  };
}
