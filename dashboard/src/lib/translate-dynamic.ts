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
//
// The shared table
// ----------------
//
// The criteria rules below are no longer the whole story. The same
// strings are rendered twice — here, and by the Telegram notifier in
// Python — and the two had drifted: 26 of the 50 templates the decision
// journal holds (about 90,000 rows) were still arriving in English on
// this side while the notifier translated them. "Fast Grower", "Tier
// A", "quality 5/5", "market cap $…", "posture Neutral".
//
// So the phrase table lives in `data_bundled/hebrew.json` and both
// languages read it. The rules in this file stay for what it does that
// the shared table does not: whole-sentence rationales, watchlist
// triggers and exit triggers, which are this surface's own.
//
// Edited in the JSON, not here.

import hebrew from "../../../data_bundled/hebrew.json" with { type: "json" };

import type { Locale } from "./i18n";

/** The shared phrase table, longest key first so a longer phrase cannot
 *  be pre-empted by a prefix of it — "yield=" must not match inside
 *  "yield=12.0% (top quintile)" and leave an English tail. */
const SHARED_TERMS: readonly [string, string][] = Object.entries(
  hebrew.terms as Record<string, string>,
).sort((a, b) => b[0].length - a[0].length);

/** Patterns whose Hebrew moves a number rather than sitting beside it:
 *  "5yr avg FCF $217,127,200" reads as "FCF ממוצע ל-5 שנים: $217,127,200".
 *  Python writes `\1` for a group; JavaScript wants `$1`. */
const SHARED_PATTERNS: readonly [RegExp, string][] = (
  hebrew.patterns as [string, string][]
).map(([pattern, replacement]) => [
  new RegExp(pattern, "g"),
  replacement.replace(/\\(\d)/g, "$$$1"),
]);

/** Latin that belongs in a Hebrew sentence: the abbreviations an Israeli
 *  investor reads in Latin script, and the units beside them. */
const LATIN_BY_CONVENTION =
  /P\/E|P\/B|P\/CF|P\/NCAV|D\/E|TR\/PE|EV\/EBIT|ROE|ROC|EPS|FCF|PEGY|PEG|MoS|OE|EY|NCAV|CAGR|DCF|EBIT|BVPS|REIT|SPAC|NAV|CEO|USD|nan|CR|bps/g;

/**
 * Whether a string came out half Hebrew and half English sentence.
 *
 * The table is substring replacement, which is right for a format
 * string and actively wrong for a sentence. Applied to the analyst's
 * English it produced "No customer reviews, retention figures או
 * pricing-power evidence were provided" and "Management ספק ביושרה
 * emerges" — ungrammatical in both languages, and worse than the
 * English alone.
 *
 * Checked on the output, not the input. Guarding the input was the
 * first attempt and it was the wrong object: a 110-character reverse
 * trigger is neither long enough nor first-person enough to look like
 * prose, and it mangled anyway. What distinguishes a bad result is
 * visible in the result.
 */
export function isHybrid(text: string): boolean {
  if (!/[\u0590-\u05ff]/.test(text)) return false;
  return /[A-Za-z]{3,}/.test(text.replace(LATIN_BY_CONVENTION, ""));
}

/** Apply the shared table to one string. Numbers are never touched —
 *  the table replaces words around them — and a half-translated result
 *  is discarded in favour of the original. */
function applyShared(text: string): string {
  let out = text;
  for (const [re, replacement] of SHARED_PATTERNS) out = out.replace(re, replacement);
  for (const [english, he] of SHARED_TERMS) {
    if (out.includes(english)) out = out.split(english).join(he);
  }
  return isHybrid(out) ? text : out;
}

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
  // The shared table last, so this file's own rules — which are more
  // specific, and some of which match a whole string — win where both
  // could apply.
  return applyShared(out);
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
      // Through the shared table as well. This returned early, which
      // meant a rationale matching one of the rules above skipped the
      // table completely — 194 of them, every one translated by the
      // notifier and half-translated here.
      // When one of this file's own rules leaves a hybrid, fall back to
      // the shared table on the *original* rather than giving up and
      // returning the English: Dreman's rationale matches a rule here
      // that half-translates it, and the table renders it whole.
      const matched = text.replace(r.re, r.he);
      if (!isHybrid(matched)) return applyShared(matched);
      const shared = applyShared(text);
      return isHybrid(shared) ? text : shared;
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
      // Through the shared table as well. This returned early, which
      // meant a rationale matching one of the rules above skipped the
      // table completely — 194 of them, every one translated by the
      // notifier and half-translated here.
      // When one of this file's own rules leaves a hybrid, fall back to
      // the shared table on the *original* rather than giving up and
      // returning the English: Dreman's rationale matches a rule here
      // that half-translates it, and the table renders it whole.
      const matched = text.replace(r.re, r.he);
      if (!isHybrid(matched)) return applyShared(matched);
      const shared = applyShared(text);
      return isHybrid(shared) ? text : shared;
    }
  }
  return applyRules(text, CRITERIA_RULES);
}

