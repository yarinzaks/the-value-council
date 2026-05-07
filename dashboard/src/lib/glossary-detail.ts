// Extended glossary entries — adds formula + example per term.
// Used by the /glossary page. Each entry has both English and Hebrew
// short explanations (≤ 2 lines), the formula, and a worked example.

import type { Locale } from "./i18n";

export interface GlossaryEntry {
  /** Stable key (also used as URL anchor). */
  key: string;
  /** Term name in English (often the canonical Latin abbrev). */
  term: string;
  /** Term name in Hebrew (full name where it differs from term). */
  term_he: string;
  /** Short explanation, English. ≤ 2 short sentences. */
  explanation_en: string;
  /** Short explanation, Hebrew. ≤ 2 short sentences. */
  explanation_he: string;
  /** Formula string (universal — same in both languages). */
  formula: string;
  /** Worked example, English. */
  example_en: string;
  /** Worked example, Hebrew. */
  example_he: string;
}

export const GLOSSARY_ENTRIES: GlossaryEntry[] = [
  {
    key: "pe",
    term: "P/E",
    term_he: "מכפיל רווח (P/E)",
    explanation_en:
      "How many years of current earnings the stock costs. Lower = cheaper.",
    explanation_he:
      "כמה שנות רווח נוכחי המניה עולה. נמוך = זול יותר.",
    formula: "P/E = Price ÷ Earnings per Share",
    example_en:
      "If a stock trades at $100 and earnings per share are $10, then P/E = 10.",
    example_he:
      "אם מניה נסחרת ב-‎$100 והרווח למניה ‎$10, אז P/E = 10.",
  },
  {
    key: "pb",
    term: "P/B",
    term_he: "מכפיל הון (P/B)",
    explanation_en:
      "Price compared to book value (accounting net worth). Below 1.0 means market is selling the company for less than its books say it's worth.",
    explanation_he:
      "מחיר בהשוואה לערך הספרי (ההון העצמי החשבונאי). מתחת ל-1.0 = השוק מוכר את החברה בפחות מהשווי החשבונאי שלה.",
    formula: "P/B = Market Cap ÷ Total Shareholders' Equity",
    example_en:
      "Market cap $100M, equity $200M → P/B = 0.5. You're paying 50¢ for every $1 of book.",
    example_he:
      "שווי שוק 100 מיליון, הון עצמי 200 מיליון → P/B = 0.5. שילמת 50 סנט לכל ‎$1 הון.",
  },
  {
    key: "pcf",
    term: "P/CF",
    term_he: "מכפיל תזרים מזומנים (P/CF)",
    explanation_en:
      "Like P/E but uses operating cash flow instead of earnings. Cash is harder to fake than earnings.",
    explanation_he:
      "כמו P/E אך עם תזרים מזומנים תפעולי במקום רווח. תזרים מזומנים קשה יותר לעוות מרווח חשבונאי.",
    formula: "P/CF = Market Cap ÷ Operating Cash Flow",
    example_en:
      "$1B market cap with $100M operating cash flow → P/CF = 10.",
    example_he:
      "שווי שוק 1 מיליארד עם תזרים תפעולי 100 מיליון → P/CF = 10.",
  },
  {
    key: "ey",
    term: "EY (Earnings Yield)",
    term_he: "תשואת רווח (EY)",
    explanation_en:
      "How much profit you'd get per dollar invested in the entire company (including debt). Higher = cheaper.",
    explanation_he:
      "כמה רווח תקבל על כל דולר שתשקיע בכל החברה (כולל החוב). גבוה = זול יותר.",
    formula: "EY = EBIT ÷ Enterprise Value",
    example_en:
      "EBIT $200M, EV $1B → EY = 20%. You earn 20¢ of operating profit per $1 of EV.",
    example_he:
      "EBIT 200 מיליון, EV מיליארד → EY = 20%. תקבל 20 סנט רווח תפעולי על כל ‎$1 מ-EV.",
  },
  {
    key: "roc",
    term: "ROC",
    term_he: "תשואה על הון (ROC)",
    explanation_en:
      "How efficiently the business turns invested capital into profit. High ROC = a good business.",
    explanation_he:
      "כמה ביעילות העסק הופך הון מושקע לרווח. ROC גבוה = עסק טוב.",
    formula: "ROC = EBIT ÷ (Net Working Capital + Net Fixed Assets)",
    example_en:
      "EBIT $200M, capital employed $400M → ROC = 50%. The business doubles its capital base every two years from operations.",
    example_he:
      "EBIT 200 מיליון, הון מועסק 400 מיליון → ROC = 50%. העסק מכפיל את ההון שלו בכל שנתיים.",
  },
  {
    key: "ncav",
    term: "NCAV",
    term_he: "נכסים שוטפים נטו (NCAV)",
    explanation_en:
      "Current assets minus all liabilities. Graham's deepest-value test: if a stock trades below ⅔ of NCAV, you're buying the cash and inventory at a discount.",
    explanation_he:
      "נכסים שוטפים פחות סך ההתחייבויות. מבחן הערך העמוק של גראהם: אם המניה נסחרת מתחת ל-⅔ מ-NCAV, אתה קונה את המזומן והמלאי בהנחה.",
    formula: "NCAV = Current Assets − Total Liabilities",
    example_en:
      "Current assets $300M, total liabilities $100M → NCAV = $200M. With 100M shares, NCAV per share = $2.",
    example_he:
      "נכסים שוטפים 300 מיליון, התחייבויות 100 מיליון → NCAV = 200 מיליון. עם 100 מיליון מניות, NCAV למניה = ‎$2.",
  },
  {
    key: "de",
    term: "D/E",
    term_he: "יחס חוב להון (D/E)",
    explanation_en:
      "How much debt the company has per dollar of equity. Above 1.0 = more debt than equity. Most value investors cap at 1.0.",
    explanation_he:
      "כמה חוב יש לחברה לכל דולר של הון עצמי. מעל 1.0 = יותר חוב מהון. רוב משקיעי הערך מגבילים ל-1.0.",
    formula: "D/E = Total Debt ÷ Shareholders' Equity",
    example_en:
      "Total debt $500M, equity $1B → D/E = 0.5. The company is conservatively financed.",
    example_he:
      "חוב 500 מיליון, הון מיליארד → D/E = 0.5. החברה ממומנת באופן שמרני.",
  },
  {
    key: "nav",
    term: "NAV",
    term_he: "שווי נכסי נטו (NAV)",
    explanation_en:
      "Total worth of the portfolio right now. Cash plus the market value of every open position.",
    explanation_he:
      "הערך הכולל של התיק כעת. מזומן ועוד שווי השוק של כל הפוזיציות הפתוחות.",
    formula: "NAV = Cash + Σ (Shares × Current Price)",
    example_en:
      "$2,000 cash + 50 shares of AAPL @ $200 = NAV $12,000.",
    example_he:
      "‎$2,000 מזומן + 50 מניות AAPL ב-‎$200 = NAV ‎$12,000.",
  },
  {
    key: "alpha",
    term: "Alpha (α)",
    term_he: "אלפא (α)",
    explanation_en:
      "Return ABOVE the benchmark. Positive alpha = the strategy is creating value beyond just being in the market.",
    explanation_he:
      "תשואה מעל המדד. אלפא חיובית = האסטרטגיה מייצרת ערך מעבר לעצם החזקת השוק.",
    formula: "Alpha = Strategy Return − Benchmark Return",
    example_en:
      "Strategy +20%, S&P 500 +10% → α = +10 percentage points.",
    example_he:
      "אסטרטגיה +20%, S&P 500 +10% → α = +10 נקודות אחוז.",
  },
  {
    key: "sharpe",
    term: "Sharpe ratio",
    term_he: "יחס שארפ",
    explanation_en:
      "Risk-adjusted return. > 1.0 is good, > 2.0 is exceptional. Penalizes both upside and downside volatility.",
    explanation_he:
      "תשואה מתואמת סיכון. מעל 1.0 = טוב, מעל 2.0 = יוצא דופן. מעניש תנודתיות לכל כיוון.",
    formula: "Sharpe = (Return − Risk-Free Rate) ÷ Standard Deviation",
    example_en:
      "10% return, 5% risk-free, 5% std dev → Sharpe = (10-5)/5 = 1.0.",
    example_he:
      "תשואה 10%, ריבית חסרת סיכון 5%, סטיית תקן 5% → שארפ = (10-5)/5 = 1.0.",
  },
  {
    key: "cagr",
    term: "CAGR",
    term_he: "תשואה שנתית מורכבת",
    explanation_en:
      "The smooth annual return that compounds your starting NAV into your ending NAV. Better than averaging year-by-year returns.",
    explanation_he:
      "התשואה השנתית החלקה שמובילה מ-NAV ההתחלתי לסופי. עדיפה על ממוצע פשוט.",
    formula: "CAGR = (End NAV ÷ Start NAV)^(1/Years) − 1",
    example_en:
      "Start $10K, end $14.4K after 4 years → CAGR = (14.4/10)^(1/4) − 1 ≈ 9.6%.",
    example_he:
      "התחלה ‎$10 אלף, סוף ‎$14.4 אלף אחרי 4 שנים → CAGR ≈ 9.6%.",
  },
  {
    key: "max_dd",
    term: "Max drawdown",
    term_he: "ירידה מקסימלית",
    explanation_en:
      "The largest peak-to-trough loss the portfolio experienced. Tells you the worst pain a holder went through.",
    explanation_he:
      "ההפסד הגדול ביותר משיא לשפל שחווה התיק. מראה את הכאב במקרה הגרוע ביותר.",
    formula: "Max DD = max((NAV_peak − NAV_trough) ÷ NAV_peak)",
    example_en:
      "NAV peaks at $20K, falls to $14K → max drawdown = 30%.",
    example_he:
      "NAV מגיע לשיא של ‎$20K ויורד ל-‎$14K → ירידה מקסימלית = 30%.",
  },
  {
    key: "current_ratio",
    term: "Current ratio",
    term_he: "יחס שוטף",
    explanation_en:
      "Short-term liquidity check: liquid assets compared to bills due within a year. Graham wanted ≥ 2.0.",
    explanation_he:
      "מבחן נזילות קצר טווח: נכסים שוטפים מול חובות לשנה. גראהם דרש ≥ 2.0.",
    formula: "Current Ratio = Current Assets ÷ Current Liabilities",
    example_en:
      "Current assets $200M, current liabilities $80M → 2.5. Plenty of liquidity.",
    example_he:
      "נכסים שוטפים 200 מיליון, התחייבויות שוטפות 80 מיליון → 2.5. נזילות שופעת.",
  },
  {
    key: "dividend_yield",
    term: "Dividend yield",
    term_he: "תשואת דיבידנד",
    explanation_en:
      "Cash dividends per year as a fraction of price. High yield can mean the stock is cheap — or that the dividend is at risk.",
    explanation_he:
      "דיבידנד שנתי כאחוז מהמחיר. תשואה גבוהה יכולה להעיד על מניה זולה — או על דיבידנד שנמצא בסיכון.",
    formula: "Yield = Annual Dividends per Share ÷ Price",
    example_en:
      "$2 dividend on a $50 stock → 4% yield.",
    example_he:
      "דיבידנד ‎$2 על מניה ב-‎$50 → תשואה 4%.",
  },
  {
    key: "market_cap",
    term: "Market cap",
    term_he: "שווי שוק",
    explanation_en:
      "What it would cost to buy every share of the company at the current price. Equity value, ignoring debt.",
    explanation_he:
      "כמה יעלה לקנות את כל מניות החברה במחיר הנוכחי. שווי ההון העצמי, ללא חוב.",
    formula: "Market Cap = Shares Outstanding × Price per Share",
    example_en:
      "100M shares at $50 → market cap $5B.",
    example_he:
      "100 מיליון מניות ב-‎$50 → שווי שוק 5 מיליארד.",
  },
  {
    key: "ev",
    term: "Enterprise Value (EV)",
    term_he: "שווי חברה (EV)",
    explanation_en:
      "What it would cost to buy the whole company AND pay off its debt with its cash. The 'real' price of the business.",
    explanation_he:
      "כמה יעלה לקנות את כל החברה ולהשתמש במזומן שלה כדי לסגור את החוב. המחיר 'האמיתי' של העסק.",
    formula: "EV = Market Cap + Total Debt − Cash",
    example_en:
      "$5B market cap + $1B debt − $200M cash → EV = $5.8B.",
    example_he:
      "שווי שוק 5 מיליארד + 1 מיליארד חוב − 200 מיליון מזומן → EV = 5.8 מיליארד.",
  },
  {
    key: "fcf",
    term: "FCF (Free Cash Flow)",
    term_he: "תזרים מזומנים חופשי (FCF)",
    explanation_en:
      "Cash the business generates after paying for everything it needs to keep operating. The cash that's truly available to owners.",
    explanation_he:
      "המזומן שהעסק מייצר אחרי שכל ההוצאות התפעוליות וההשקעות החיוניות שולמו. המזומן שזמין באמת לבעלים.",
    formula: "FCF = Operating Cash Flow − Capital Expenditures",
    example_en:
      "$500M operating cash flow − $150M capex → $350M FCF.",
    example_he:
      "תזרים תפעולי 500 מיליון − 150 מיליון השקעות = 350 מיליון FCF.",
  },
  {
    key: "roe",
    term: "ROE (Return on Equity)",
    term_he: "תשואה על ההון (ROE)",
    explanation_en:
      "What % of shareholders' equity the company turns into profit each year. > 15% = consistently good business.",
    explanation_he:
      "איזה אחוז מההון העצמי של בעלי המניות החברה הופכת לרווח בשנה. מעל 15% = עסק טוב באופן עקבי.",
    formula: "ROE = Net Income ÷ Shareholders' Equity",
    example_en:
      "$100M net income, $500M equity → ROE = 20%.",
    example_he:
      "רווח נקי 100 מיליון, הון 500 מיליון → ROE = 20%.",
  },
  {
    key: "margin_of_safety",
    term: "Margin of Safety",
    term_he: "מרווח ביטחון",
    explanation_en:
      "The gap between price you paid and the company's intrinsic value. Buffett: 'be willing to pay 50¢ for $1 of value.'",
    explanation_he:
      "הפער בין המחיר ששילמת לערך הפנימי של החברה. באפט: 'תהיה מוכן לשלם 50 סנט עבור ‎$1 של ערך'.",
    formula: "MoS = (Intrinsic Value − Price) ÷ Intrinsic Value",
    example_en:
      "Intrinsic value $100, paid $60 → 40% margin of safety.",
    example_he:
      "ערך פנימי ‎$100, שילמת ‎$60 → מרווח ביטחון 40%.",
  },
];

/** Lookup by key (case-insensitive). */
export function findEntry(key: string): GlossaryEntry | undefined {
  const k = key.toLowerCase();
  return GLOSSARY_ENTRIES.find((e) => e.key === k);
}

/** Filter by free-text query in either language. */
export function searchEntries(q: string, locale: Locale): GlossaryEntry[] {
  const needle = q.trim().toLowerCase();
  if (!needle) return GLOSSARY_ENTRIES;
  return GLOSSARY_ENTRIES.filter((e) => {
    const haystack = (
      locale === "he"
        ? `${e.term} ${e.term_he} ${e.explanation_he} ${e.formula}`
        : `${e.term} ${e.term_he} ${e.explanation_en} ${e.formula}`
    ).toLowerCase();
    return haystack.includes(needle);
  });
}
