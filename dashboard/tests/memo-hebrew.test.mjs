// The brain page showed the analyst's memos as a raw JSON dump.
//
// Labels came from `key.replace(/_/g, " ")`, so all 123 fields a memo
// can carry were titled in English — "thesis en", "moat analysis",
// "fifteen points score". Values were printed as written. And where the
// analyst produced both languages, which it does for `thesis`, the page
// showed the English *and* the Hebrew side by side: the translation was
// already in the file and the reader was shown both.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  memoEntries,
  memoFieldLabel,
  memoValue,
} from "../src/lib/translate-dynamic.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "../..");
const BUNDLE = JSON.parse(
  readFileSync(join(ROOT, "data_bundled/hebrew.json"), "utf8"),
);

/** Every field path the real memos contain, nested ones dotted. */
function liveFieldPaths() {
  const dir = join(ROOT, "data/memos");
  if (!existsSync(dir)) return null;
  const paths = new Set();
  for (const agent of readdirSync(dir)) {
    let files;
    try {
      files = readdirSync(join(dir, agent)).filter((f) => f.endsWith(".json"));
    } catch {
      continue;
    }
    for (const file of files) {
      let rows;
      try {
        rows = JSON.parse(readFileSync(join(dir, agent, file), "utf8"));
      } catch {
        continue;
      }
      for (const row of Array.isArray(rows) ? rows : [rows]) {
        const memo = row?.memo;
        if (!memo || typeof memo !== "object") continue;
        for (const [k, v] of Object.entries(memo)) {
          paths.add(k);
          if (v && typeof v === "object" && !Array.isArray(v)) {
            for (const k2 of Object.keys(v)) paths.add(`${k}.${k2}`);
          }
        }
      }
    }
  }
  return paths;
}

// ------------------------------------------------------------- labels

test("every field the live memos carry has a Hebrew label", () => {
  const paths = liveFieldPaths();
  if (!paths) {
    console.log("    (no memos in this checkout — skipped)");
    return;
  }
  assert.ok(paths.size > 50, `only ${paths.size} field paths found`);
  const unlabelled = [...paths].filter(
    (p) => memoFieldLabel(p, "he") === p.split(".").pop().replace(/_/g, " "),
  );
  assert.deepEqual(
    unlabelled.sort(),
    [],
    `no Hebrew label: ${unlabelled.slice(0, 6).join(", ")}`,
  );
});

test("a nested field is labelled by its own path", () => {
  // `full_exit_price` under the exit plan is not whatever
  // `full_exit_price` might mean elsewhere.
  assert.equal(
    memoFieldLabel("scaling_out_plan.full_exit_price", "he"),
    "מחיר היציאה המלאה",
  );
  assert.equal(
    memoFieldLabel("fifteen_points_score.point_7_labor_relations", "he"),
    "7. יחסי עבודה",
  );
});

test("an unknown field is readable rather than blank", () => {
  // A schema a new agent introduces. English beats nothing, and the
  // test above names it so it does not stay English.
  assert.equal(memoFieldLabel("some_new_field", "he"), "some new field");
  assert.equal(memoFieldLabel("a.b_c", "he"), "b c");
});

test("English keeps the English field names", () => {
  assert.equal(memoFieldLabel("moat_analysis", "en"), "moat analysis");
  assert.equal(memoFieldLabel("scaling_out_plan.full_exit_price", "en"), "full exit price");
});

// ------------------------------------------------------------- values

test("the analyst's verdicts read in Hebrew", () => {
  const cases = {
    WATCH: "מעקב",
    UNCLEAR: "לא ברור",
    INSIDE: "בתוך המעגל",
    Brand: "מותג",
    "Owner Earnings DCF": "DCF על רווחי בעלים",
    "Phase 2 (Quality + Moats)": "שלב 2 (איכות וחומות)",
    "Mid-cycle": "אמצע המחזור",
    positive_stable: "חיובי ויציב",
  };
  for (const [english, hebrew] of Object.entries(cases)) {
    assert.equal(memoValue(english, "he"), hebrew, english);
  }
});

