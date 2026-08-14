// Minimal i18n. Strings are stored as a flat dictionary; the toggle
// just swaps the active locale. Server components read the locale
// from the `council:locale` cookie (see `locale-server.ts`); the
// Providers component writes it client-side on every toggle.

export type Locale = "en" | "he";

type Dict = Record<string, string>;

const en: Dict = {
  // ---- App chrome ----
  app_title: "The Value Council",
  app_subtitle: "Ten value investors. One paper-money portfolio each.",
  footer:
    "© The Value Council — paper-trading simulation, not investment advice.",

  // ---- Nav ----
  nav_overview: "Overview",
  nav_heatmap: "Heatmap",
  nav_compare: "Compare",
  nav_agents: "Agents",
  nav_watchlist: "Watchlist",
  nav_journal: "Journal",
  nav_insights: "AI Insights",
  nav_history: "History",
  nav_backtest: "Backtests",
  nav_council: "Council",
  nav_glossary: "Glossary",

  // ---- Toggles ----
  toggle_language: "עברית",
  toggle_theme: "Toggle theme",

  // ---- Overview page ----
  overview_subtitle: "Live paper-trading state across all ten investors",
  council_nav: "Council NAV",
  council_cash: "Council cash",
  council_invested: "Invested",
  council_pnl: "P&L (USD)",
  benchmark_cagr: "S&P 500 CAGR",
  council_alpha: "Council α",
  active_agents: "Active agents",
  total_runs: "Total runs analysed",
  since_seed: "since seed",
  vs_sp: "vs S&P",
  // Live performance ranking (new section)
  ranking_title: "Live performance ranking",
  ranking_subtitle: "Sorted by total return since seed",
  col_rank: "Rank",
  col_medal: " ",
  // Live portfolios table
  live_portfolios: "Live portfolios",
  live_portfolios_caption: "$10,000 seed · 10 bps cost per trade",
  col_agent: "Agent",
  col_nav: "NAV",
  col_cash: "Cash",
  col_invested: "Invested",
  col_pnl_usd: "P&L $",
  col_pnl_pct: "P&L %",
  col_positions_short: "Pos.",
  col_positions: "Positions",
  col_watch: "Watch",
  last_updated: "Last updated",
  // Backtest leaderboard
  backtest_leaderboard: "Backtest leaderboard",
  ranked_by_alpha: "Ranked by alpha (Strategy CAGR − S&P CAGR)",
  col_window: "Window",
  col_cagr: "CAGR",
  col_sp: "S&P",
  col_alpha_short: "α",
  col_sharpe: "Sharpe",
  col_max_dd: "Max DD",
  col_trades: "Trades",

  // ---- Agents grid ----
  agents_subtitle: "Click a card to drill in",
  alpha_vs_sp: "α vs S&P",

  // ---- Agent drilldown ----
  total_pnl: "Total P&L",
  cash_available: "Cash available",
  pct_of_nav: "of NAV",
  positions_count: "{n} positions",
  seed_dollars: "seed ${amount}",
  ir_label: "IR",
  drilldown_live_positions: "Live positions",
  drilldown_updated_prefix: "Updated",
  drilldown_watchlist: "Watchlist",
  drilldown_no_watchlist: "No active watch entries.",
  col_ticker: "Ticker",
  col_rank_short: "Rank",
  col_trigger: "Trigger",
  col_why: "Why",
  drilldown_annual_returns: "Annual returns",
  col_year: "Year",
  col_strategy: "Strategy",
  col_sp500: "S&P 500",
  drilldown_recent_decisions: "Recent decisions",
  view_all: "View all →",
  no_decisions: "No decisions logged yet.",
  drilldown_no_data: "No backtest run or live portfolio for this agent yet.",

  // ---- Backtest caveat ----
  caveat_title: "One bias is left in these numbers, and it points up",
  caveat_body:
    "Re-run on 2026-08-07 through the corrected pipeline: all ten agents now cover the same window (2019-12-30 to 2024-12-31), a price is no longer carried forward across a gap of more than five days, and fundamentals resolve by recency rather than by the order concepts happen to appear in. The correction was not small — Greenblatt fell from a 112.13% CAGR to 14.60%, Klarman from a 1,365% total return to 106.58%, and every agent now lands between 9.7% and 21.6% against an S&P that returned 14.49%. What is still not fixed is survivorship: the universe roster comes from the SEC's list of currently registered issuers, so a company acquired or wound up before the roster was built is missing at every historical date — and the missing names are disproportionately the ones that failed. Read every figure here as an upper bound.",

  // ---- Sectors ----
  sector_title: "Where the money sits",
  sector_note: "Share of invested value by SIC division. Weighted by value, not by number of holdings.",
  sector_empty: "No holdings to break down yet.",
  sector_agriculture: "Agriculture",
  sector_mining: "Mining & energy",
  sector_construction: "Construction",
  sector_manufacturing: "Manufacturing",
  sector_transport_utilities: "Transport & utilities",
  sector_wholesale: "Wholesale",
  sector_retail: "Retail",
  sector_finance: "Finance & real estate",
  sector_services: "Services",
  sector_public_admin: "Public administration",
  sector_unknown: "Unclassified",

  // ---- Heatmap ----
  heatmap_title: "Annual Returns Heatmap",
  heatmap_subtitle: "Per-agent alpha vs S&P 500 each calendar year",
  avg_alpha: "Avg α",
  legend_color: "Color: alpha vs S&P 500 (green = outperform, red = underperform)",
  legend_under_50: "≤ −50%",
  legend_over_50: "≥ +50%",

  // ---- Compare ----
  compare_title: "Compare agents",
  compare_subtitle: "Pick agents to compare metrics side by side",
  compare_select_agents: "Select agents",
  compare_risk_return: "Risk-return profile",
  compare_metric_table: "Metric table",
  compare_axis_caption:
    "Each axis is normalized 0–100 against an empirical cap (CAGR 50% = 100, Sharpe 2.0 = 100). Drawdown axis is inverted so a larger blob = better.",
  col_total_return: "Total ret.",
  col_sortino: "Sortino",
  col_calmar: "Calmar",
  col_hit_rate: "Hit rate",

  // ---- Watchlist ----
  watchlist_title: "Watchlist",
  watchlist_subtitle:
    "Companies on at least one agent's radar — sorted by cross-agent agreement",
  col_watching: "Watching",
  col_agents: "Agents",
  col_last_update: "Last update",

  // ---- Journal ----
  journal_title: "Decision Journal",
  journal_subtitle: "Every BUY / WATCH the agents made, with full reasoning",

  // ---- Position stories (grouped journal) ----
  pos_title: "Positions",
  pos_subtitle: "One card per company — what the agent decided, and what came of it",
  pos_held: "Held",
  pos_closed: "Closed",
  pos_never: "Flagged, never bought",
  pos_days_affirmed: "days reaffirmed",
  pos_since: "since",
  pos_entry: "Entry",
  pos_now: "Now",
  pos_weight: "Weight",
  pos_show_timeline: "Show the full paper trail",
  pos_timeline_note: "One row per run. The agent re-states its verdict every day; a long list is conviction, not activity.",
  pos_evidence_position: "exact, from the open position",
  pos_evidence_trade: "exact, from a recorded trade",
  pos_evidence_flagged: "first flagged — the fill may have been later",
  pos_collapsed_from: "decision rows, grouped into",
  pos_cards: "companies",
  pos_longest: "Longest conviction",
  pos_trades_from: "Executed trades are recorded from {date}. Earlier days show no trades because the record was overwritten, not because nothing happened.",
  pos_no_criteria: "no criteria recorded",
  pos_watching: "Watching",
  pos_event_opened: "Opened the position",
  pos_event_reopened: "Bought back in",
  pos_event_exited: "Sold out",
  pos_event_changed: "Changed its view",
  pos_still_held: "Held since, unchanged, through {days} trading days.",
  price_chart_title: "Price since entry",
  price_chart_note: "Adjusted closes over the past year. The dashed line is what the agent paid; the dot is the entry date when it falls inside this window.",
  filter_agent: "Agent",
  filter_all: "All",
  filter_type: "Type",
  exit_label: "Exit:",
  conf_label: "conf",
  no_journal_match: "No decisions match the current filter.",
  decision_buy: "BUY",
  decision_sell: "SELL",
  decision_watch: "WATCH",
  decision_hold: "HOLD",

  // ---- Insights ----
  insights_title: "AI Insights",
  insights_subtitle: "Patterns surfaced from cross-agent decision logs",
  insights_decisions_logged: "Decisions logged",
  insights_unique_tickers: "Unique tickers seen",
  insights_council_alpha: "Council α (CAGR)",
  insights_consensus_title: "Consensus picks · current rebalance",
  insights_consensus_empty:
    "No tickers are picked by 2+ agents on the current rebalance. Each school is finding different opportunities — useful when evaluating whether the agents are diversifying signal.",
  insights_divergence_title: "Divergence · one agent buys, others wait",
  insights_divergence_empty: "No divergence on the current rebalance.",
  insights_per_school: "Per-school activity",
  insights_per_school_line: "{buys} BUYs · {watches} watches",
  passed_label: "passed:",

  // ---- Empty / error states ----
  no_data: "No data — run a backtest first.",
  no_backtest_data: "No backtest data yet.",
  no_live_data:
    "No backtest runs found. Run an agent or seed live portfolios first.",
  no_open_positions: "No open positions yet — agent is in cash.",

  // ---- Live positions table ----
  col_shares: "Shares",
  col_entry: "Entry",
  col_current: "Current",
  col_value: "Value",
  col_weight: "Wt",

  // ---- Today's activity ----
  todays_activity: "Today's activity",
  no_activity_today: "No trades today.",
  bought_today: "Bought today",
  sold_today: "Sold today",
  nav_change_today: "NAV change today",
  no_change: "no change",

  // ---- Position detail ----
  position_detail: "Position detail",
  why_bought: "Why the agent bought",
  entry_trigger: "Entry trigger",
  exit_trigger: "Exit trigger",
  entry_price_target: "Entry price target",
  distance_from_trigger: "Distance from trigger",
  position_metrics: "Position metrics",
  back_to_drilldown: "← Back to agent",
  no_position_found: "Position not found.",
  company_name: "Company",

  // ---- History tab ----
  nav_chart_title: "Performance — last 7 days",
  daily_trades_title: "Daily trades",
  nav_change_table: "Day-by-day NAV change",
  col_date: "Date",
  col_nav_change: "NAV change",
  col_buys: "Buys",
  col_sells: "Sells",
  col_trades_count: "Trades",
  no_history: "No snapshots yet.",
  history_subtitle: "Percent change over the window, so all ten sit on one scale. Dollar NAV is in the tables below.",

  // ---- Backtest tab ----
  backtest_tab_title: "Backtests",
  backtest_tab_subtitle: "Historical performance, year-by-year heatmap, and crisis stress tests",
  crisis_test_title: "Crisis stress tests",
  crisis_test_subtitle: "How each agent fared in major drawdowns",
  crisis_2008: "2008 — Global Financial Crisis",
  crisis_2020: "2020 — COVID Crash",
  crisis_2022: "2022 — Inflation Bear Market",
  crisis_no_data: "No data — backtest didn't include this year.",
  crisis_strategy_return: "Strategy return",
  crisis_benchmark_return: "S&P 500 return",
  crisis_alpha: "Alpha",

  // ---- 4-agent overview cards ----
  card_pnl_today: "P&L today",

  // ---- Glossary page ----
  glossary_title: "Glossary",
  glossary_subtitle: "Plain-language definitions of every metric on the dashboard",
  col_term: "Term",
  col_meaning: "Meaning",

  // ---- Schedule banner ----
  // ---- Sector trend (beside the donut) ----
  sector_trend_title: "How those sectors moved",
  sector_trend_note:
    "This agent's own holdings in each sector, weighted by today's position size and rebased to 100. Not its realised P&L — the book changed over the window; this rewinds what it holds now.",
  sector_trend_empty: "No price history for these holdings yet.",
  sector_trend_base: "100 = {d}",

  schedule_banner:
    "Scan starts 10:00 ET (17:00 IL), lands by 17:45 · Marks refresh 19:00, 21:00, 23:30 IL · Prices live during market hours",

  // ---- Timestamps & freshness ----
  last_sync: "Last sync",
  last_open_scan: "Last open scan",
  last_close_scan: "Last close",
  price_updated: "Price updated",
  of_which_dividends: "of which dividends",
  mark_undated: "Price date unavailable",
  mark_undated_help:
    "No price series has been published for this symbol yet, so the date of the bar behind this mark cannot be shown. It is not a claim that the price is out of date — only that its age cannot be checked here.",
  mark_stale: "Last traded",
  mark_stale_help:
    "This price comes from the last bar the market produced for this symbol, not from today's run. The symbol may have been renamed, delisted or suspended — check before acting on the quote.",
  days_ago: "{n} days ago",
  stale_warning:
    "⚠️ Data not refreshed in over 24 hours — check the daily runner.",
  next_us_update: "US",
  next_tase_update: "Israel",
  next_label: "next update",
  never: "never",
};

