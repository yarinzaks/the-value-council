// Financial-term explanations shown as tooltips throughout the UI.
// Both languages live alongside each entry so the i18n toggle just
// reaches into the right field.

export interface TermDefinition {
  /** English term-name shown above the tooltip popover. */
  term: string;
  /** Optional Hebrew override; falls back to ``term`` when missing.
   *  Universal acronyms (P/E, P/B, EY, ROC, NCAV) keep their Latin form
   *  in both languages — finance lingo on Hebrew sites uses the same
   *  abbreviations. Multi-word names (Sharpe ratio, Max drawdown,
   *  Information ratio, etc.) are translated. */
  term_he?: string;
  en: string;
  he: string;
}

export const GLOSSARY: Record<string, TermDefinition> = {
  cagr: {
    term: "CAGR",
    en: "Compound Annual Growth Rate — the smooth annual return that gets you from start NAV to end NAV. Better than simple averages because it compounds.",
    he: "תשואה שנתית מורכבת — התשואה השנתית החלקה שמובילה מ־NAV התחלתי לסופי. עדיפה על ממוצע פשוט כי היא מורכבת.",
  },
  alpha: {
    term: "Alpha (α)",
    term_he: "אלפא (α)",
    en: "Excess return vs the benchmark (S&P 500). Positive = strategy beat the index; negative = underperformed.",
    he: "תשואה עודפת מעל המדד (S&P 500). חיובי = האסטרטגיה היכתה את המדד.",
  },
  sharpe: {
    term: "Sharpe ratio",
    term_he: "יחס שארפ",
    en: "Return per unit of risk (volatility). Sharpe > 1.0 is good; > 2.0 is exceptional. Penalizes downside AND upside vol equally.",
    he: "תשואה ליחידת סיכון. שארפ מעל 1.0 טוב; מעל 2.0 יוצא דופן.",
  },
  sortino: {
    term: "Sortino ratio",
    term_he: "יחס סורטינו",
    en: "Like Sharpe but only counts downside volatility — a fairer measure when upside spikes are normal.",
    he: "כמו שארפ, אך מעניש רק תנודתיות כלפי מטה.",
  },
  calmar: {
    term: "Calmar ratio",
    term_he: "יחס קלמר",
    en: "CAGR ÷ |max drawdown|. How much pain the strategy inflicts to deliver its return. > 1.0 means you earned more annually than the worst peak-to-trough loss.",
    he: "CAGR חלקי הירידה המקסימלית. מודד כמה כאב נדרש כדי להשיג את התשואה.",
  },
  max_dd: {
    term: "Max drawdown",
    term_he: "ירידה מקסימלית",
    en: "Largest peak-to-trough decline in NAV during the period. Measures worst-case pain a holder experienced.",
    he: "הירידה הגדולה ביותר משיא לשפל בערך התיק. מודד את הכאב במקרה הגרוע.",
  },
  // ---- Fundamental metrics ----
  pe: {
    term: "P/E (Price/Earnings)",
    en: "Stock price ÷ trailing earnings per share. Lower = cheaper relative to profits. Graham's Defensive Investor wanted P/E ≤ 15.",
    he: "מחיר חלקי רווח למניה. נמוך = זול יחסית לרווחים. גראהם דרש P/E ≤ 15.",
  },
  pb: {
    term: "P/B (Price/Book)",
    en: "Market cap ÷ book value (shareholders' equity). P/B < 1.0 means trading below accounting net worth — a Schloss favorite.",
    he: "שווי שוק חלקי הון עצמי. P/B < 1.0 = נסחרת מתחת לערך החשבונאי — אהוב על שלוס.",
  },
  pcf: {
    term: "P/CF (Price/Cash Flow)",
    en: "Market cap ÷ operating cash flow. Cash-flow version of P/E that's harder to fake with accounting tricks.",
    he: "שווי שוק חלקי תזרים מזומנים תפעולי. גרסה של P/E שקשה יותר לעוות.",
  },
  ey: {
    term: "EY (Earnings Yield)",
    en: "EBIT ÷ Enterprise Value. Greenblatt's 'cheapness' metric — the higher the yield, the more profit you're buying per dollar of EV.",
    he: "EBIT חלקי שווי חברה (EV). מדד הזולות של גרינבלאט — תשואה גבוהה = יותר רווחים לכל דולר.",
  },
  roc: {
    term: "ROC (Return on Capital)",
    en: "EBIT ÷ (Net Working Capital + Net Fixed Assets). Greenblatt's 'quality' metric — measures how efficiently the business turns capital into profit.",
    he: "EBIT חלקי הון מועסק. מדד האיכות של גרינבלאט — מודד יעילות הון.",
  },
  ncav: {
    term: "NCAV (Net Current Asset Value)",
    en: "Current assets − total liabilities. Graham's Net-Net stocks trade at price ≤ ⅔ × NCAV per share — pure liquidation-value plays.",
    he: "נכסים שוטפים פחות התחייבויות סך. במניות Net-Net של גראהם, המחיר ≤ ⅔ × NCAV למניה.",
  },
  p_ncav: {
    term: "P/NCAV",
    en: "Price ÷ NCAV per share. Graham's deepest-value test — anything ≤ 0.667 qualifies as a Net-Net.",
    he: "מחיר חלקי NCAV למניה. מבחן הערך העמוק ביותר של גראהם — מתחת ל־0.667 נחשב Net-Net.",
  },
  de_ratio: {
    term: "D/E (Debt/Equity)",
    en: "Total debt ÷ shareholders' equity. Higher = more leveraged. Most value investors cap at 1.0 — a sign of conservative capital structure.",
    he: "חוב חלקי הון. גבוה = ממונף יותר. רוב משקיעי הערך מגבילים ל־1.0.",
  },
  current_ratio: {
    term: "Current ratio",
    term_he: "יחס שוטף",
    en: "Current assets ÷ current liabilities. Measures short-term liquidity. Graham wanted ≥ 2.0 — twice as many liquid assets as bills due in a year.",
    he: "נכסים שוטפים חלקי התחייבויות שוטפות. מדד נזילות. גראהם דרש ≥ 2.0.",
  },
  dividend_yield: {
    term: "Dividend yield",
    term_he: "תשואת דיבידנד",
    en: "Annual dividends per share ÷ price. Top quintile = 'cheap' on this dimension for Dreman's contrarian screen.",
    he: "דיבידנד שנתי חלקי מחיר. רבעון עליון = 'זול' לפי דרמן.",
  },
  // ---- Strategy concepts ----
  net_net: {
    term: "Net-Net",
    en: "Graham's deepest-value bucket: stocks trading below ⅔ of their net current asset value. Effectively buying the cash & inventory at a discount, with the operating business thrown in for free.",
    he: "סוג הערך העמוק ביותר של גראהם: מניות הנסחרות מתחת ל־⅔ NCAV. רכישת מזומנים ומלאי בהנחה.",
  },
  defensive_investor: {
    term: "Defensive Investor",
    term_he: "המשקיע ההגנתי",
    en: "Graham's 'safer' rules from The Intelligent Investor Ch. 14: P/E ≤ 15, P/B ≤ 1.5, current ratio ≥ 2.0, positive earnings, manageable debt.",
    he: "כללי המשקיע ההגנתי של גראהם מהמשקיע הנבון פרק 14: P/E ≤ 15, P/B ≤ 1.5, יחס שוטף ≥ 2.0.",
  },
  magic_formula: {
    term: "Magic Formula",
    term_he: "הפורמולה הקסומה",
    en: "Greenblatt's two-metric ranking: combined rank of EY (cheapness) + ROC (quality). Buy the top 30, hold for a year, repeat.",
    he: "הפורמולה הקסומה של גרינבלאט: דירוג משולב של EY (זולות) ו־ROC (איכות). 30 הראשונים, לשנה.",
  },
  contrarian: {
    term: "Contrarian (Dreman)",
    term_he: "ניגודי (דרמן)",
    en: "Buy stocks the market hates. Dreman's screen requires bottom-quintile valuation on at least 2 of 4 metrics: P/E, P/CF, P/B, dividend yield.",
    he: "ניגודי (דרמן): קניית מניות שהשוק שונא. רבעון תחתון על לפחות 2 מתוך 4: P/E, P/CF, P/B, תשואת דיבידנד.",
  },
  hit_rate: {
    term: "Hit rate",
    term_he: "אחוז פגיעה",
    en: "Percentage of months with positive returns. Higher is more consistent — but says nothing about magnitude.",
    he: "אחוז החודשים עם תשואה חיובית. גבוה = יציב יותר.",
  },
  ir: {
    term: "Information ratio",
    term_he: "יחס מידע",
    en: "Alpha ÷ tracking error. How efficient the strategy is at producing excess return per unit of deviation from the benchmark.",
    he: "אלפא חלקי שגיאת מעקב. יעילות בייצור תשואה עודפת.",
  },
  nav: {
    term: "NAV",
    en: "Net Asset Value — total portfolio worth right now. Cash + market value of every open position, minus any accrued fees.",
    he: "שווי נטו של התיק כעת — מזומן + שווי שוק של כל הפוזיציות הפתוחות.",
  },
  weight: {
    term: "Weight",
    term_he: "משקל",
    en: "What share of the total portfolio NAV this position takes up. 100% / N for an equal-weight portfolio of N stocks.",
    he: "אחוז מתוך שווי התיק שהפוזיציה הזו תופסת. 100% / N לתיק בהשקעה שווה.",
  },
  pnl: {
    term: "P&L",
    en: "Profit and Loss. Difference between current market value and the cost basis (entry price × shares).",
    he: "רווח/הפסד. ההפרש בין שווי השוק הנוכחי לעלות הקנייה (מחיר כניסה × כמות).",
  },
};

export function getTerm(key: string): TermDefinition | undefined {
  return GLOSSARY[key.toLowerCase()];
}
