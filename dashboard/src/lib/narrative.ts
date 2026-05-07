// Plain-Hebrew narrative explanations for the journal.
//
// The Python decision logger emits short technical strings like
// "Magic Formula rank 16: EY=0.302, ROC=6.098". For the dashboard
// journal we want a human-readable sentence the user can read aloud
// without needing to know what EY or ROC mean.
//
// Each narrative builder takes a DecisionRow and returns a single
// short paragraph (≤ 3 sentences, ≤ ~250 chars) explaining what the
// agent saw and why it acted.

import type { DecisionRow } from "./types";
import type { Locale } from "./i18n";

interface NarrativeOpts {
  /** Optional human-readable company name to inject ("Apple Inc."). */
  companyName?: string;
  /** How many tickers were in the universe — used to anchor "ranked
   *  X out of N". Optional. */
  universe_size?: number;
}

function pct(v: unknown, digits = 0): string {
  if (typeof v !== "number" || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function ratio(v: unknown, digits = 2): string {
  if (typeof v !== "number" || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

// ---------------------------------------------------------------------------
// Hebrew narratives — one per agent / decision-type combination.
// ---------------------------------------------------------------------------
function greenblattBuyHe(d: DecisionRow, opts: NarrativeOpts): string {
  const v = d.criteria_values ?? {};
  const ey = pct(v.earnings_yield);
  const roc = pct(v.return_on_capital);
  const rank = v.combined_rank ?? "?";
  const universe = opts.universe_size ?? 3860;
  const subject = opts.companyName ? `את ${d.ticker} (${opts.companyName})` : `את ${d.ticker}`;
  return [
    `גרינבלאט זיהה ${subject} כהזדמנות.`,
    `הרווח של החברה גבוה ביחס למחיר (${ey} תשואת רווח), והיא מייצרת תשואה גבוהה על הכסף שמושקע בה (${roc}).`,
    `הסוכן קנה כי היא דורגה במקום ${rank} מתוך ${universe.toLocaleString("en-US")} חברות.`,
  ].join(" ");
}

function schlossBuyHe(d: DecisionRow, opts: NarrativeOpts): string {
  const v = d.criteria_values ?? {};
  const pb = ratio(v.pb_ratio);
  const de = ratio(v.debt_to_equity);
  const subject = opts.companyName ? `את ${d.ticker} (${opts.companyName})` : `את ${d.ticker}`;
  return [
    `שלוס מצא ${subject} נסחרת מתחת להון העצמי שלה (P/B = ${pb}).`,
    `החברה לא ממונפת יתר על המידה (D/E = ${de}) ויש לה רווחים — סימן שהשוק פשוט ויתר עליה.`,
    `כשמחיר חוזר לערך הספרי, יש פוטנציאל לרווח.`,
  ].join(" ");
}

function grahamNetNetBuyHe(d: DecisionRow, opts: NarrativeOpts): string {
  const v = d.criteria_values ?? {};
  const pNcav = ratio(v.p_ncav, 3);
  const subject = opts.companyName ? `את ${d.ticker} (${opts.companyName})` : `את ${d.ticker}`;
  return [
    `גראהם זיהה Net-Net קלאסי: ${subject} נסחרת ב-${pNcav} בלבד מהנכסים השוטפים נטו שלה.`,
    `כלומר אפשר לקנות את המזומן ומלאי החברה בהנחה — והעסק התפעולי שלה ניתן בחינם.`,
    `זו המציאה העמוקה ביותר שגראהם חיפש.`,
  ].join(" ");
}

function grahamDefensiveBuyHe(d: DecisionRow, opts: NarrativeOpts): string {
  const v = d.criteria_values ?? {};
  const pe = ratio(v.pe);
  const pb = ratio(v.pb);
  const cr = ratio(v.current_ratio);
  const subject = opts.companyName ? `את ${d.ticker} (${opts.companyName})` : `את ${d.ticker}`;
  return [
    `גראהם זיהה ${subject} כעומדת בכל קריטריוני ה"משקיע ההגנתי".`,
    `מחיר סביר (P/E = ${pe}, P/B = ${pb}), נזילות חזקה (יחס שוטף = ${cr}), ורווח עקבי.`,
    `מניה שאפשר להחזיק בלי לאבד שינה.`,
  ].join(" ");
}

function dremanBuyHe(d: DecisionRow, opts: NarrativeOpts): string {
  const v = d.criteria_values ?? {};
  const qual = v.qualifying_metrics ?? "?";
  const subject = opts.companyName ? `את ${d.ticker} (${opts.companyName})` : `את ${d.ticker}`;
  return [
    `דרמן זיהה ${subject} כמועמדת ניגודית: השוק שונא אותה — ובדיוק לכן היא מעניינת.`,
    `החברה זולה ב-${qual} מתוך 4 מדדים מרכזיים: P/E, P/CF, P/B, או תשואת דיבידנד.`,
    `ההיסטוריה מראה שמניות בקבוצה זו נוטות להפתיע לטובה כשהשוק מתקן את עצמו.`,
  ].join(" ");
}

function genericSellHe(d: DecisionRow, opts: NarrativeOpts): string {
  const subject = opts.companyName ? `${d.ticker} (${opts.companyName})` : d.ticker;
  // The Python rationale is "Closed live position: entry $X.XX, exit $Y.YY (Z.ZZ%)"
  const m = (d.rationale ?? "").match(/entry \$([\d.]+), exit \$([\d.]+) \(([+\-−][\d.]+)%\)/);
  if (m) {
    const entry = m[1], exit = m[2], pct_ = m[3];
    return `הסוכן סגר את ${subject}: כניסה ב-‎$${entry}‎, יציאה ב-‎$${exit}‎ (${pct_}%). היא יצאה מתוך החברות שעומדות בקריטריונים — לכן הסוכן עבר הלאה.`;
  }
  return `הסוכן סגר את ${subject} כי היא יצאה מקבוצת המועמדים האקטיבית של האסטרטגיה.`;
}

function genericWatchHe(d: DecisionRow, opts: NarrativeOpts): string {
  const subject = opts.companyName ? `${d.ticker} (${opts.companyName})` : d.ticker;
  return `הסוכן עוקב אחר ${subject} — הקריטריונים קרובים אך טרם נחצו במלואם. אם המחיר ירד או היסודות ישתפרו, היא עשויה להיכנס לתיק.`;
}

// ---------------------------------------------------------------------------
// English fallback — re-frames the same idea in plain English.
// ---------------------------------------------------------------------------
function greenblattBuyEn(d: DecisionRow, opts: NarrativeOpts): string {
  const v = d.criteria_values ?? {};
  const ey = pct(v.earnings_yield);
  const roc = pct(v.return_on_capital);
  const rank = v.combined_rank ?? "?";
  const universe = opts.universe_size ?? 3860;
  const subject = opts.companyName ? `${d.ticker} (${opts.companyName})` : d.ticker;
  return `Greenblatt picked ${subject}: cheap relative to earnings (${ey} earnings yield) AND highly profitable on the capital it employs (${roc}). It ranked #${rank} out of ${universe.toLocaleString("en-US")} companies.`;
}

function schlossBuyEn(d: DecisionRow, opts: NarrativeOpts): string {
  const v = d.criteria_values ?? {};
  const subject = opts.companyName ? `${d.ticker} (${opts.companyName})` : d.ticker;
  return `Schloss picked ${subject}: trading below tangible book (P/B = ${ratio(v.pb_ratio)}), low debt (D/E = ${ratio(v.debt_to_equity)}), and profitable. The market simply gave up on it — when sentiment turns, the discount closes.`;
}

function grahamNetNetBuyEn(d: DecisionRow, opts: NarrativeOpts): string {
  const v = d.criteria_values ?? {};
  const subject = opts.companyName ? `${d.ticker} (${opts.companyName})` : d.ticker;
  return `Graham found a classic Net-Net in ${subject}: it trades at just ${ratio(v.p_ncav, 3)}× its net current assets. You're effectively buying the cash and inventory at a discount — the operating business comes free.`;
}

function grahamDefensiveBuyEn(d: DecisionRow, opts: NarrativeOpts): string {
  const v = d.criteria_values ?? {};
  const subject = opts.companyName ? `${d.ticker} (${opts.companyName})` : d.ticker;
  return `Graham flagged ${subject} under his "Defensive Investor" rules: reasonable price (P/E ${ratio(v.pe)}, P/B ${ratio(v.pb)}), strong liquidity (current ratio ${ratio(v.current_ratio)}), and steady earnings. A stock you can own without losing sleep.`;
}

function dremanBuyEn(d: DecisionRow, opts: NarrativeOpts): string {
  const v = d.criteria_values ?? {};
  const subject = opts.companyName ? `${d.ticker} (${opts.companyName})` : d.ticker;
  return `Dreman bought ${subject} as a contrarian play: the market hates it — which is exactly when stocks like this tend to surprise to the upside. It's in the cheapest 20% on ${v.qualifying_metrics ?? "?"} of 4 metrics (P/E, P/CF, P/B, or yield).`;
}

function genericSellEn(d: DecisionRow, opts: NarrativeOpts): string {
  const subject = opts.companyName ? `${d.ticker} (${opts.companyName})` : d.ticker;
  const m = (d.rationale ?? "").match(/entry \$([\d.]+), exit \$([\d.]+) \(([+\-−][\d.]+)%\)/);
  if (m) {
    return `Closed ${subject}: in at $${m[1]}, out at $${m[2]} (${m[3]}%). It dropped out of the active candidate set, so the agent moved on.`;
  }
  return `Closed ${subject} because it left the strategy's active candidate set.`;
}

function genericWatchEn(d: DecisionRow, opts: NarrativeOpts): string {
  const subject = opts.companyName ? `${d.ticker} (${opts.companyName})` : d.ticker;
  return `Watching ${subject}: criteria are close but not fully met. If the price falls or fundamentals improve, it may enter the portfolio.`;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Produce a plain-language narrative for one decision. */
export function narrative(
  d: DecisionRow,
  locale: Locale,
  opts: NarrativeOpts = {},
): string {
  const isHe = locale === "he";
  // Use criteria_values.mode if present (Graham emits "NET_NET" or "DEFENSIVE")
  const mode = d.criteria_values?.mode;
  if (d.decision === "BUY") {
    switch (d.agent) {
      case "greenblatt_magic_formula":
        return isHe ? greenblattBuyHe(d, opts) : greenblattBuyEn(d, opts);
      case "walter_schloss":
        return isHe ? schlossBuyHe(d, opts) : schlossBuyEn(d, opts);
      case "benjamin_graham":
        if (mode === "DEFENSIVE")
          return isHe ? grahamDefensiveBuyHe(d, opts) : grahamDefensiveBuyEn(d, opts);
        return isHe ? grahamNetNetBuyHe(d, opts) : grahamNetNetBuyEn(d, opts);
      case "david_dreman":
        return isHe ? dremanBuyHe(d, opts) : dremanBuyEn(d, opts);
    }
  }
  if (d.decision === "SELL") {
    return isHe ? genericSellHe(d, opts) : genericSellEn(d, opts);
  }
  if (d.decision === "WATCH") {
    return isHe ? genericWatchHe(d, opts) : genericWatchEn(d, opts);
  }
  return d.rationale ?? "";
}

/** Translate the trio of badge labels: BUY/SELL/WATCH/HOLD. */
export function decisionLabel(
  decision: string,
  locale: Locale,
): string {
  if (locale === "he") {
    switch (decision) {
      case "BUY":
        return "קנייה";
      case "SELL":
        return "מכירה";
      case "WATCH":
        return "מעקב";
      case "HOLD":
        return "החזקה";
      default:
        return decision;
    }
  }
  return decision;
}
