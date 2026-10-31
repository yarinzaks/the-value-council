// The two dictionaries must stay the same shape.
//
// A key present in English and missing in Hebrew does not fail a build
// or a typecheck: `t()` falls back to the key name, so the page renders
// and shows `punch_card` — or, worse, the English, which looks
// deliberate. Three keys had been missing that way, and the only way
// anyone found out was by reading the page.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(join(HERE, "../src/lib/i18n.ts"), "utf8");

function dictionary(name) {
  const match = SOURCE.match(
    new RegExp(`const ${name}: Dict = \\{([\\s\\S]*?)\\n\\};`),
  );
  assert.ok(match, `no ${name} dictionary found`);
  const body = match[1];
  const keys = [...body.matchAll(/^ {2}([A-Za-z_][A-Za-z0-9_]*):/gm)].map(
    (m) => m[1],
  );
  const entries = new Map(
    [
      ...body.matchAll(
        /^ {2}([A-Za-z_][A-Za-z0-9_]*):\s*(?:\n\s+)?((?:"[^"]*"|'[^']*')(?:\s*\+\s*(?:\n\s+)?(?:"[^"]*"|'[^']*'))*)/gm,
      ),
    ].map((m) => [m[1], m[2]]),
  );
  return { keys, entries };
}

const en = dictionary("en");
const he = dictionary("he");

test("every English key has a Hebrew one", () => {
  const missing = en.keys.filter((k) => !he.keys.includes(k));
  assert.deepEqual(missing, [], `missing from Hebrew: ${missing.join(", ")}`);
});

test("no Hebrew key is orphaned", () => {
  const extra = he.keys.filter((k) => !en.keys.includes(k));
  assert.deepEqual(extra, [], `in Hebrew only: ${extra.join(", ")}`);
});

test("no key is declared twice in one dictionary", () => {
  for (const [name, dict] of [["en", en], ["he", he]]) {
    const seen = new Set();
    const duplicates = dict.keys.filter((k) => (seen.has(k) ? true : !seen.add(k)));
    assert.deepEqual(duplicates, [], `${name} declares twice: ${duplicates}`);
  }
});

test("no value is left empty", () => {
  // `col_medal` is a single space on purpose: it heads the medal column,
  // which has no title and must still occupy one. A blank elsewhere is
  // a key someone added and never filled in.
  const BLANK_BY_DESIGN = new Set(["col_medal"]);
  for (const [name, dict] of [["en", en], ["he", he]]) {
    for (const [key, value] of dict.entries) {
      if (BLANK_BY_DESIGN.has(key)) continue;
      assert.ok(value.replace(/["'\s+]/g, "").length > 0, `${name}.${key} is empty`);
    }
  }
});

test("every Hebrew value that should be Hebrew is", () => {
  // The exceptions are symbols and proper nouns a Hebrew reader uses
  // in Latin script, which is why this is an allowlist rather than a
  // rule. Anything new has to be added here deliberately — which is
  // the point: an untranslated string cannot slip in unnoticed.
  const LATIN_BY_DESIGN = new Set([
    "toggle_language", // the toggle shows the *other* language's name
    "col_medal",
    "col_sp",
    "col_sp500",
    "col_alpha_short",
    "ir_label",
    "legend_under_50",
    "legend_over_50",
    "sector_trend_base",
  ]);
  const hebrew = /[֐-׿]/;
  const untranslated = [];
  for (const [key, value] of he.entries) {
    if (LATIN_BY_DESIGN.has(key)) continue;
    if (!hebrew.test(value)) untranslated.push(key);
  }
  assert.deepEqual(untranslated, [], `still English: ${untranslated.join(", ")}`);
});
