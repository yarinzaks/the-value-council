// The dashboard must render the same Hebrew the notifier does.
//
// The two had drifted. The phrase table was written twice — once in
// Python for the Telegram message, once in TypeScript for this site —
// and 26 of the 50 criteria templates the decision journal holds were
// still arriving in English here while the notifier translated them.
// About 90,000 journal rows: "Fast Grower", "Tier A", "quality 5/5",
// "market cap $…", "posture Neutral".
//
// They now read one file, `data_bundled/hebrew.json`. This suite walks
// the real journal and fails if anything in it still comes back in
// English — so a template a future strategy writes shows up here rather
// than on a page.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  translateCriterion,
  translateExitTrigger,
  translateRationale,
} from "../src/lib/translate-dynamic.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "../..");
const BUNDLE = JSON.parse(
  readFileSync(join(ROOT, "data_bundled/hebrew.json"), "utf8"),
);

/** Latin that stays Latin: the abbreviations a Hebrew-speaking investor
 *  reads in Latin script, exactly as the Hebrew financial press and
 *  every Israeli broker's screen print them.
 *
 *  This list has to match the one the Python side is measured against.
 *  It did not — DCF, CAGR, NCAV, BVPS and EV/EBIT were missing — so the
 *  test reported 510 untranslated rationales that were in fact fully
 *  translated, and the one phrase it named ("… DCF שמרני") was correct
 *  in both languages. A test's own allowlist is as load-bearing as the
 *  table it checks.
 *
 *  `nan` is here because the notifier drops a criterion carrying one and
 *  this site may still render it — see core/live/notify.py. */
const KEEP =
  /P\/E|P\/B|P\/CF|P\/NCAV|D\/E|TR\/PE|EV\/EBIT|ROE|ROC|EPS|FCF|PEGY|PEG|MoS|OE|EY|NCAV|CAGR|DCF|EBIT|BVPS|Net-Net|SPAC|REIT|XBRL|nan/g;

function stillEnglish(text) {
  return /[A-Za-z]{3,}/.test(text.replace(KEEP, ""));
}

test("the shared table is well formed", () => {
  assert.ok(Object.keys(BUNDLE.terms).length > 20, "the table is suspiciously small");
  for (const [english, he] of Object.entries(BUNDLE.terms)) {
    assert.notEqual(english, he, `${english} translates to itself`);
    assert.match(he, /[֐-׿]/, `${english} has no Hebrew`);
  }
  for (const [pattern, replacement] of BUNDLE.patterns) {
    assert.doesNotThrow(() => new RegExp(pattern), pattern);
    assert.match(replacement, /[֐-׿]/, pattern);
  }
});

test("the phrases the notifier translates, this side translates too", () => {
  // Every key in the shared table, through this file's own entry point.
  for (const english of Object.keys(BUNDLE.terms)) {
    const he = translateCriterion(english, "he");
    assert.ok(!stillEnglish(he), `${english} -> ${he}`);
  }
});

// Four keys this side renders differently from the table. Each is a
// second valid Hebrew wording, not a wrong one, so they are named here
// rather than silently tolerated — and anything NOT named fails.
//
// They are a real inconsistency and should be settled on one wording,
// which is a change to what the dashboard says and so not this fix's
// business. What is this fix's business is that the list cannot grow.
const KNOWN_DIVERGENCES = new Set([
  "positive trailing net income", // "in the last year" vs "in the last four quarters"
  "rotated out of target portfolio", // "left" vs "was rotated out of"
  "mode=DEFENSIVE", // "track:" vs "mode="
  "mode=NET_NET", // and this one keeps Net-Net in Latin
]);

test("the phrases the notifier translates, this side words the same", () => {
  // The test above asserts there is Hebrew in the output. It does not
  // assert it is the SAME Hebrew, and its name claims it does — so it
  // was green at 136/136 while the two surfaces disagreed on six keys,
  // two of them a plain error: the table says the agents screen the
  // cheapest fifth, and this side said the cheapest quarter.
  const disagreeing = [];
  for (const [english, table] of Object.entries(BUNDLE.terms)) {
    const got = translateCriterion(english, "he");
    if (got !== table && !KNOWN_DIVERGENCES.has(english)) {
      disagreeing.push(`${english}\n    table: ${table}\n    here : ${got}`);
    }
  }
  assert.deepEqual(disagreeing, [], disagreeing.join("\n  "));
});

