// Holdings the runner could not price (audit CE-1a).
//
// LEG was acquired on 2026-08-26 and sat in Klarman's book at its last
// close for 78 days. The runner now writes `unpriced_since` on such a
// position; these are the rules the pages use to show it.

import test from "node:test";
import assert from "node:assert/strict";

import { isUnpriced, unpricedHoldings } from "../src/lib/unpriced.ts";

const pos = (ticker, extra = {}) => ({
  ticker,
  shares: 10,
  entry_price: 10,
  entry_date: "2026-08-03",
  current_price: 9,
  pnl_usd: -10,
  pnl_pct: -10,
  weight_pct: 1,
  why_en: "",
  why_he: "",
  ...extra,
});

test("a flagged position is unpriced and is listed with its value", () => {
  const leg = pos("LEG", { shares: 17, current_price: 9.2, unpriced_since: "2026-08-27" });
  assert.equal(isUnpriced(leg), true);
  assert.deepEqual(unpricedHoldings([pos("KO"), leg]), [
    { ticker: "LEG", since: "2026-08-27", value: 17 * 9.2 },
  ]);
});

test("a book written before the field existed reads as priced", () => {
  const old = pos("KO");
  assert.equal(isUnpriced(old), false);
  assert.deepEqual(unpricedHoldings([old]), []);
});

test("an empty flag is priced, not unpriced", () => {
  assert.equal(isUnpriced(pos("KO", { unpriced_since: "" })), false);
});

test("several unpriced holdings come oldest first", () => {
  const rows = unpricedHoldings([
    pos("NEW", { unpriced_since: "2026-10-01" }),
    pos("OLD", { unpriced_since: "2026-08-27" }),
  ]);
  assert.deepEqual(
    rows.map((r) => r.ticker),
    ["OLD", "NEW"],
  );
});

test("no positions at all is an empty list", () => {
  assert.deepEqual(unpricedHoldings([]), []);
});
