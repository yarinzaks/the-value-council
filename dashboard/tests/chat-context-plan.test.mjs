// The assistant has to be able to say why a name is held.
//
// Asked it, the assistant said it did not know, and it was right to:
// the context kept eight fields per position and dropped
// `target_price` and `exit_trigger`. The decision map kept five of the
// row's thirteen and dropped the same two. The watchlist map kept
// ticker and reason and dropped `entry_trigger`, which every live entry
// carries — so it could say a name was watched, but not why or for
// what. The system prompt made it worse than a gap: its own inventory
// of the data never mentioned a plan, and its Limits section forbids
// inventing a number.
//
// What is NOT here: anything read from data/memos. Every memo on disk
// records `decision: "WATCH"` and 78 of the 82 matching a holding were
// written after the purchase while the book held the shares, so a
// memo-sourced plan for a held name is false whatever the label. A
// version of this that read them shipped and was reverted.
//
// This runs the real export script over a fixture tree.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SCRIPT = join(HERE, "..", "scripts", "export-chat-context.mjs");

function buildTree() {
  const root = mkdtempSync(join(tmpdir(), "vc-ctx-"));
  const write = (rel, data) => {
    const p = join(root, rel);
    mkdirSync(dirname(p), { recursive: true });
    writeFileSync(p, JSON.stringify(data));
  };
  const pos = (ticker) => ({
    ticker,
    shares: 4,
    entry_price: 100,
    entry_date: "2026-05-08",
    current_price: 107.2,
    pnl_pct: 7.2,
    weight_pct: 25,
    why_en: "x",
  });

  write("portfolios/peter_lynch.json", {
    agent: "peter_lynch",
    total_nav: 11000,
    cash: 1000,
    invested: 10000,
    initial_cash: 10000,
    cumulative_dividends: 0,
    cumulative_costs: 0,
    positions: [pos("KEPT"), pos("DENIED"), pos("NOROW")],
    watchlist: [
      {
        ticker: "CPB",
        why_en: "packaged food",
        entry_trigger: "price falls to the 25% margin-of-safety level",
        entry_price_target: 32.78,
      },
      { ticker: "BARE", why_en: "no trigger recorded" },
    ],
  });

  // Both buys are OLD — outside the 14-day window the context keeps.
  // Their plan still has to reach the position, which is why only 88 of
  // the 240 real holdings had any decision row at all.
  write("decisions/peter_lynch/2026-05-08.json", [
    {
      ticker: "KEPT",
      decision: "BUY",
      timestamp: "2026-05-08T13:00:00+00:00",
      entry_price: 100,
      rationale: "fast grower at a fair price",
      target_price: 346.97,
      exit_trigger: "PEG exceeds 1.5 OR growth falls below 15%",
    },
    {
      ticker: "DENIED",
      decision: "BUY",
      timestamp: "2026-05-08T13:00:00+00:00",
      entry_price: 100,
      rationale: "x",
      exit_trigger:
        "Not applicable until a position is taken; if bought, sell when growth falls below 15%",
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
let book;
const find = (t) => book.positions.find((p) => p.ticker === t);

test("the fixture exports", () => {
  tree = buildTree();
  book = exportContext(tree).portfolios.find((p) => p.agent === "peter_lynch");
  assert.ok(book);
});

test("a holding carries the rule the strategy recorded", () => {
  assert.equal(
    find("KEPT").exit_rule,
    "PEG exceeds 1.5 OR growth falls below 15%",
  );
});

test("a plan reaches a holding whose buy is older than the decision window", () => {
  // The buy is from May and is not in the `decisions` list, so reading
  // the plan off that list would have found nothing.
  assert.ok(book.decisions.length <= 2);
  assert.ok(find("KEPT").exit_rule);
});

test("a rule that denies the position exists is withheld", () => {
  // 30 of the 201 real holdings that carry a rule. peter_lynch/URBN
  // holds 4 shares and its newest BUY row says "Not applicable until a
  // position is taken". Handing that over as the sell plan would have
  // the assistant tell the reader their position is not open.
  assert.equal(find("DENIED").exit_rule, undefined);
});

test("the same sentence survives on the dated decision row", () => {
  // It was true of a WATCH on the day it was written, and `decisions`
  // is a dated record rather than a claim about today.
  const row = book.decisions.find((d) => d.ticker === "DENIED");
  assert.match(row.exit_rule, /Not applicable until a position is taken/);
});

test("a holding with no decision row claims no rule", () => {
  assert.equal(find("NOROW").exit_rule, undefined);
  assert.equal(find("NOROW").intrinsic_value_usd_per_share, undefined);
});

test("the valuation is named as a valuation, never as a price to pay", () => {
  assert.equal(find("KEPT").intrinsic_value_usd_per_share, 346.97);
  for (const key of Object.keys(find("KEPT"))) {
    assert.doesNotMatch(key, /entry_price_target|waiting|limit|bid/i, key);
  }
});

test("a watch says what it is waiting for, which a holding never does", () => {
  const cpb = book.watchlist.find((w) => w.ticker === "CPB");
  assert.equal(
    cpb.entry_trigger,
    "price falls to the 25% margin-of-safety level",
  );
  assert.equal(cpb.entry_price_target, 32.78);
  // And a watch with none claims none.
  const bare = book.watchlist.find((w) => w.ticker === "BARE");
  assert.equal(bare.entry_trigger, undefined);
  assert.equal(bare.entry_price_target, undefined);
});

test("nothing from data/memos reaches the context", () => {
  // The reverted version carried thesis_he, means_to_hold and
  // margin_of_safety_pct off memos that say the agent holds no
  // position. Guarded, not merely omitted.
  const raw = JSON.stringify(book);
  for (const field of [
    "thesis_he",
    "thesis_en",
    "means_to_hold",
    "margin_of_safety_pct",
    "memo_exit_triggers",
  ]) {
    assert.ok(!raw.includes(field), `${field} is back in the context`);
  }
});

test("the tree is cleaned up", () => {
  rmSync(tree, { recursive: true, force: true });
});

// ---- the prompt has to match what the context carries --------------------

const system = async () => (await import("../../functions/api/chat.js")).SYSTEM;

test("the prompt tells the assistant where the plan is", async () => {
  const SYSTEM = await system();
  assert.match(SYSTEM, /Why a name is held, and when it would be sold/);
  for (const field of [
    "exit_rule",
    "intrinsic_value_usd_per_share",
    "entry_trigger",
    "entry_price_target",
  ]) {
    assert.ok(SYSTEM.includes(field), `the prompt names ${field}`);
  }
});

test("the prompt names no field the context does not carry", async () => {
  const SYSTEM = await system();
  for (const gone of [
    "thesis_he",
    "means_to_hold",
    "memo_exit_triggers",
    "margin_of_safety_pct",
  ]) {
    assert.ok(!SYSTEM.includes(gone), `the prompt still promises ${gone}`);
  }
});

test("the prompt forbids reading a valuation as a price to pay", async () => {
  const SYSTEM = await system();
  assert.match(SYSTEM, /not a price it is waiting to pay/i);
  assert.match(SYSTEM, /[Nn]ever call it an entry price/);
});

test("the prompt says an absent plan is an answer, not a gap", async () => {
  const SYSTEM = await system();
  assert.match(SYSTEM, /Say the agent recorded no rule/i);
  assert.match(SYSTEM, /Do not reason one out of the criteria/i);
  assert.match(SYSTEM, /Magic Formula/);
});

test("the prompt forbids filling the gap from a dated decision row", async () => {
  // The 30 withheld rules are still in `decisions`, where they belong.
  const SYSTEM = await system();
  assert.match(SYSTEM, /do not use a .*decisions.* row/i);
});

test("the prompt does not claim a holding horizon the data lacks", async () => {
  const SYSTEM = await system();
  assert.match(SYSTEM, /Nothing in the data states a holding horizon/);
});