test("no key is excused that already agrees", () => {
  // An excuse outliving the disagreement it covers is how the list
  // grows back.
  for (const english of KNOWN_DIVERGENCES) {
    assert.ok(english in BUNDLE.terms, `${english} is not a table key`);
    assert.notEqual(
      translateCriterion(english, "he"),
      BUNDLE.terms[english],
      `${english} agrees now — drop it from KNOWN_DIVERGENCES`,
    );
  }
});

test("a quintile is a fifth on both surfaces", () => {
  // The word the reader meets. "רבעון" is a calendar quarter in Hebrew
  // before it is anything else, and the screen is DEFAULT_QUINTILE =
  // 0.20 in agents/dreman/filters.py.
  for (const english of ["(bottom quintile)", "(top quintile)"]) {
    const he = translateCriterion(english, "he");
    assert.match(he, /חמישון/, `${english} -> ${he}`);
    assert.doesNotMatch(he, /רבעון/, `${english} -> ${he}`);
  }
  // And in the sentence the Dreman adapter writes, where the phrase is
  // embedded rather than standing alone.
  const sentence = translateRationale(
    "Bottom-quintile on 3/4 (P/E, P/CF, P/B). Dreman contrarian, composite rank 0.123.",
    "he",
  );
  assert.match(sentence, /בחמישון התחתון/, sentence);
  assert.doesNotMatch(sentence, /רבעון/, sentence);
});

test("a number is never altered", () => {
  for (const input of [
    "current ratio=2.10 (≥ 2.0)",
    "market cap $1,234,567,890",
    "5yr avg FCF $217,127,200",
    "yield=12.0%",
    "quality 4/5",
    "rank #17",
  ]) {
    const before = input.match(/[\d,.]+/g) ?? [];
    const after = translateCriterion(input, "he").match(/[\d,.]+/g) ?? [];
    assert.deepEqual(after, before, input);
  }
});

test("English is returned untouched when the locale is English", () => {
  for (const input of ["Fast Grower", "positive trailing net income", "Tier A"]) {
    assert.equal(translateCriterion(input, "en"), input);
    assert.equal(translateRationale(input, "en"), input);
    assert.equal(translateExitTrigger(input, "en"), input);
  }
});

test("the scaling-out ladder reads in Hebrew", () => {
  // agents/marks/cycle_value.py has written "trim at {p:.2f}", joined by
  // "; ", since the strategy was built — but only when a memo carries
  // trim_at_price_levels, and none did until 2026-11-06. A format string
  // the table had never covered sat in the code for months and then
  // arrived on a page in English, which is exactly what the journal walk
  // below exists to catch.
  const real = "trim at 95.00; trim at 110.00"; // howard_marks/2026-11-06
  const he = translateExitTrigger(real, "he");
  assert.equal(he, "מכירה חלקית ב-95.00; מכירה חלקית ב-110.00");
  assert.ok(!stillEnglish(he), he);
  // And the prices are untouched.
  assert.deepEqual(he.match(/[\d,.]+/g), ["95.00", "110.00"]);
});

test("the ratio abbreviations are left in Latin", () => {
  for (const input of ["P/E=11.23 (≤ 15.0)", "D/E=0.94 (≤ 1.0)", "ROE=18.2%"]) {
    const he = translateCriterion(input, "he");
    assert.ok(he.includes(input.split("=")[0]), `${input} -> ${he}`);
  }
});

/** Walk the real journal and return, per field, how many strings still
 *  carry English after translation — keyed on the template so one
 *  phrase is reported once rather than seven thousand times. */
function journalCoverage(fields) {
  const journal = join(ROOT, "data/decisions");
  if (!existsSync(journal)) return null;
  const result = {};
  for (const field of fields) result[field] = { rows: 0, left: new Map() };
  for (const agent of readdirSync(journal)) {
    let files;
    try {
      files = readdirSync(join(journal, agent))
        .filter((f) => f.endsWith(".json"))
        .slice(-20);
    } catch {
      continue;
    }
    for (const file of files) {
      let rows;
      try {
        rows = JSON.parse(readFileSync(join(journal, agent, file), "utf8"));
      } catch {
        continue;
      }
      for (const row of rows) {
        for (const field of fields) {
          const raw = row[field];
          const values = typeof raw === "string" ? [raw] : (raw ?? []);
          for (const value of values) {
            const bucket = result[field];
            bucket.rows++;
            const he =
              field === "exit_trigger"
                ? translateExitTrigger(String(value), "he")
                : field === "rationale"
                  ? translateRationale(String(value), "he")
                  : translateCriterion(String(value), "he");
            if (stillEnglish(he)) {
              // Keyed on the number-stripped template so one phrase is
              // reported once rather than seven thousand times, and
              // holding the original, which is what the prose test
              // below has to look at.
              const template = String(value).replace(/-?\$?[\d,]+\.?\d*%?/g, "N");
              const seen = bucket.left.get(template);
              bucket.left.set(template, {
                count: (seen?.count ?? 0) + 1,
                raw: seen?.raw ?? String(value),
              });
            }
          }
        }
      }
    }
  }
  return result;
}