test("a value is matched whole, never as a substring", () => {
  // The trap this map exists to avoid. "Fair" is in it; "Fairfax" must
  // come through untouched, and so must a ticker that happens to be an
  // English word.
  assert.equal(memoValue("Fair", "he"), "סביר");
  assert.equal(memoValue("Fairfax Financial", "he"), "Fairfax Financial");
  assert.equal(memoValue("stable", "he"), "יציב");
  assert.equal(memoValue("unstable", "he"), "unstable");
});

test("a ticker is never translated", () => {
  // CALM is Cal-Maine Foods; YELP, LULU and MLI are likewise names.
  for (const ticker of ["CALM", "YELP", "LULU", "MLI", "GIS", "PIPR"]) {
    assert.equal(memoValue(ticker, "he"), ticker, ticker);
  }
});

test("an investor's own quotation stays in his own words", () => {
  // Fisher's playbook says so outright: "all quotes from Fisher's
  // writings remain in original English to preserve accuracy."
  const quote = "You can't predict. You can prepare.";
  assert.equal(memoValue(quote, "he"), quote);
});

test("English returns the value untouched", () => {
  for (const v of ["WATCH", "Brand", "Mid-cycle"]) {
    assert.equal(memoValue(v, "en"), v);
  }
});

// --------------------------------------------------- the two languages

test("where the analyst wrote both, the reader sees one", () => {
  const memo = { ticker: "GIS", thesis_en: "English thesis", thesis_he: "תזה בעברית" };
  const he = memoEntries(memo, "he");
  assert.deepEqual(he.map(([k]) => k), ["ticker", "thesis_he"]);
  const en = memoEntries(memo, "en");
  assert.deepEqual(en.map(([k]) => k), ["ticker", "thesis_en"]);
});

test("a lone language field is kept whichever one it is", () => {
  // Most prose fields have no twin — making them bilingual is a change
  // to five playbooks and a re-run, not something this can do. Dropping
  // them would show the reader less rather than more.
  assert.deepEqual(
    memoEntries({ thesis_en: "only English" }, "he").map(([k]) => k),
    ["thesis_en"],
  );
  assert.deepEqual(
    memoEntries({ moat_analysis: "English prose" }, "he").map(([k]) => k),
    ["moat_analysis"],
  );
});

test("the live memos never show both languages at once", () => {
  const dir = join(ROOT, "data/memos");
  if (!existsSync(dir)) {
    console.log("    (no memos in this checkout — skipped)");
    return;
  }
  let checked = 0;
  for (const agent of readdirSync(dir)) {
    let files;
    try {
      files = readdirSync(join(dir, agent)).filter((f) => f.endsWith(".json"));
    } catch {
      continue;
    }
    for (const file of files) {
      let rows;
      try {
        rows = JSON.parse(readFileSync(join(dir, agent, file), "utf8"));
      } catch {
        continue;
      }
      for (const row of Array.isArray(rows) ? rows : [rows]) {
        const memo = row?.memo;
        if (!memo || typeof memo !== "object") continue;
        checked++;
        const keys = memoEntries(memo, "he").map(([k]) => k);
        const pairs = keys.filter(
          (k) => k.endsWith("_en") && keys.includes(`${k.slice(0, -3)}_he`),
        );
        assert.deepEqual(pairs, [], `${agent}/${file} shows both: ${pairs}`);
      }
    }
  }
  assert.ok(checked > 0, "no memos were checked");
});

test("the two memo maps are well formed", () => {
  for (const [name, map] of [
    ["memo_fields", BUNDLE.memo_fields],
    ["memo_values", BUNDLE.memo_values],
  ]) {
    assert.ok(Object.keys(map).length > 20, `${name} is suspiciously small`);
    for (const [key, value] of Object.entries(map)) {
      assert.ok(value.trim(), `${name}.${key} is empty`);
      assert.notEqual(value, key, `${name}.${key} translates to itself`);
      // Latin-only is allowed where the term is Latin by convention —
      // "P/E", "PEG", "DCF על רווחי בעלים" has Hebrew; "P/E" does not.
      if (!/[֐-׿]/.test(value)) {
        assert.ok(
          /^[A-Z/+()\s-]+$/.test(value),
          `${name}.${key} = ${value} is neither Hebrew nor an abbreviation`,
        );
      }
    }
  }
});
