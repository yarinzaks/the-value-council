// The positions an agent used to hold, from the ledger it sold them in.

import test from "node:test";
import assert from "node:assert/strict";

import { buildClosedPositions, soldTickers } from "../src/lib/past-positions.ts";

const sell = (ticker, date, price, extra = {}) => ({
  date,
  ticker,
  side: "SELL",
  shares: 10,
  price,
  realized_pnl_usd: 0,
  source: "executed",
  ...extra,
});

const pts = (...pairs) => pairs.map(([d, c]) => ({ d, c }));

const SPY = pts(
  ["2026-05-06", 100],
  ["2026-06-01", 110],
  ["2026-08-07", 120],
  ["2026-11-12", 126],
);

test("a sale with its entry becomes a closed position with both windows measured", () => {
  const ledger = [sell("CRI", "2026-08-07", 40, { entry_date: "2026-05-06", entry_price: 32 })];
  const series = {
    CRI: pts(["2026-05-06", 32], ["2026-08-07", 40], ["2026-11-12", 50]),
    SPY,
  };
  const [row] = buildClosedPositions(ledger, [], series);
  assert.equal(row.key, "CRI@2026-08-07");
  assert.equal(row.heldPct, 25);
  assert.equal(row.heldDays, 93);
  assert.equal(row.sinceExitPct, 25);
  assert.equal(row.sinceExitDate, "2026-11-12");
  assert.equal(row.spyHeldPct, 20);
  assert.equal(row.spySinceExitPct, 5);
  assert.equal(row.reconstructed, false);
});

test("a trim of a line still held is not a closing", () => {
  const ledger = [sell("HOG", "2026-07-01", 30, { entry_date: "2026-05-06", entry_price: 26 })];
  const open = [{ ticker: "HOG", entry_date: "2026-05-06" }];
  assert.deepEqual(buildClosedPositions(ledger, open, {}), []);
});

test("a name sold and bought back later is a closing, not a trim", () => {
  const ledger = [sell("HOG", "2026-07-01", 30, { entry_date: "2026-05-06", entry_price: 26 })];
  const open = [{ ticker: "HOG", entry_date: "2026-09-01" }];
  const [row] = buildClosedPositions(ledger, open, {});
  assert.equal(row.exitDate, "2026-07-01");
});

test("two sales of one line fold into one closing at the weighted exit", () => {
  const ledger = [
    sell("X", "2026-08-07", 10, { shares: 30, entry_date: "2026-05-06", entry_price: 8 }),
    sell("X", "2026-08-10", 12, { shares: 10, entry_date: "2026-05-06", entry_price: 8 }),
  ];
  const [row] = buildClosedPositions(ledger, [], {});
  assert.equal(row.shares, 40);
  assert.equal(row.exitPrice, 10.5);
  assert.equal(row.exitDate, "2026-08-10");
  assert.equal(row.heldPct, 31.25);
});

test("a sale without entry fields is listed with its entry unknown, not guessed", () => {
  // Every row written before 2026-11-13 looks like this.
  const ledger = [sell("RIGL", "2026-05-06", 28.09, { source: "reconstructed" })];
  const [row] = buildClosedPositions(ledger, [], { SPY });
  assert.equal(row.entryDate, null);
  assert.equal(row.entryPrice, null);
  assert.equal(row.heldPct, null);
  assert.equal(row.heldDays, null);
  assert.equal(row.spyHeldPct, null);
  assert.equal(row.reconstructed, true);
});

test("a series that stops at the exit says nothing about afterwards", () => {
  const ledger = [sell("GONE", "2026-08-07", 40, { entry_date: "2026-05-06", entry_price: 32 })];
  const series = { GONE: pts(["2026-05-06", 32], ["2026-08-07", 40]), SPY };
  const [row] = buildClosedPositions(ledger, [], series);
  assert.equal(row.sinceExitPct, null);
  assert.equal(row.sinceExitDate, null);
  assert.equal(row.spySinceExitPct, null);
  // And with no series at all.
  const [bare] = buildClosedPositions(ledger, [], {});
  assert.equal(bare.sinceExitPct, null);
});

test("a buy, a zero price and a zero quantity are ignored", () => {
  const ledger = [
    { ...sell("A", "2026-08-07", 10), side: "BUY" },
    sell("B", "2026-08-07", 0),
    sell("C", "2026-08-07", 10, { shares: 0 }),
    sell("D", "2026-08-07", NaN),
  ];
  assert.deepEqual(buildClosedPositions(ledger, [], {}), []);
});

test("newest exit first, ticker as the tie-break", () => {
  const ledger = [
    sell("B", "2026-06-01", 10, { entry_date: "2026-05-06", entry_price: 9 }),
    sell("A", "2026-06-01", 10, { entry_date: "2026-05-06", entry_price: 9 }),
    sell("C", "2026-08-07", 10, { entry_date: "2026-05-06", entry_price: 9 }),
  ];
  assert.deepEqual(
    buildClosedPositions(ledger, [], {}).map((r) => r.key),
    ["C@2026-08-07", "A@2026-06-01", "B@2026-06-01"],
  );
});

test("soldTickers is every name the ledger sold, once, sorted", () => {
  const ledger = [sell("b", "2026-06-01", 1), sell("A", "2026-06-02", 1), sell("B", "2026-06-03", 1),
    { ...sell("Z", "2026-06-03", 1), side: "BUY" }];
  assert.deepEqual(soldTickers(ledger), ["A", "B"]);
});

test("the inputs are not mutated", () => {
  const ledger = [sell("X", "2026-08-07", 10, { entry_date: "2026-05-06", entry_price: 8 })];
  const before = JSON.stringify(ledger);
  buildClosedPositions(ledger, [], {});
  assert.equal(JSON.stringify(ledger), before);
});
