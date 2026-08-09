import type { AgentSlug } from "./types";

export interface AgentMeta {
  slug: AgentSlug;
  display_name: string;
  display_name_he: string;
  description: string;
  description_he: string;
  /** Source playbook section / school of thought (English). */
  school: string;
  school_he: string;
  color: string;
}

/**
 * Static metadata for all 10 council members.
 * Hebrew labels included so the i18n toggle works without an extra call.
 *
 * The 4 original quant agents (Greenblatt, Schloss, Graham, Dreman) live
 * in `core/live/runner.py`'s `build_default_adapters`. The 6 hybrid
 * agents below (Neff, Buffett, Lynch, Marks, Klarman, Fisher) ship as
 * Python+LLM hybrids — backtest-only on this branch; live adapters
 * follow in a subsequent PR.
 */
export const AGENTS: AgentMeta[] = [
  {
    slug: "greenblatt_magic_formula",
    display_name: "Joel Greenblatt",
    display_name_he: "ג'ואל גרינבלאט",
    description: "Magic Formula: rank by EBIT/EV + ROC, hold 30, annual rebalance.",
    description_he: "הפורמולה הקסומה: דירוג לפי תשואה על הון ותשואת רווח, 30 מניות, איזון שנתי.",
    school: "Quality + Value (Special Situations)",
    school_he: "איכות + ערך (מצבים מיוחדים)",
    color: "#7c3aed",
  },
  {
    slug: "walter_schloss",
    display_name: "Walter Schloss",
    display_name_he: "וולטר שלוס",
    description: "Net-net deep value: low P/B, conservative debt, broad diversification.",
    description_he: "ערך עמוק: P/B נמוך, חוב שמרני, פיזור רחב.",
    school: "Statistical Bargain Hunting",
    school_he: "ציד מציאות סטטיסטי",
    color: "#0ea5e9",
  },
  {
    slug: "benjamin_graham",
    display_name: "Benjamin Graham",
    display_name_he: "בנג'מין גראהם",
    description: "Net-Net (P ≤ ⅔ NCAV) with Defensive Investor fallback.",
    description_he: "Net-Net עם נסיגה למשקיע ההגנתי כשאין מספיק מציאות.",
    school: "Defensive / Deep Value",
    school_he: "ערך עמוק / הגנתי",
    color: "#059669",
  },
  {
    slug: "david_dreman",
    display_name: "David Dreman",
    display_name_he: "דייוויד דרמן",
    description: "Contrarian: bottom 20% on 2+ of P/E, P/CF, P/B, dividend yield.",
    description_he: "ניגודי: רבעון תחתון על 2+ מתוך P/E, P/CF, P/B, תשואת דיבידנד.",
    school: "Contrarian Value",
    school_he: "ערך ניגודי",
    color: "#dc2626",
  },
  {
    slug: "john_neff",
    display_name: "John Neff",
    display_name_he: "ג'ון נף",
    description:
      "Total-Return / PE: 7-criteria soft scoring with industry-relative medians.",
    description_he:
      "תשואה כוללת חלקי מכפיל: ניקוד רך של 7 קריטריונים מול חציוני הענף.",
    school: "Yield-Adjusted Value",
    school_he: "ערך מתואם תשואה",
    color: "#ea580c",
  },
  {
    slug: "warren_buffett",
    display_name: "Warren Buffett",
    display_name_he: "וורן באפט",
    description:
      "Wonderful business at a fair price: 6 acquisition criteria + Owner-Earnings DCF + LLM moat verdict.",
    description_he:
      "עסק נפלא במחיר הוגן: 6 קריטריונים, DCF על Owner Earnings ובדיקת חפיר באמצעות LLM.",
    school: "Quality + Moats",
    school_he: "איכות וחפירים",
    color: "#b91c1c",
  },
  {
    slug: "peter_lynch",
    display_name: "Peter Lynch",
    display_name_he: "פיטר לינץ'",
    description:
      "GARP: PEG ranking with 6-category classification (Slow/Stalwart/Fast Grower + LLM for cyclical/turnaround/asset).",
    description_he:
      "צמיחה במחיר סביר: PEG עם סיווג ל-6 קטגוריות (איטי/יציב/מהיר + LLM למחזורי/מהפך/נכסי).",
    school: "Growth At Reasonable Price",
    school_he: "צמיחה במחיר סביר",
    color: "#16a34a",
  },
  {
    slug: "howard_marks",
    display_name: "Howard Marks",
    display_name_he: "הווארד מארקס",
    description:
      "Cycle-aware value: market-temperature posture (Cold→Hot) + cycle-adjusted ranking + LLM second-level memo.",
    description_he:
      "ערך מודע מחזור: מד טמפרטורת שוק (קר→חם) + ניקוד מותאם מחזור + ניתוח 'שכבה שנייה' של LLM.",
    school: "Cycle Positioning",
    school_he: "מיצוב מחזורי",
    color: "#0891b2",
  },
  {
    slug: "seth_klarman",
    display_name: "Seth Klarman",
    display_name_he: "סת' קלרמן",
    description:
      "Margin of Safety: conservative DCF with 30% MoS floor + cash-as-residual sizing + LLM 'what could go wrong'.",
    description_he:
      "מרווח ביטחון: DCF שמרני ברף 30% MoS, מזומן כשארית, וניתוח 'מה יכול להשתבש' של LLM.",
    school: "Risk-First Value",
    school_he: "ערך עם דגש על סיכון",
    color: "#a16207",
  },
  {
    slug: "philip_fisher",
    display_name: "Philip Fisher",
    display_name_he: "פיליפ פישר",
    description:
      "Quality growth: 5-point quant + 15-point LLM scuttlebutt; tier-weighted, multi-decade hold.",
    description_he:
      "צמיחה איכותית: 5 נקודות כמותיות + 15 נקודות סקאטלבאט מבוסס LLM; ניקוד שכבתי לטווח עשורים.",
    school: "Quality Growth + Scuttlebutt",
    school_he: "צמיחה איכותית + סקאטלבאט",
    color: "#9333ea",
  },
  {
    // Not an investor, and the description says so. This one answers to
    // a measurement rather than to a book: it holds the largest liquid
    // US companies weighted by what they are worth, and it is here
    // because it was the only one of twenty-one designs to beat the
    // index in both halves of the research history. Its edge is being
    // more concentrated in the biggest names than the index is, which
    // is a bet on continued mega-cap leadership rather than a
    // mispricing — 2022 is the demonstration, at -28.1% against -18.2%.
    slug: "market_core",
    display_name: "Market Core",
    display_name_he: "ליבת השוק",
    description:
      "The 25 largest liquid US companies, capitalisation-weighted, quarterly. Not stock picking — a concentrated bet on mega-cap leadership.",
    description_he:
      "25 החברות האמריקאיות הגדולות והסחירות, במשקל שווי שוק, איזון רבעוני. לא בחירת מניות — הימור מרוכז על הובלת הענקיות.",
    school: "Evidence, not biography",
    school_he: "ראיות, לא ביוגרפיה",
    color: "#0891b2",
  },
];

/** Locale-aware metadata accessor. */
export function metaLocalized(slug: string, locale: "en" | "he") {
  const m = metaFor(slug);
  if (!m) return undefined;
  return {
    ...m,
    display: locale === "he" ? m.display_name_he : m.display_name,
    school_label: locale === "he" ? m.school_he : m.school,
    description_label: locale === "he" ? m.description_he : m.description,
  };
}

export function metaFor(slug: string): AgentMeta | undefined {
  return AGENTS.find((a) => a.slug === slug);
}
