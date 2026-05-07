// Dynamic-string translator.
//
// The Python live runner writes English-only text into:
//   * decision_log.criteria_met[]
//   * decision_log.rationale
//   * decision_log.exit_trigger
//   * watch_entry.entry_trigger
//
// Rather than refactor every Python writer to emit two languages,
// we pattern-translate the strings client-side. Patterns are
// deliberately conservative: if no rule matches, we return the
// original English so the user always sees something.
//
// Each rule is a (regex, replacement) pair. ``$1``/``$2`` etc. carry
// matched groups (numbers, ratios) through unchanged.

import type { Locale } from "./i18n";

interface Rule {
  re: RegExp;
  he: string;
}

// ---- Criteria fragments commonly emitted ------------------------------------
const CRITERIA_RULES: Rule[] = [
  // "P/E=X.XX (bottom quintile)" → "P/E=X.XX (רבעון תחתון)"
  { re: /\(bottom quintile\)/g, he: "(רבעון תחתון)" },
  { re: /\(top quintile\)/g, he: "(רבעון עליון)" },

  // "P/B=X.XXX (< 0.75)" — translate the comparator phrase
  { re: /\(<\s*([\d.]+)\)/g, he: "(< $1)" },
  { re: /\(>\s*([\d.]+)\)/g, he: "(> $1)" },
  { re: /\(≤\s*([\d.]+)\)/g, he: "(≤ $1)" },
  { re: /\(≥\s*([\d.]+)\)/g, he: "(≥ $1)" },

  // "yield=23.02% (top quintile)"
  { re: /\byield=/g, he: "תשואה=" },

  // "positive trailing net income"
  {
    re: /positive trailing net income/g,
    he: "רווח נקי חיובי בארבעת הרבעונים האחרונים",
  },

  // "non-positive trailing net income"
  {
    re: /non-positive trailing net income/g,
    he: "רווח נקי לא-חיובי",
  },

  // "current ratio=X.XX (≥ 2.0)"
  { re: /\bcurrent ratio=/g, he: "יחס שוטף=" },

  // "rotated out of target portfolio"
  {
    re: /rotated out of target portfolio/g,
    he: "סובבה החוצה מתיק היעד",
  },

  // ---- "rank" variants — longest-match first so we don't partially
  // translate "combined rank #N" into "combined דירוג #N".
  { re: /\bMagic Formula combined rank #(\d+)\b/g, he: "דירוג משולב בפורמולה הקסומה #$1" },
  { re: /\bcombined rank #(\d+)\b/g, he: "דירוג משולב #$1" },
  { re: /\bEY rank #(\d+)\b/g, he: "EY דירוג #$1" },
  { re: /\bROC rank #(\d+)\b/g, he: "ROC דירוג #$1" },
  { re: /\brank #(\d+)\b/g, he: "דירוג #$1" },

  // "live paper-trade"
  { re: /\blive paper-trade\b/gi, he: "מסחר נייר חי" },

  // "mode=NET_NET" / "mode=DEFENSIVE"
  { re: /\bmode=NET_NET\b/g, he: "מצב=Net-Net" },
  { re: /\bmode=DEFENSIVE\b/g, he: "מצב=הגנתי" },
];

// ---- Rationale fragments (full sentences) ----------------------------------
const RATIONALE_RULES: Rule[] = [
  // Greenblatt
  {
    re: /^EY ([\d.]+%), ROC ([\d.]+%)\. Magic Formula combined rank #(\d+)\.$/,
    he: "EY $1, ROC $2. דירוג משולב #$3 בפורמולה הקסומה.",
  },
  // Greenblatt (Python-side decision-log format):
  // "Magic Formula rank N: EY=0.123, ROC=0.456"
  {
    re: /^Magic Formula rank (\d+): EY=([\d.]+), ROC=([\d.]+)$/,
    he: "פורמולה קסומה דירוג $1: EY=$2, ROC=$3",
  },
  // Schloss (live adapter — full sentence)
  {
    re: /^P\/B ([\d.]+), D\/E ([\d.]+)\. Trades below tangible book — Schloss deep-value bargain\.$/,
    he: "P/B $1, D/E $2. נסחרת מתחת להון העצמי המוחשי — מציאה לפי שלוס.",
  },
  // Schloss (Python decision-log):
  // "Schloss deep-value: P/B 0.13, D/E 0.96, BVPS 382.41"
  {
    re: /^Schloss deep-value: P\/B ([\d.]+), D\/E ([\d.]+), BVPS ([\d.]+)$/,
    he: "ערך עמוק לפי שלוס: P/B $1, D/E $2, הון למניה $3",
  },
  // Graham Net-Net (live adapter)
  {
    re: /^P\/NCAV ([\d.]+) \(≤ ⅔\), NCAV\/share \$([\d.]+)\. Classic Graham Net-Net\.$/,
    he: "P/NCAV $1 (≤ ⅔), NCAV למניה ‎$$2‎. Net-Net קלאסי לפי גראהם.",
  },
  // Graham Defensive (live adapter)
  {
    re: /^P\/E ([\d.]+), P\/B ([\d.]+), current ratio ([\d.]+)\. Graham Defensive Investor \(Intelligent Investor Ch\.14\)\.$/,
    he: "P/E $1, P/B $2, יחס שוטף $3. \"המשקיע ההגנתי\" של גראהם (פרק 14).",
  },
  // Graham (Python decision-log)
  {
    re: /^Graham Net-Net: P\/NCAV ([\d.]+), NCAV\/share ([\d.]+)$/,
    he: "Net-Net לפי גראהם: P/NCAV $1, NCAV למניה $2",
  },
  {
    re: /^Graham Defensive: P\/E ([\d.]+), P\/B ([\d.]+), CR ([\d.]+), Graham # ([\d.]+)$/,
    he: "המשקיע ההגנתי לפי גראהם: P/E $1, P/B $2, יחס שוטף $3, מס׳ גראהם $4",
  },
  // Dreman (live adapter)
  {
    re: /^Bottom-quintile on (\d)\/4 \(([^)]+)\)\. Dreman contrarian, composite rank ([\d.]+)\.$/,
    he: "רבעון תחתון על $1/4 ($2). ניגודי לפי דרמן, דירוג משולב $3.",
  },
  // Dreman (Python decision-log)
  {
    re: /^Dreman contrarian: bottom-quintile on (\d)\/4 metrics, composite rank ([\d.]+)$/,
    he: "ניגודי לפי דרמן: רבעון תחתון על $1/4 מדדים, דירוג משולב $2",
  },
  // Greenblatt (live-adapter alt phrasing)
  {
    re: /^Magic Formula combined rank #(\d+)\. EY ([\d.]+%), ROC ([\d.]+%)\.$/,
    he: "פורמולה קסומה דירוג משולב #$1. EY $2, ROC $3.",
  },
  // Closed position rationale (from runner._log_sell)
  {
    re: /^Closed live position: entry \$([\d.]+), exit \$([\d.]+) \(([+\-−][\d.]+)%\)$/,
    he: "סגירת פוזיציה חיה: כניסה ‎$$1, יציאה ‎$$2 ($3%)",
  },
];

// ---- Watchlist entry triggers ---------------------------------------------
const TRIGGER_RULES: Rule[] = [
  {
    re: /^Magic Formula combined rank enters top \d+$/,
    he: "דירוג משולב נכנס לטופ 30 בפורמולה הקסומה",
  },
  {
    re: /^P\/B drops into Schloss bargain range$/,
    he: "P/B יורד לטווח המציאה לפי שלוס",
  },
  {
    re: /^P\/NCAV enters Graham buy zone OR Defensive thresholds met$/,
    he: "P/NCAV נכנס לאזור הקנייה של גראהם או שעמדו ספי המשקיע ההגנתי",
  },
  {
    re: /^joins bottom-quintile cohort on 2\+ metrics$/,
    he: "מצטרף לרבעון התחתון בלפחות 2 מדדים",
  },
  {
    re: /^rank enters top portfolio$/,
    he: "דירוג נכנס לתיק הפעיל",
  },
];

// ---- Exit triggers ---------------------------------------------------------
const EXIT_RULES: Rule[] = [
  {
    re: /^P\/NCAV reverts toward 1\.0× OR ~50% gain$/,
    he: "P/NCAV חוזר לכיוון 1.0× או ~50% רווח",
  },
  {
    re: /^P\/E or P\/B revert above thresholds; OR fundamentals deteriorate$/,
    he: "P/E או P/B חוזרים מעל הספים או שהיסודות מתדרדרים",
  },
  {
    re: /^metrics revert toward median \(composite rank >= 0\.5\) OR ~50% gain$/,
    he: "המדדים חוזרים לכיוון החציון (דירוג משולב >= 0.5) או ~50% רווח",
  },
  {
    re: /^Combined rank slips out of top 30 OR holding ≥ 1 year$/,
    he: "דירוג משולב יוצא מהטופ 30 או החזקה ≥ שנה",
  },
  {
    re: /^annual rotation OR rank slips out of top-N$/,
    he: "סבב שנתי או דירוג יוצא מהטופ-N",
  },
  {
    re: /^P\/B reverts toward 1\.0× book OR ~50% gain$/,
    he: "P/B חוזר לכיוון 1.0× ההון העצמי או ~50% רווח",
  },
];

function applyRules(text: string, rules: Rule[]): string {
  let out = text;
  for (const r of rules) {
    out = out.replace(r.re, r.he);
  }
  return out;
}

/** Translate a single ``criteria_met`` entry. */
export function translateCriterion(text: string, locale: Locale): string {
  if (locale !== "he") return text;
  return applyRules(text, CRITERIA_RULES);
}

/** Translate a rationale string (free-text, may match a known template). */
export function translateRationale(text: string, locale: Locale): string {
  if (locale !== "he") return text;
  for (const r of RATIONALE_RULES) {
    if (r.re.test(text)) {
      return text.replace(r.re, r.he);
    }
  }
  // Fall through to per-fragment translation for unknown rationales.
  return applyRules(text, CRITERIA_RULES);
}

/** Translate a watchlist entry trigger. */
export function translateTrigger(text: string, locale: Locale): string {
  if (locale !== "he") return text;
  for (const r of TRIGGER_RULES) {
    if (r.re.test(text)) {
      return text.replace(r.re, r.he);
    }
  }
  return applyRules(text, CRITERIA_RULES);
}

/** Translate a SELL/exit trigger. */
export function translateExitTrigger(text: string, locale: Locale): string {
  if (locale !== "he") return text;
  for (const r of EXIT_RULES) {
    if (r.re.test(text)) {
      return text.replace(r.re, r.he);
    }
  }
  return applyRules(text, CRITERIA_RULES);
}
