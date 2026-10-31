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
  memoDigest,
  memoEntries,
  memoFieldLabel,
  memoRest,
  memoValue,
} from "../src/lib/translate-dynamic.ts";

/** The digest helpers, so each test reads like the others. */
function require_digest() {
  return { memoDigest, memoRest };
}

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

// --------------------------------------------------- the digest

test("the digest leads with the verdict, the thesis and three figures", () => {
  const { memoDigest } = require_digest();
  const memo = {
    ticker: "GIS",
    decision: "WATCH",
    thesis_en: "English",
    thesis_he: "תזה בעברית",
    margin_of_safety_pct: 32,
    intrinsic_value_estimate_usd_per_share: 72.42,
    confidence: 0.45,
    moat_analysis: "long prose nobody reads first",
  };
  const d = memoDigest(memo, "he");
  assert.equal(d.decision, "WATCH");
  assert.equal(d.thesis, "תזה בעברית");
  assert.deepEqual(d.figures.map(([k]) => k), [
    "margin_of_safety_pct",
    "intrinsic_value_estimate_usd_per_share",
    "confidence",
  ]);
});

test("a valuation of zero is not shown as a figure", () => {
  // 72 of 120 memos carry `intrinsic_value_estimate_usd` as zero: the
  // snapshot did not give the analyst what a valuation needs. "$0" as
  // the headline reads as "worth nothing", which is a much stronger
  // claim than the memo is making.
  const { memoDigest } = require_digest();
  const d = memoDigest(
    {
      decision: "WATCH",
      margin_of_safety_pct: 0,
      intrinsic_value_estimate_usd: 0,
      peg_ratio: 1.4,
      confidence: 0.3,
    },
    "he",
  );
  const keys = d.figures.map(([k]) => k);
  assert.ok(!keys.includes("intrinsic_value_estimate_usd"), keys.join(","));
  assert.ok(!keys.includes("margin_of_safety_pct"), keys.join(","));
  // It falls through to the next candidate in the same group.
  assert.ok(keys.includes("peg_ratio"), keys.join(","));
});

test("a score of zero IS shown — it is a verdict", () => {
  // Fisher scoring a company 0 out of 15 is a judgement, and six memos
  // say exactly that.
  const { memoDigest } = require_digest();
  const d = memoDigest({ decision: "REJECT", total_score_out_of_15: 0 }, "he");
  assert.deepEqual(d.figures, [["total_score_out_of_15", 0]]);
});

test("what the digest showed is not repeated in the fold", () => {
  const { memoDigest, memoRest } = require_digest();
  const memo = {
    ticker: "GIS",
    decision: "WATCH",
    thesis_en: "English",
    thesis_he: "עברית",
    confidence: 0.45,
    moat_analysis: "prose",
  };
  const shown = new Set([
    "decision",
    ...memoDigest(memo, "he").figures.map(([k]) => k),
    "thesis_he",
    "thesis_en",
  ]);
  for (const [key] of memoRest(memo, "he")) {
    assert.ok(!shown.has(key), `${key} appears twice`);
  }
  // And nothing is lost: the fold still carries the rest.
  assert.deepEqual(memoRest(memo, "he").map(([k]) => k), ["ticker", "moat_analysis"]);
});

test("a memo with no thesis and no figures still has a verdict", () => {
  const { memoDigest } = require_digest();
  const d = memoDigest({ decision: "REJECT" }, "he");
  assert.equal(d.decision, "REJECT");
  assert.equal(d.thesis, null);
  assert.deepEqual(d.figures, []);
});

test("every live memo produces a digest with something in it", () => {
  const { memoDigest } = require_digest();
  const dir = join(ROOT, "data/memos");
  if (!existsSync(dir)) {
    console.log("    (no memos in this checkout — skipped)");
    return;
  }
  let checked = 0;
  let thin = 0;
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
        const d = memoDigest(memo, "he");
        if (!d.decision && !d.thesis && d.figures.length === 0) thin++;
      }
    }
  }
  assert.ok(checked > 100, `only ${checked} memos checked`);
  assert.equal(thin, 0, `${thin} memos would render as an empty card`);
});
