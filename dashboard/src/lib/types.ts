// Shared TypeScript types for the Value Council dashboard.
// Mirrors the Python dataclasses in core/backtest/reporting.py and
// core/backtest/decision_logger.py.

export type AgentSlug =
  | "greenblatt_magic_formula"
  | "walter_schloss"
  | "benjamin_graham"
  | "david_dreman"
  | "john_neff"
  | "warren_buffett"
  | "peter_lynch"
  | "howard_marks"
  | "seth_klarman"
  | "philip_fisher"
  | "market_core";

export interface BacktestMetrics {
  cagr_pct: number;
  sharpe: number;
  sortino: number;
  max_drawdown_pct: number;
  max_drawdown_duration_days: number;
  calmar: number;
  hit_rate_monthly_pct: number;
  best_year: number | null;
  best_year_return_pct: number | null;
  worst_year: number | null;
  worst_year_return_pct: number | null;
  information_ratio_vs_benchmark: number | null;
  total_return_pct: number;
  n_observations: number;
  start_date: string;
  end_date: string;
  frequency: string;
  cost_model_name: string;
}

export interface BacktestSummary {
  run_id: string;
  strategy_name: string;
  config: {
    start_date: string;
    end_date: string;
    initial_cash: number;
    rebalance_freq: string;
    benchmark_ticker: string;
    cost_model: string;
  };
  n_trades: number;
  n_rebalances: number;
  total_costs_paid: number;
  strategy_metrics: BacktestMetrics;
  benchmark_metrics: BacktestMetrics;
}

export interface AnnualReturn {
  year: number;
  strategy_return_pct: number;
  benchmark_return_pct: number;
  alpha_pct: number;
}

export interface NavRow {
  date: string;
  nav: number;
  benchmark_nav: number;
}

/**
 * BUY/SELL/HOLD/WATCH are the strategy's intent — what the doctrine
 * decided. FILL/EXIT are the runner's execution — what the portfolio
 * actually did about it, and at what price. Both were previously
 * written as BUY, so every executed purchase appeared twice for the
 * same ticker on the same day.
 */
/** Mirrors `VALID_DECISION_TYPES` in core/backtest/decision_logger.py.
 *
 *  That list has nine members and this had six — REJECT, TRIM and ADD
 *  were absent, so a row carrying one of them was typed as something it
 *  is not and no exhaustive check could see it. */
export type DecisionKind =
  | "BUY"
  | "SELL"
  | "WATCH"
  | "REJECT"
  | "HOLD"
  | "TRIM"
  | "ADD"
  | "FILL"
  | "EXIT";

export interface DecisionRow {
  ticker: string;
  decision: DecisionKind;
  agent: string;
  timestamp: string;
  criteria_met: string[];
  criteria_failed: string[];
  criteria_values: Record<string, unknown>;
  market_conditions: Record<string, unknown>;
  confidence: number | null;
  entry_price: number | null;
  target_price: number | null;
  exit_trigger: string | null;
  rationale: string | null;
}

export interface AgentLatestRun {
  slug: AgentSlug;
  display_name: string;
  description: string;
  run_id: string;
  summary: BacktestSummary;
  annual_returns: AnnualReturn[];
  nav: NavRow[];
}

export interface CouncilOverview {
  agents: AgentLatestRun[];
  /** Equal-weighted average of strategy CAGRs across active agents. */
  council_cagr_pct: number;
  /** Benchmark CAGR (S&P) of the most recent run window. */
  benchmark_cagr_pct: number;
  /** Total alpha vs benchmark, equal-weighted. */
  council_alpha_pct: number;
}

// --------------------------------------------------------------------------
// Live paper-trading portfolio (mirrors core/live/portfolio.py JSON schema)
// --------------------------------------------------------------------------
export interface LivePosition {
  ticker: string;
  shares: number;
  entry_price: number;
  entry_date: string;
  current_price: number;
  pnl_usd: number;
  pnl_pct: number;
  weight_pct: number;
  why_en: string;
  why_he: string;
}

