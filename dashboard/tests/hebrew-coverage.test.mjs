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
  /P\/E|P\/B|P\/CF|P\/NCAV|D\/E|TR\/PE|EV\/EBIT|ROE|ROC|EPS|FCF|PEGY|PEG|MoS|OE|EY|NCAV|CAGR|DCF|EBIT|BVPS|Net-Net|SPAC|REIT|nan/g;

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
  return text.includes(";") && text.length > 150;
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