/** Translate a SELL/exit trigger. */
export function translateExitTrigger(text: string, locale: Locale): string {
  if (locale !== "he") return text;
  for (const r of EXIT_RULES) {
    if (r.re.test(text)) {
      // Through the shared table as well. This returned early, which
      // meant a rationale matching one of the rules above skipped the
      // table completely — 194 of them, every one translated by the
      // notifier and half-translated here.
      // When one of this file's own rules leaves a hybrid, fall back to
      // the shared table on the *original* rather than giving up and
      // returning the English: Dreman's rationale matches a rule here
      // that half-translates it, and the table renders it whole.
      const matched = text.replace(r.re, r.he);
      if (!isHybrid(matched)) return applyShared(matched);
      const shared = applyShared(text);
      return isHybrid(shared) ? text : shared;
    }
  }
  return applyRules(text, CRITERIA_RULES);
}

// ---- Memo fields, for the brain page ---------------------------------------
//
// The page rendered the memo as a key/value dump with `key.replace(/_/g, " ")`
// for the label, so every one of the 123 fields a memo can carry was
// titled in raw English — "thesis en", "moat analysis", "fifteen points
// score". And it showed `thesis_en` *and* `thesis_he` side by side, the
// Hebrew already written and the English next to it.

/** Field name → Hebrew label. Nested fields are keyed `parent.child`. */
const MEMO_FIELDS = hebrew.memo_fields as Record<string, string>;

/** Enum value → Hebrew, matched **whole** and never as a substring.
 *
 *  A substring rule on a short word is a trap here: "Fair" would rewrite
 *  the middle of "Fairfax", "stable" the middle of "positive_stable",
 *  and "CALM" is Cal-Maine Foods before it is an adjective. Whole value
 *  or nothing. */
const MEMO_VALUES = hebrew.memo_values as Record<string, string>;

/**
 * A memo's value, in Hebrew where it is one of the analyst's enums.
 *
 * The exact map first, then the phrase table for anything longer — an
 * exit trigger, a sentence. A ticker, a standard abbreviation and the
 * investors' own quotations fall through both and stay as written,
 * which is correct: Fisher's playbook says outright that his quotes
 * stay in English to preserve accuracy.
 */
export function memoValue(value: string, locale: Locale): string {
  if (locale !== "he") return value;
  const exact = MEMO_VALUES[value.trim()];
  if (exact !== undefined) return exact;
  return translateCriterion(value, locale);
}

/**
 * The label for a memo field.
 *
 * `path` is the dotted path, so a nested `scaling_out_plan.full_exit_price`
 * is labelled as itself rather than as whatever `full_exit_price` means
 * somewhere else. Falls back to the old underscore-stripping, so a field
 * a new agent schema introduces is readable rather than blank — and the
 * test that walks the live memos names it.
 */
export function memoFieldLabel(path: string, locale: Locale): string {
  if (locale !== "he") return path.split(".").pop()!.replace(/_/g, " ");
  const label = MEMO_FIELDS[path] ?? MEMO_FIELDS[path.split(".").pop()!];
  return label ?? path.split(".").pop()!.replace(/_/g, " ");
}

/**
 * The memo's fields, with the reader's language picked where the analyst
 * wrote both.
 *
 * Five agents' schemas carry `thesis_en` and `thesis_he` — 180 memos, the
 * Hebrew already there. Showing both is showing the reader a language
 * they did not ask for, beside the one they did. So the twin that matches
 * the locale is kept under the bare name and the other is dropped.
 *
 * Fields the analyst writes in only one language are left alone. Making
 * those bilingual is a change to the five playbooks and a re-run, not
 * something this function can do.
 */
