// The assistant has to be able to say why a name is held.
//
// Asked it, the assistant answered that it did not know, and it was
// right to: the context it was handed kept eight fields per position,
// and `target_price` and `exit_trigger` were among the eight it
// dropped. The decision map kept five of the row's thirteen fields and
// dropped the same two. The watchlist map kept ticker and reason and
// dropped `entry_trigger`, which every live entry carries. And the
// export never opened data/memos, where the richest plan in the
// product sits: a valuation per share, a margin of safety, a holding
// period in years, the triggers that would reverse the decision, and a
// thesis the agent wrote in Hebrew itself.
//
// This runs the real export script over a fixture tree shaped like the
// real one, and reads what comes out.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SCRIPT = join(HERE, "..", "scripts", "export-chat-context.mjs");

/** A data tree with one agent, one holding, one watch, one memo. */
function buildTree() {
  const root = mkdtempSync(join(tmpdir(), "vc-ctx-"));
  const write = (rel, data) => {
    const p = join(root, rel);
    mkdirSync(dirname(p), { recursive: true });
    writeFileSync(p, JSON.stringify(data));
  };

  write("portfolios/warren_buffett.json", {
    agent: "warren_buffett",
    total_nav: 11000,
    cash: 1000,
    invested: 10000,
    initial_cash: 10000,
    cumulative_dividends: 0,
    cumulative_costs: 0,
    positions: [
      {
        ticker: "LULU",
        shares: 10,
        entry_price: 100,
        entry_date: "2026-05-08",
        current_price: 107.2,
        pnl_pct: 7.2,
        weight_pct: 50,
        why_en: "wonderful business",
      },
      {
        // No memo and no journal row: the honest answer is no plan.
        ticker: "NOPLAN",
        shares: 1,
        entry_price: 50,
        entry_date: "2026-05-08",
        current_price: 50,
        pnl_pct: 0,
        weight_pct: 50,
        why_en: "x",
      },
    ],
    watchlist: [
      {
        ticker: "CPB",
        why_en: "packaged food",
        entry_trigger: "price falls to the 25% margin-of-safety level",
        entry_price_target: 32.78,
      },
    ],
  });

  // The buy is OLD — outside the 14-day decision window the context
  // keeps. Its plan still has to reach the position, which is the whole
  // reason only 88 of 240 holdings had a row before.
  write("decisions/warren_buffett/2026-05-08.json", [
    {
      ticker: "LULU",
      decision: "BUY",
      timestamp: "2026-05-08T13:00:00+00:00",
      entry_price: 100,
      rationale: "Buffett wonderful business",
      target_price: 346.97,
      exit_trigger: "moat erosion OR extreme overvaluation > 1.5x intrinsic",
    },
  ]);

  write("memos/warren_buffett/2026-10-31.json", [
    {
      agent: "warren_buffett",
      ticker: "LULU",
      schema: "BuffettMemo",
      timestamp: "2026-10-31T13:37:09+00:00",
      model: "m",
      memo: {
        thesis_en: "Lululemon is a branded apparel business I can describe.",
        thesis_he: "לולולמון היא עסק מותג שאני מסוגל להסביר.",
        intrinsic_value_estimate_usd_per_share: 346.97,
        current_price_usd: 107.2,
        margin_of_safety_pct: 69.09,
        expected_holding_period_years: 10,
        exit_triggers: ["brand pricing power eroding", "returns below 15%"],
      },
      input_tokens: 1,
      output_tokens: 1,
      prompt_chars: 1,
      prompt_sha256: "x",
    },
    {
      // An older memo for the same name. The newer one above wins.
      agent: "warren_buffett",
      ticker: "LULU",
      schema: "BuffettMemo",
      timestamp: "2026-06-01T00:00:00+00:00",
      model: "m",
      memo: { thesis_en: "stale", thesis_he: "מיושן" },
      input_tokens: 1,
      output_tokens: 1,
      prompt_chars: 1,
      prompt_sha256: "x",
    },
  ]);

  return root;
}

function exportContext(root) {
  const out = join(root, "ctx.json");
  execFileSync(process.execPath, [SCRIPT, out], {
    env: { ...process.env, VALUE_COUNCIL_DATA_DIR: root },
    stdio: "pipe",
  });
  return JSON.parse(readFileSync(out, "utf8"));
}

let tree;
let ctx;
let held;

test("the fixture exports", () => {
  tree = buildTree();
  ctx = exportContext(tree);
  const book = ctx.portfolios.find((p) => p.agent === "warren_buffett");
  assert.ok(book, "the agent is in the context");
  held = book;
});

