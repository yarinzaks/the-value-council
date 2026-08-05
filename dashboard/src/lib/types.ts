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
  | "philip_fisher";

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
export type DecisionKind =
  | "BUY"
  | "SELL"
  | "HOLD"
  | "WATCH"
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
