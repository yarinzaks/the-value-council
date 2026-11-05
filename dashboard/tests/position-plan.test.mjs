// Reading an agent's own plan off its memo.
//
// Every surface answered "why is this held and when will it be sold"
// with numbers. The answer was on disk the whole time: 82 of the 240
// held positions have a memo, each with a thesis the agent wrote in
// Hebrew itself, and for most of them a horizon and a valuation.
//
// The five memo models do not agree with each other, so the shapes
// below are copied from real records in the live tree rather than
// invented — the fixtures are the five dialects this has to read.

import test from "node:test";
import assert from "node:assert/strict";

import {
  latestMemoFor,
  planFor,
  planFromMemo,
} from "../src/lib/position-plan.ts";

// ---- the five real dialects -----------------------------------------------

/** warren_buffett/NEU, 2026-10-31. The only schema that says per-share. */
const BUFFETT = {
  thesis_en: "A packaged-food company with a 30.6% ROE.",
  thesis_he: "חברת מזון ארוז עם תשואה על ההון של 30.6%.",
  intrinsic_value_estimate_usd_per_share: 1271.73,
  current_price_usd: 783.66,
  margin_of_safety_pct: 38.38,
  expected_holding_period_years: 10,
  exit_triggers: ["moat erosion", "management failure"],
};

/** philip_fisher/META. The horizon is a STRING range, not a number. */
const FISHER = {
  thesis_en: "I will not buy on a screen alone.",
  thesis_he: "אני לא קונה על סמך מסנן כמותי בלבד.",
  intrinsic_value_estimate_usd: 742.1,
  current_price_usd: 610.0,
  expected_holding_period_years: "10+",
  reverse_triggers: ["integrity question", "R&D decline"],
};

/** seth_klarman/YELP. Months, and a scaling-out plan beside the list. */
const KLARMAN = {
  thesis_en: "I start with what could go wrong.",
  thesis_he: "אני מתחיל במה שעלול להשתבש.",
  intrinsic_value_estimate_usd: 64.87,
  current_price_usd: 19.09,
  margin_of_safety_pct: 70.57,
  expected_holding_period_months: 36,
  reverse_triggers: ["free cash flow below the 5-year average"],
  scaling_out_plan: { trim_at_price_levels: [45.5, 58.25] },
};

/** howard_marks/CALM. No valuation at all, by doctrine. */
const MARKS = {
  thesis_en: "I start with the pendulum.",
  thesis_he: "אני מתחיל מהמטוטלת.",
  expected_holding_period_months: 24,
  scaling_out_plan: { trim_at_price_levels: [] },
};

/** peter_lynch/URBN. The intrinsic value IS the price, to the cent —
 *  one of seven such memos. Not an estimate the agent reached. */
const LYNCH_NO_ESTIMATE = {
  thesis_en: "A P/E of 6.83 against 17.07 for the industry.",
  thesis_he: "מכפיל רווח של 6.83 מול 17.07 בתעשייה.",
  intrinsic_value_estimate_usd: 90.72,
  current_price_usd: 90.72,
  margin_of_safety_pct: 0.0,
  expected_holding_period_months: 18,
  exit_triggers: ["growth falls below 15%", "PEG exceeds 1.5"],
};

// ---- the thesis ----------------------------------------------------------

test("the Hebrew reader gets the Hebrew the agent wrote", () => {
  const plan = planFromMemo(BUFFETT, "he");
  assert.equal(plan.thesis, "חברת מזון ארוז עם תשואה על ההון של 30.6%.");
  assert.equal(plan.thesisIsNative, true);
});

test("the English reader gets the English", () => {
  const plan = planFromMemo(BUFFETT, "en");
  assert.equal(plan.thesis, "A packaged-food company with a 30.6% ROE.");
  assert.equal(plan.thesisIsNative, true);
});

test("a memo with only one language says which it gave", () => {
  const plan = planFromMemo({ ...BUFFETT, thesis_he: "" }, "he");
  assert.equal(plan.thesis, "A packaged-food company with a 30.6% ROE.");
  assert.equal(plan.thesisIsNative, false);
});

// ---- the horizon, in three shapes ----------------------------------------

test("Buffett's horizon is a number of years", () => {
  assert.deepEqual(planFromMemo(BUFFETT, "he").horizon, {
    kind: "years",
    years: 10,
  });
});

test("Fisher's horizon is a range and stays one", () => {
  // "10+" is not 10. Reading it as a number would invent precision,
  // and Number("10+") is NaN, which would read as absent.
  assert.deepEqual(planFromMemo(FISHER, "he").horizon, {
    kind: "range",
    range: "10+",
  });
});

test("Klarman's horizon is months", () => {
  assert.deepEqual(planFromMemo(KLARMAN, "he").horizon, {
    kind: "months",
    months: 36,
  });
});

test("a zero horizon is no horizon", () => {
  // Marks and Lynch write 0 where the model left it unanswered.
  assert.equal(
    planFromMemo({ thesis_he: "x", expected_holding_period_months: 0 }, "he")
      .horizon,
    null,
  );
});

// ---- the valuation -------------------------------------------------------

test("the per-share value is read under either field name", () => {
  assert.equal(planFromMemo(BUFFETT, "he").intrinsicValue, 1271.73);
  assert.equal(planFromMemo(FISHER, "he").intrinsicValue, 742.1);
  assert.equal(planFromMemo(KLARMAN, "he").intrinsicValue, 64.87);
});