test("a holding carries the rule the strategy recorded", () => {
  const lulu = held.positions.find((p) => p.ticker === "LULU");
  assert.equal(
    lulu.plan.exit_rule,
    "moat erosion OR extreme overvaluation > 1.5x intrinsic",
  );
});

test("a plan reaches a holding whose buy is older than the decision window", () => {
  // The context keeps 14 days of decision files and 25 rows per agent.
  // This buy is from May and is not in the `decisions` list at all, so
  // reading the plan off that list would have found nothing.
  assert.equal(held.decisions.length, 1);
  const lulu = held.positions.find((p) => p.ticker === "LULU");
  assert.ok(lulu.plan.exit_rule, "the plan came from the whole journal");
});

test("the valuation is labelled as a valuation, never as a price to pay", () => {
  const lulu = held.positions.find((p) => p.ticker === "LULU");
  assert.equal(lulu.plan.value_target_usd_per_share, 346.97);
  assert.equal(lulu.plan.intrinsic_value_usd_per_share, 346.97);
  assert.equal(lulu.plan.margin_of_safety_pct, 69.09);
  // The names matter: nothing in a plan may read as an entry price.
  for (const key of Object.keys(lulu.plan)) {
    assert.doesNotMatch(key, /entry|waiting|limit|bid/i, `plan.${key}`);
  }
});

test("the holding horizon survives", () => {
  const lulu = held.positions.find((p) => p.ticker === "LULU");
  assert.equal(lulu.plan.means_to_hold, "10 years");
});

test("both theses travel, and the newest memo is the one used", () => {
  const lulu = held.positions.find((p) => p.ticker === "LULU");
  assert.match(lulu.plan.thesis_en, /branded apparel/);
  assert.match(lulu.plan.thesis_he, /לולולמון/);
  assert.doesNotMatch(lulu.plan.thesis_en, /stale/);
  assert.doesNotMatch(lulu.plan.thesis_he, /מיושן/);
});

test("the analyst layer's own trigger list travels", () => {
  const lulu = held.positions.find((p) => p.ticker === "LULU");
  assert.deepEqual(lulu.plan.memo_exit_triggers, [
    "brand pricing power eroding",
    "returns below 15%",
  ]);
});

test("a holding the agent recorded no plan for carries none", () => {
  // 39 of the 240 real holdings. An empty object would read as a plan
  // that says nothing, which invites the assistant to fill it in.
  const bare = held.positions.find((p) => p.ticker === "NOPLAN");
  assert.equal(bare.plan, undefined);
});

test("a watch says what it is waiting for, which a holding never does", () => {
  const cpb = held.watchlist.find((w) => w.ticker === "CPB");
  assert.equal(
    cpb.entry_trigger,
    "price falls to the 25% margin-of-safety level",
  );
  assert.equal(cpb.entry_price_target, 32.78);
});

test("a recent decision keeps the two fields the reader asks about", () => {
  const row = held.decisions[0];
  assert.equal(row.exit_rule, "moat erosion OR extreme overvaluation > 1.5x intrinsic");
  assert.equal(row.value_target_usd_per_share, 346.97);
});

test("the tree is cleaned up", () => {
  rmSync(tree, { recursive: true, force: true });
});

// ---- the prompt has to say the plan is there ------------------------------

test("the system prompt tells the assistant where the plan is", async () => {
  const { SYSTEM } = await import("../../functions/api/chat.js");
  assert.match(SYSTEM, /Why a name is held, and when it would be sold/);
  for (const field of [
    "exit_rule",
    "means_to_hold",
    "thesis_he",
    "memo_exit_triggers",
    "intrinsic_value_usd_per_share",
    "entry_trigger",
  ]) {
    assert.ok(SYSTEM.includes(field), `the prompt names ${field}`);
  }
});

test("the prompt forbids reading a valuation as a price to pay", () => {
  // The mistake the action log made: "waiting for $67.40" about a stock
  // trading at $24.97.
  return import("../../functions/api/chat.js").then(({ SYSTEM }) => {
    assert.match(SYSTEM, /not a price it is waiting to pay/i);
    assert.match(SYSTEM, /[Nn]ever call either one an entry price/);
  });
});

test("the prompt says an absent plan is an answer, not a gap", () => {
  return import("../../functions/api/chat.js").then(({ SYSTEM }) => {
    assert.match(SYSTEM, /say the agent recorded none rather than reasoning one out/i);
    assert.match(SYSTEM, /Magic Formula/);
  });
});