const he: Dict = {
  // ---- App chrome ----
  app_title: "המועצה הערכית",
  app_subtitle: "עשרה משקיעי ערך. תיק נייר אחד לכל אחד.",
  footer:
    "© המועצה הערכית — סימולציית מסחר נייר, לא ייעוץ השקעות.",

  // ---- Nav ----
  nav_overview: "סקירה",
  nav_heatmap: "מפת חום",
  nav_compare: "השוואה",
  nav_agents: "סוכנים",
  nav_watchlist: "רשימת מעקב",
  nav_journal: "יומן",
  nav_insights: "תובנות AI",
  nav_history: "היסטוריה",
  nav_backtest: "בקטסטים",
  nav_council: "המועצה",
  nav_glossary: "מילון",

  // ---- Toggles ----
  toggle_language: "English",
  toggle_theme: "החלף ערכת צבעים",

  // ---- Overview page ----
  overview_subtitle: "מצב מסחר הנייר החי בכל עשרת המשקיעים",
  council_nav: "שווי המועצה",
  council_cash: "מזומן המועצה",
  council_invested: "מושקע",
  council_pnl: "רווח/הפסד ($)",
  benchmark_cagr: "תשואה שנתית S&P 500",
  council_alpha: "α המועצה",
  active_agents: "סוכנים פעילים",
  total_runs: "סך ריצות שנותחו",
  since_seed: "מהשקעה ראשונית",
  vs_sp: "מול S&P",
  // Live performance ranking
  ranking_title: "דירוג ביצועים חי",
  ranking_subtitle: "ממוין לפי תשואה כוללת מהשקעה ראשונית",
  col_rank: "דירוג",
  col_medal: " ",
  // Live portfolios table
  live_portfolios: "תיקים חיים",
  live_portfolios_caption: "השקעה ראשונית $10,000 · עמלה 10 נקודות בסיס",
  col_agent: "סוכן",
  col_nav: "שווי",
  col_cash: "מזומן",
  col_invested: "מושקע",
  col_pnl_usd: "$ רווח/הפסד",
  col_pnl_pct: "% רווח/הפסד",
  col_positions_short: "פוז.",
  col_positions: "פוזיציות",
  col_watch: "מעקב",
  last_updated: "עודכן",
  // Backtest leaderboard
  backtest_leaderboard: "טבלת ביצועים היסטורית",
  ranked_by_alpha: "מדורג לפי אלפא (תשואה שנתית − תשואת S&P)",
  col_window: "תקופה",
  col_cagr: "תשואה שנתית",
  col_sp: "S&P",
  col_alpha_short: "α",
  col_sharpe: "שארפ",
  col_max_dd: "ירידה מקס׳",
  col_trades: "עסקאות",

  // ---- Agents grid ----
  agents_subtitle: "לחץ על כרטיס לצלילה פנימה",
  alpha_vs_sp: "α מול S&P",

  // ---- Agent drilldown ----
  total_pnl: "רווח/הפסד כולל",
  cash_available: "מזומן זמין",
  pct_of_nav: "מהשווי",
  positions_count: "{n} פוזיציות",
  seed_dollars: "השקעה ראשונית ${amount}",
  ir_label: "IR",
  drilldown_live_positions: "פוזיציות חיות",
  drilldown_updated_prefix: "עודכן",
  drilldown_watchlist: "רשימת מעקב",
  drilldown_no_watchlist: "אין מניות במעקב כרגע.",
  col_ticker: "מניה",
  col_rank_short: "דירוג",
  col_trigger: "טריגר",
  col_why: "סיבה",
  drilldown_annual_returns: "תשואות שנתיות",
  col_year: "שנה",
  col_strategy: "אסטרטגיה",
  col_sp500: "S&P 500",
  drilldown_recent_decisions: "החלטות אחרונות",
  view_all: "צפה בהכל ←",
  no_decisions: "אין החלטות מתועדות עדיין.",
  drilldown_no_data: "אין ריצת בקטסט או תיק חי לסוכן זה עדיין.",

  // ---- Backtest caveat ----
  caveat_title: "נותרה הטיה אחת במספרים האלה, והיא מנפחת כלפי מעלה",
  caveat_body:
    "הורצו מחדש ב-07/08/2026 דרך הצינור המתוקן: כל עשרת הסוכנים מכסים עכשיו את אותו חלון (30/12/2019 עד 31/12/2024), מחיר כבר לא נגרר קדימה על פני חור של יותר מחמישה ימים, ונתוני היסוד נפתרים לפי עדכניות ולא לפי הסדר שבו המושגים מופיעים. התיקון לא היה קטן — גרינבלט ירד מ-CAGR של 112.13% ל-14.60%, קלרמן מ-1,365% תשואה כוללת ל-106.58%, וכל הסוכנים נוחתים עכשיו בין 9.7% ל-21.6% מול S&P שהחזיר 14.49%. מה שעדיין לא תוקן הוא הישרדות: רשימת היקום מגיעה מרשימת המנפיקים הרשומים כיום של ה-SEC, כך שחברה שנרכשה או פורקה לפני בניית הרשימה חסרה בכל תאריך היסטורי — והשמות החסרים הם באופן לא־פרופורציונלי אלה שנכשלו. קרא כל מספר כאן כגבול עליון.",

  // ---- Sectors ----
  sector_title: "איפה הכסף יושב",
  sector_note: "חלק מהשווי המושקע לפי חטיבת SIC. משוקלל לפי שווי, לא לפי מספר החזקות.",
  sector_empty: "אין עדיין החזקות לפילוח.",
  sector_agriculture: "חקלאות",
  sector_mining: "כרייה ואנרגיה",
  sector_construction: "בנייה",
  sector_manufacturing: "תעשייה",
  sector_transport_utilities: "תחבורה ותשתיות",
  sector_wholesale: "סיטונאות",
  sector_retail: "קמעונאות",
  sector_finance: "פיננסים ונדל\"ן",
  sector_services: "שירותים",
  sector_public_admin: "מנהל ציבורי",
  sector_unknown: "לא מסווג",

  // ---- Heatmap ----
  heatmap_title: "מפת חום של תשואות שנתיות",
  heatmap_subtitle: "אלפא של כל סוכן מול S&P 500 בכל שנה קלנדרית",
  avg_alpha: "α ממוצע",
  legend_color: "צבע: אלפא מול S&P 500 (ירוק = ביצוע עודף, אדום = תת-ביצוע)",
  legend_under_50: "≤ −50%",
  legend_over_50: "≥ +50%",

  // ---- Compare ----
  compare_title: "השוואת סוכנים",
  compare_subtitle: "בחר סוכנים להשוואת מדדים זה לצד זה",
  compare_select_agents: "בחר סוכנים",
  compare_risk_return: "פרופיל סיכון–תשואה",
  compare_metric_table: "טבלת מדדים",
  compare_axis_caption:
    "כל ציר מנורמל 0–100 לפי תקרה אמפירית (CAGR 50% = 100, שארפ 2.0 = 100). ציר הירידה הפוך — בועה גדולה = טוב יותר.",
  col_total_return: "תשואה כוללת",
  col_sortino: "סורטינו",
  col_calmar: "קלמר",
  col_hit_rate: "אחוז פגיעה",

  // ---- Watchlist ----
  watchlist_title: "רשימת מעקב",
  watchlist_subtitle:
    "חברות במעקב של לפחות סוכן אחד — ממוין לפי הסכמה בין-סוכנים",
  col_watching: "במעקב של",
  col_agents: "סוכנים",
  col_last_update: "עדכון אחרון",

  // ---- Journal ----
  journal_title: "יומן החלטות",
  journal_subtitle: "כל קנייה / מעקב שהסוכנים ביצעו, עם הנמקה מלאה",

  // ---- Position stories (grouped journal) ----
  pos_title: "פוזיציות",
  pos_subtitle: "כרטיס אחד לכל חברה — מה הסוכן החליט, ומה יצא מזה",
  pos_held: "מוחזקת",
  pos_closed: "נסגרה",
  pos_never: "סומנה, לא נקנתה",
  pos_days_affirmed: "ימים שהתזה אושררה",
  pos_since: "מאז",
  pos_entry: "כניסה",
  pos_now: "עכשיו",
  pos_weight: "משקל",
  pos_show_timeline: "הצג את כל התיעוד",
  pos_timeline_note: "שורה לכל ריצה. הסוכן חוזר על הקביעה שלו כל יום; רשימה ארוכה היא שכנוע, לא פעילות.",
  pos_evidence_position: "מדויק, מהפוזיציה הפתוחה",
  pos_evidence_trade: "מדויק, מעסקה שנרשמה",
  pos_evidence_flagged: "תאריך הסימון הראשון — הקנייה בפועל אולי הייתה מאוחר יותר",
  pos_collapsed_from: "שורות החלטה, מקובצות ל־",
  pos_cards: "חברות",
  pos_longest: "השכנוע הארוך ביותר",
  pos_trades_from: "עסקאות שבוצעו נרשמות החל מ־{date}. בימים קודמים לא מוצגות עסקאות כי הרישום נדרס, לא כי לא קרה כלום.",
  pos_no_criteria: "לא נרשמו קריטריונים",
  pos_watching: "במעקב",
  pos_event_opened: "נפתחה פוזיציה",
  pos_event_reopened: "נקנתה מחדש",
  pos_event_exited: "נמכרה",
  pos_event_changed: "שינה את דעתו",
  pos_still_held: "מוחזקת מאז ללא שינוי, לאורך {days} ימי מסחר.",
  price_chart_title: "מחיר מאז הכניסה",
  price_chart_note: "מחירי סגירה מתואמים בשנה האחרונה. הקו המקווקו הוא מה שהסוכן שילם; הנקודה היא תאריך הכניסה, כשהוא נופל בתוך החלון הזה.",
  filter_agent: "סוכן",
  filter_all: "הכל",
  filter_type: "סוג",
  exit_label: "יציאה:",
  conf_label: "ביטחון",
  no_journal_match: "אין החלטות התואמות את הסינון הנוכחי.",
  decision_buy: "קנה",
  decision_sell: "מכור",
  decision_watch: "מעקב",
  decision_hold: "החזק",

  // ---- Insights ----
  insights_title: "תובנות AI",
  insights_subtitle: "דפוסים שעלו מיומני ההחלטות הצולבים",
  insights_decisions_logged: "החלטות מתועדות",
  insights_unique_tickers: "מניות ייחודיות שזוהו",
  insights_council_alpha: "α המועצה (CAGR)",
  insights_consensus_title: "בחירות בקונצנזוס · איזון נוכחי",
  insights_consensus_empty:
    "אין מניות שנבחרו על ידי 2+ סוכנים באיזון הנוכחי. כל אסכולה מוצאת הזדמנויות שונות — מועיל להערכה האם הסוכנים מגוונים אות.",
  insights_divergence_title: "מחלוקת · סוכן אחד קונה, האחרים ממתינים",
  insights_divergence_empty: "אין מחלוקת באיזון הנוכחי.",
  insights_per_school: "פעילות לפי אסכולה",
  insights_per_school_line: "{buys} קניות · {watches} במעקב",
  passed_label: "ויתרו:",

  // ---- Empty / error states ----
  no_data: "אין נתונים — הרץ בקטסט תחילה.",
  no_backtest_data: "אין נתוני בקטסט עדיין.",
  no_live_data:
    "לא נמצאו ריצות בקטסט. הרץ סוכן או זרע תיקים חיים תחילה.",
  no_open_positions: "אין פוזיציות פתוחות עדיין — הסוכן במזומן.",

  // ---- Live positions table ----
  col_shares: "מניות",
  col_entry: "כניסה",
  col_current: "נוכחי",
  col_value: "ערך",
  col_weight: "משקל",

  // ---- Today's activity ----
  todays_activity: "פעילות היום",
  no_activity_today: "אין עסקאות היום.",
  bought_today: "נקנו היום",
  sold_today: "נמכרו היום",
  nav_change_today: "שינוי שווי היום",
  no_change: "ללא שינוי",

  // ---- Position detail ----
  position_detail: "פירוט פוזיציה",
  why_bought: "מדוע הסוכן קנה",
  entry_trigger: "טריגר כניסה",
  exit_trigger: "טריגר יציאה",
  entry_price_target: "מחיר כניסה מטרה",
  distance_from_trigger: "מרחק מטריגר",
  position_metrics: "מדדי פוזיציה",
  back_to_drilldown: "← חזרה לסוכן",
  no_position_found: "פוזיציה לא נמצאה.",
  company_name: "חברה",

  // ---- History tab ----
  nav_chart_title: "ביצועים — 7 הימים האחרונים",
  daily_trades_title: "עסקאות יומיות",
  nav_change_table: "שינוי שווי יום-יום",
  col_date: "תאריך",
  col_nav_change: "שינוי שווי",
  col_buys: "קניות",
  col_sells: "מכירות",
  col_trades_count: "עסקאות",
  no_history: "אין צילומי-מצב עדיין.",
  history_subtitle: "שינוי באחוזים על פני החלון, כך שכל העשרה על אותו סולם. השווי בדולרים בטבלאות למטה.",

  // ---- Backtest tab ----
  backtest_tab_title: "בקטסטים",
  backtest_tab_subtitle: "ביצועים היסטוריים, מפת חום שנתית ומבחני לחץ במשברים",
  crisis_test_title: "מבחני לחץ במשברים",
  crisis_test_subtitle: "כיצד כל סוכן עמד בירידות הגדולות",
  crisis_2008: "2008 — משבר פיננסי גלובלי",
  crisis_2020: "2020 — קריסת הקורונה",
  crisis_2022: "2022 — שוק דובי אינפלציוני",
  crisis_no_data: "אין נתונים — הבקטסט לא כיסה את השנה.",
  crisis_strategy_return: "תשואת אסטרטגיה",
  crisis_benchmark_return: "תשואת S&P 500",
  crisis_alpha: "אלפא",

  // ---- 4-agent overview cards ----
  card_pnl_today: "רווח/הפסד היום",

  // ---- Glossary page ----
  glossary_title: "מילון מונחים",
  glossary_subtitle: "הגדרות בשפה ברורה לכל מדד בלוח",
  col_term: "מונח",
  col_meaning: "משמעות",

  // ---- Schedule banner ----
  // ---- Sector trend (beside the donut) ----
  sector_trend_title: "איך הסקטורים האלה נעו",
  sector_trend_note:
    "ההחזקות של הסוכן עצמו בכל סקטור, משוקללות לפי גודל הפוזיציה היום ומנורמלות ל-100. זה לא הרווח שהוא בפועל עשה — התיק השתנה לאורך התקופה, וזה מריץ אחורה את מה שהוא מחזיק עכשיו.",
  sector_trend_empty: "עדיין אין היסטוריית מחירים להחזקות האלה.",
  sector_trend_base: "100 = {d}",

  schedule_banner:
    "הסריקה מתחילה 17:00 ונכנסת עד 17:45 · עדכון מחירים 19:00, 21:00, 23:30 (שעון ישראל) · מחירים בזמן אמת בשעות המסחר",

  // ---- Timestamps & freshness ----
  last_sync: "עודכן לאחרונה",
  last_open_scan: "סריקה אחרונה",
  last_close_scan: "סגירה",
  price_updated: "מחיר מעודכן",
  of_which_dividends: "מזה דיבידנדים",
  mark_undated: "תאריך המחיר לא זמין",
  mark_undated_help:
    "עדיין לא פורסמה סדרת מחירים לסימבול הזה, ולכן אי אפשר להציג את תאריך הנר שממנו נלקח הסימון. זו לא טענה שהמחיר לא מעודכן — רק שאי אפשר לבדוק כאן את גילו.",
  mark_stale: "נסחר לאחרונה",
  mark_stale_help:
    "המחיר הזה מגיע מהנר האחרון שהשוק ייצר לסימבול הזה, לא מהריצה של היום. ייתכן שהסימבול שונה, נמחק מהמסחר או הושעה — בדוק לפני שאתה פועל לפי הציטוט.",
  days_ago: "לפני {n} ימים",
  stale_warning:
    "⚠️ נתונים לא מעודכנים מעל 24 שעות — בדוק את הסריקה היומית.",
  next_us_update: "US",
  next_tase_update: "ישראל",
  next_label: "עדכון הבא",
  never: "לא היה",
};

export const dictionaries: Record<Locale, Dict> = { en, he };

export function t(locale: Locale, key: string): string {
  return dictionaries[locale][key] ?? key;
}

/** Substitute {placeholders} in a translated string. */
export function tFmt(
  locale: Locale,
  key: string,
  vars: Record<string, string | number>,
): string {
  let s = t(locale, key);
  for (const [k, v] of Object.entries(vars)) {
    s = s.replace(`{${k}}`, String(v));
  }
  return s;
}