export function memoEntries(
  memo: Record<string, unknown>,
  locale: Locale,
): [string, unknown][] {
  const wanted = locale === "he" ? "_he" : "_en";
  const other = locale === "he" ? "_en" : "_he";
  const out: [string, unknown][] = [];
  const seen = new Set<string>();
  for (const [key, value] of Object.entries(memo ?? {})) {
    if (key.endsWith(other)) {
      const base = key.slice(0, -3);
      // Only drop it when the twin is actually present.
      if (`${base}${wanted}` in memo) continue;
    }
    if (key.endsWith(wanted)) {
      const base = key.slice(0, -3);
      if (seen.has(base)) continue;
      seen.add(base);
      out.push([`${base}${wanted}`, value]);
      continue;
    }
    out.push([key, value]);
  }
  return out;
}

/** The fields a reader wants first, in the order they want them.
 *
 *  The brain page printed all 123 as a two-column grid, per memo, 400
 *  memos deep. Every field equally prominent is no field prominent, and
 *  the founder's word for it was "מתיש" — exhausting.
 *
 *  So a memo now leads with its verdict, its thesis and three figures,
 *  and everything else folds away. These are the three figures: the
 *  first of each group that the memo actually carries, because the five
 *  schemas do not share a vocabulary — Buffett quotes owner earnings
 *  where Lynch quotes PEG.
 */
const HEADLINE_FIGURES: readonly string[][] = [
  ["margin_of_safety_pct", "total_score_out_of_15", "market_temperature_score"],
  ["intrinsic_value_estimate_usd_per_share", "intrinsic_value_estimate_usd", "peg_ratio"],
  ["confidence"],
];

/** Fields where a zero means "could not estimate", not "zero".
 *
 *  A company is never worth $0, and 72 of the 120 memos carrying
 *  `intrinsic_value_estimate_usd` carry it as zero — the snapshot did
 *  not give the analyst what a valuation needs. Printing "$0" as the
 *  headline figure reads as "worth nothing", which is a different and
 *  much stronger claim than the one the memo is making.
 *
 *  `total_score_out_of_15` is deliberately not here: Fisher scoring a
 *  company 0 out of 15 is a verdict, and six memos say exactly that.
 */
const ZERO_MEANS_ABSENT = new Set([
  "intrinsic_value_estimate_usd",
  "intrinsic_value_estimate_usd_per_share",
  "margin_of_safety_pct",
]);

/** The verdict, the thesis and up to three figures — in that order. */
export function memoDigest(
  memo: Record<string, unknown>,
  locale: Locale,
): {
  decision: string | null;
  thesis: string | null;
  figures: [string, unknown][];
} {
  const entries = new Map(memoEntries(memo ?? {}, locale));
  const thesisKey = locale === "he" ? "thesis_he" : "thesis_en";
  const thesis =
    (entries.get(thesisKey) as string | undefined) ??
    (entries.get("thesis_en") as string | undefined) ??
    (entries.get("thesis_he") as string | undefined) ??
    null;

  const figures: [string, unknown][] = [];
  for (const group of HEADLINE_FIGURES) {
    for (const key of group) {
      const value = entries.get(key);
      if (value === undefined || value === null || value === "") continue;
      if (value === 0 && ZERO_MEANS_ABSENT.has(key)) continue;
      figures.push([key, value]);
      break;
    }
  }

  return {
    decision: (memo?.decision as string | undefined) ?? null,
    thesis,
    figures,
  };
}

/** Everything the digest did not show, for the fold. */
export function memoRest(
  memo: Record<string, unknown>,
  locale: Locale,
): [string, unknown][] {
  const digest = memoDigest(memo, locale);
  const shown = new Set<string>(["decision", ...digest.figures.map(([k]) => k)]);
  if (digest.thesis !== null) {
    shown.add(locale === "he" ? "thesis_he" : "thesis_en");
    shown.add("thesis_en");
    shown.add("thesis_he");
  }
  return memoEntries(memo ?? {}, locale).filter(([k]) => !shown.has(k));
}