test("every criterion in the live journal comes back in Hebrew", () => {
  // Exactly zero. criteria_met is wholly templated — the agents emit it
  // from a fixed set of formats — so there is nothing here a table
  // cannot reach, and a new template must fail this rather than appear
  // on a page.
  const coverage = journalCoverage(["criteria_met"]);
  if (!coverage) {
    console.log("    (no decision journal in this checkout — skipped)");
    return;
  }
  const { rows, left } = coverage.criteria_met;
  assert.ok(rows > 0, "the journal produced no criteria to check");
  assert.deepEqual(
    [...left.keys()].sort(),
    [],
    `${rows} criteria checked; still English: ${[...left.entries()]
      .map(([t, n]) => `${t} (${n})`)
      .join(", ")}`,
  );
});

/** A string the analyst layer wrote rather than a strategy template.
 *
 *  Claude answers in the first person — "Before I look at any security
 *  I look at the pendulum" — and writes an exit plan as several
 *  semicolon-joined clauses whose wording varies between runs ("while
 *  PEG is above" one day, "and PEG rises above" the next). No strategy
 *  template does either: they are format strings, so they are short,
 *  single-clause, and identical every time.
 */
function isAnalystProse(text) {
  if (/\b(I|me|my|Let|we|our)\b/.test(text)) return true;
  // A comma joins clauses as a semicolon does, and the analyst uses
  // both. Lynch wrote "Not applicable since no position is taken.
  // Revisit only if PEG on verified three-year growth falls to 1.0 or
  // below, with earnings consistency, free cash flow, margins and
  // insider data confirmed from the filing" — 200 characters, no
  // semicolon, and this called it a strategy template.
  //
  // 150 characters stays the line because no template comes near it:
  // the longest of the eight the quantitative strategies write is
  // Graham's 99-character "price reaches the Graham Number; OR P/E or
  // P/B revert above thresholds; OR fundamentals deteriorate".
  if (/[;,]/.test(text) && text.length > 150) return true;
  // A capitalised word opening a clause after a semicolon. Buffett
  // wrote "Not applicable; no position held; Would revisit if a durable
  // moat can be evidenced and a 10-year earnings record verified" — 118
  // characters, first person nowhere, and prose in every other way. No
  // template capitalises mid-string: the eight the strategies write
  // follow a semicolon with "OR", "integrity" or "thesis".
  return /; [A-Z][a-z]/.test(text);
}

test("nothing templated is left in English", () => {
  // The real invariant, and a stronger one than a percentage.
  //
  // `rationale` and `exit_trigger` each hold two kinds of string: the
  // strategies' templates, which the shared table covers completely,
  // and the analyst layer's own prose, which Claude writes in English.
  // A phrase table must not rewrite a sentence — substituting
  // mid-clause produces broken Hebrew — so the fix for that text is to
  // have the analyst answer in Hebrew, which is a change to its prompt
  // and not to this table.
  //
  // So: every string still carrying English after translation has to be
  // prose. A template that slips through is not prose, and fails here
  // rather than appearing on a page. Measured across the whole journal:
  // 316 untranslated, 316 of them prose, 0 templated.
  const coverage = journalCoverage(["rationale", "exit_trigger"]);
  if (!coverage) {
    console.log("    (no decision journal in this checkout — skipped)");
    return;
  }
  const gaps = [];
  for (const field of ["rationale", "exit_trigger"]) {
    for (const [template, { raw }] of coverage[field].left) {
      if (!isAnalystProse(raw)) gaps.push(`${field}: ${template}`);
    }
  }
  assert.deepEqual(gaps, [], `templated strings left in English: ${gaps.slice(0, 4)}`);
});