export interface LiveWatchEntry {
  ticker: string;
  identified_date: string;
  current_rank: number | null;
  entry_trigger: string;
  entry_price_target: number | null;
  why_en: string;
  why_he: string;
}

export interface LivePortfolio {
  agent: AgentSlug;
  cash: number;
  invested: number;
  total_nav: number;
  cumulative_return_pct: number;
  initial_cash: number;
  cumulative_costs: number;
  /** Cash dividends received to date. NAV already contains this money;
   *  the field exists so a total-return figure can be split into price
   *  appreciation and income instead of asserted as one number.
   *  Written by core/live/portfolio.py since the start and simply never
   *  declared here, so no page could show it. */
  cumulative_dividends: number;
  positions: LivePosition[];
  watchlist: LiveWatchEntry[];
  /** Most-recent run timestamp regardless of mode (open/close). */
  last_updated: string;
  /** Most-recent OPEN-mode run. Empty string until the first one. */
  last_open_run?: string;
  /** Most-recent CLOSE-mode mark-to-market. Empty until the first one. */
  last_close_run?: string;
}

export interface CouncilLive {
  portfolios: LivePortfolio[];
  council_nav: number;
  council_cash: number;
  council_invested: number;
  council_initial_cash: number;
  council_return_pct: number;
  council_pnl_usd: number;
}

// Daily snapshot — mirrors core/live/snapshots.py
export interface DailySnapshot {
  agent: AgentSlug;
  date: string; // ISO YYYY-MM-DD
  nav: number;
  cash: number;
  invested: number;
  pnl_usd: number;
  pnl_pct: number;
  position_count: number;
  watchlist_count: number;
  buys: string[];
  sells: string[];
  trade_count: number;
}

export interface AgentDailyDelta {
  /** Latest snapshot. */
  today: DailySnapshot;
  /** Previous snapshot (or null if none). */
  prev: DailySnapshot | null;
  /** Today's NAV minus prev NAV. */
  nav_change_usd: number;
  nav_change_pct: number;
}

/** Decision kinds that mean "the agent wants to own this".
 *
 *  The runner logs an executed purchase as FILL and a rank rotation out
 *  as EXIT, so a page that tests `decision === "BUY"` sees an intent
 *  and misses the execution. Those labels merged on 2026-08-07 and the
 *  live logs still hold only BUY/WATCH/SELL, so nothing is broken yet —
 *  it breaks on the next run, which is why this exists now.
 */
export const BUY_KINDS: readonly DecisionKind[] = ["BUY", "FILL", "ADD"];

/** Decision kinds that mean "the agent is out". */
export const SELL_KINDS: readonly DecisionKind[] = ["SELL", "EXIT", "TRIM"];

export function isBuy(d: DecisionKind): boolean {
  return (BUY_KINDS as DecisionKind[]).includes(d);
}

export function isSell(d: DecisionKind): boolean {
  return (SELL_KINDS as DecisionKind[]).includes(d);
}

/**
 * How stale a mark may be before the UI must stop calling it current.
 *
 * Mirrors MAX_CARRY_FORWARD_DAYS in core/backtest/data_loader.py, which
 * carries the evidence: 99.94% of inter-bar gaps are five days or under
 * — every routine exchange closure — and past that a missing bar is a
 * hole in the data, not a closed market.
 *
 * Lives here rather than in data.ts because the positions table is a
 * client component, and importing it from there drags node:fs into the
 * browser bundle.
 */
export const MAX_MARK_AGE_DAYS = 5;

/** When a position's mark was really struck, as opposed to when the run
 *  that used it happened. */
export interface MarkFreshness {
  /** Date of the bar the mark actually came from. */
  bar_date: string;
  /** Days between that bar and the run that used it. */
  days_stale: number;
}