test("a value equal to the price is the price, not an estimate", () => {
  // Seven of Lynch's thirty memos. Printing $90.72 as what the share
  // is "worth" would restate the market back as a valuation.
  const plan = planFromMemo(LYNCH_NO_ESTIMATE, "he");
  assert.equal(plan.intrinsicValue, null);
  assert.equal(plan.marginOfSafetyPct, 0);
});

test("a book that values nothing claims no value", () => {
  assert.equal(planFromMemo(MARKS, "he").intrinsicValue, null);
  assert.equal(planFromMemo(MARKS, "he").marginOfSafetyPct, null);
});

// ---- the exit triggers, from three field names ---------------------------

test("exit triggers are collected under every name the models use", () => {
  assert.deepEqual(planFromMemo(BUFFETT, "he").exitTriggers, [
    "moat erosion",
    "management failure",
  ]);
  assert.deepEqual(planFromMemo(FISHER, "he").exitTriggers, [
    "integrity question",
    "R&D decline",
  ]);
  assert.deepEqual(planFromMemo(KLARMAN, "he").exitTriggers, [
    "free cash flow below the 5-year average",
    "trim at 45.50",
    "trim at 58.25",
  ]);
});

test("an empty scaling-out plan adds nothing", () => {
  assert.deepEqual(planFromMemo(MARKS, "he").exitTriggers, []);
});

test("a malformed memo is read as holding nothing, not as a crash", () => {
  assert.equal(planFromMemo({}, "he"), null);
  assert.equal(
    planFromMemo({ margin_of_safety_pct: 12.5 }, "he"),
    null,
    "a margin of safety alone is a number with no claim attached",
  );
  const junk = planFromMemo(
    {
      thesis_he: "ok",
      exit_triggers: ["real", "", 42, null],
      scaling_out_plan: { trim_at_price_levels: ["x", 10] },
      expected_holding_period_years: {},
      intrinsic_value_estimate_usd: "nope",
    },
    "he",
  );
  assert.deepEqual(junk.exitTriggers, ["real", "trim at 10.00"]);
  assert.equal(junk.horizon, null);
  assert.equal(junk.intrinsicValue, null);
});

// ---- picking the memo ----------------------------------------------------

const rec = (agent, ticker, timestamp, memo) => ({
  agent,
  ticker,
  schema: "X",
  timestamp,
  model: "m",
  memo,
  input_tokens: null,
  output_tokens: null,
  prompt_chars: null,
  prompt_sha256: null,
});

test("the newest memo for the name wins, whatever the list order", () => {
  const memos = [
    rec("warren_buffett", "NEU", "2026-09-01T00:00:00Z", { thesis_he: "old" }),
    rec("warren_buffett", "NEU", "2026-10-31T00:00:00Z", { thesis_he: "new" }),
    rec("warren_buffett", "BBY", "2026-11-01T00:00:00Z", { thesis_he: "other" }),
    rec("seth_klarman", "NEU", "2026-11-02T00:00:00Z", { thesis_he: "wrong agent" }),
  ];
  assert.equal(latestMemoFor(memos, "warren_buffett", "NEU").memo.thesis_he, "new");
  assert.equal(planFor(memos, "warren_buffett", "NEU", "he").thesis, "new");
});

test("a name the analyst layer never reached has no plan", () => {
  // 158 of the 240 held positions.
  assert.equal(planFor([], "john_neff", "VICI", "he"), null);
  const memos = [rec("john_neff", "OTHER", "2026-11-01T00:00:00Z", BUFFETT)];
  assert.equal(planFor(memos, "john_neff", "VICI", "he"), null);
});

// ---- the language of the trigger list ------------------------------------

test("an English trigger list on a Hebrew page is declared as one", () => {
  // All five memo models write the trigger lists in English only, and
  // each sentence is written fresh per company — 71 distinct strings
  // across 71 held positions, so no phrase table reaches them. The
  // page says whose words they are instead of letting them pass as
  // its own.
  const he = planFromMemo(BUFFETT, "he");
  assert.deepEqual(he.exitTriggers, ["moat erosion", "management failure"]);
  assert.equal(he.exitTriggersAreNative, false);

  const en = planFromMemo(BUFFETT, "en");
  assert.equal(en.exitTriggersAreNative, true);
});

test("a Hebrew trigger list, once the analyst layer writes one, is used", () => {
  // No agent fills these fields today. Reading them now means the day
  // one does, this page needs no second change.
  const plan = planFromMemo(
    {
      ...BUFFETT,
      exit_triggers_he: ["שחיקת החפיר", "כשל הנהלה"],
    },
    "he",
  );
  assert.deepEqual(plan.exitTriggers, ["שחיקת החפיר", "כשל הנהלה"]);
  assert.equal(plan.exitTriggersAreNative, true);
});

test("the Hebrew list is ignored for an English reader", () => {
  const plan = planFromMemo(
    { ...BUFFETT, exit_triggers_he: ["שחיקת החפיר"] },
    "en",
  );
  assert.deepEqual(plan.exitTriggers, ["moat erosion", "management failure"]);
  assert.equal(plan.exitTriggersAreNative, true);
});

test("no triggers at all is not a language problem", () => {
  const plan = planFromMemo(MARKS, "he");
  assert.deepEqual(plan.exitTriggers, []);
  assert.equal(plan.exitTriggersAreNative, false);
});
