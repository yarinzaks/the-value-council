// What an agent's action log must show, and in what order.
//
// The page had a flat list of its last ten verdicts, which answers
// "what does it think now". The log answers "when did this happen, and
// what happened before it" — a name is noticed, waited for, bought,
// trimmed, sold, and those five events had never sat in one order.

import test from "node:test";
import assert from "node:assert/strict";

import { buildActionLog, byDay } from "../src/lib/action-log.ts";

const trade = (date, ticker, side, shares, price, realized = null) => ({
  date,
  ticker,
  side,
  shares,
  price,
  realized_pnl_usd: realized,
});

const decision = (timestamp, ticker, verdict, extra = {}) => ({
  ticker,
  decision: verdict,
  timestamp,
  ...extra,
});

test("an opening purchase is a buy", () => {
  const log = buildActionLog([trade("2026-08-10", "HOG", "BUY", 61, 25.95)], []);
  assert.equal(log.length, 1);
  assert.equal(log[0].kind, "buy");
  assert.equal(log[0].shares, 61);
  assert.equal(log[0].price, 25.95);
  assert.equal(log[0].realized, null, "a purchase realises nothing");
});

test("a second purchase of a name already held is an add", () => {
  const log = buildActionLog(
    [
      trade("2026-08-10", "HOG", "BUY", 61, 25.95),
      trade("2026-09-01", "HOG", "BUY", 12, 28.10),
    ],
    [],
  );
  // Newest first.
  assert.deepEqual(log.map((a) => a.kind), ["add", "buy"]);
});

test("selling part of a line is a trim, all of it is a sale", () => {
  const log = buildActionLog(
    [
      trade("2026-08-10", "HOG", "BUY", 61, 25.95),
      trade("2026-09-01", "HOG", "SELL", 12, 31.22, 63.24),
      trade("2026-10-01", "HOG", "SELL", 49, 33.00, 345.45),
    ],
    [],
  );
  assert.deepEqual(log.map((a) => a.kind), ["sell", "trim", "buy"]);
  // Without the running position a trim is indistinguishable from a
  // sale, and the page would report the line closed twice.
  assert.equal(log[1].shares, 12);
  assert.equal(log[1].realized, 63.24);
});

test("a realised loss keeps its sign", () => {
  const log = buildActionLog(
    [
      trade("2026-08-10", "X", "BUY", 10, 50),
      trade("2026-09-10", "X", "SELL", 10, 40, -100),
    ],
    [],
  );
  assert.equal(log[0].realized, -100);
});

test("a name joining the watchlist is one event, not thirty", () => {
  // The journal writes a WATCH row on every run the name stays listed.
  // On a six-week wait that is thirty identical rows; only the first is
  // an event.
  const rows = [];
  for (let d = 1; d <= 30; d++) {
    const day = String(d).padStart(2, "0");
    rows.push(decision(`2026-09-${day}T00:00:00+00:00`, "UVV", "WATCH"));
  }
  const log = buildActionLog([], rows);
  assert.equal(log.length, 1);
  assert.equal(log[0].kind, "watch");
  assert.equal(log[0].date, "2026-09-01", "the first day, not the last");
});

test("a name leaving the watchlist is reported, and may rejoin", () => {
  const log = buildActionLog([], [
    decision("2026-09-01T00:00:00+00:00", "UVV", "WATCH"),
    decision("2026-09-20T00:00:00+00:00", "UVV", "REJECT"),
    decision("2026-10-05T00:00:00+00:00", "UVV", "WATCH"),
  ]);
  assert.deepEqual(log.map((a) => a.kind), ["watch", "unwatch", "watch"]);
  assert.deepEqual(log.map((a) => a.date), ["2026-10-05", "2026-09-20", "2026-09-01"]);
});

test("a rejection of a name never watched is not an event", () => {
  const log = buildActionLog([], [
    decision("2026-09-01T00:00:00+00:00", "NOPE", "REJECT"),
  ]);
  assert.deepEqual(log, []);
});

test("the watch carries the price it is waiting for, and its reason", () => {
  const log = buildActionLog([], [
    decision("2026-09-01T00:00:00+00:00", "UVV", "WATCH", {
      target_price: 42,
      rationale: "P/B 1.42 above the 1.20 Graham requires",
    }),
  ]);
  assert.equal(log[0].price, 42);
  assert.match(log[0].why, /Graham requires/);
});

test("the whole arc of one name reads in order", () => {
  // The question the log exists to answer.
  const log = buildActionLog(
    [
      trade("2026-08-20", "HOG", "BUY", 61, 25.95),
      trade("2026-10-30", "HOG", "SELL", 61, 31.22, 321.47),
    ],
    [
      decision("2026-07-01T00:00:00+00:00", "HOG", "WATCH", { target_price: 26 }),
      decision("2026-07-15T00:00:00+00:00", "HOG", "WATCH"),
    ],
  );
  assert.deepEqual(
    log.map((a) => [a.date, a.kind]),
    [
      ["2026-10-30", "sell"],
      ["2026-08-20", "buy"],
      ["2026-07-01", "watch"],
    ],
  );
});

test("events landing on one day read in the order they happened", () => {
  // Sorting by date alone put a sale above the purchase that preceded
  // it, which is the one thing a log must not do.
  const log = buildActionLog(
    [
      trade("2026-09-01", "A", "BUY", 10, 10),
      trade("2026-09-01", "B", "BUY", 10, 10),
      trade("2026-09-01", "B", "SELL", 10, 12, 20),
    ],
    [decision("2026-09-01T00:00:00+00:00", "C", "WATCH")],
  );
  assert.deepEqual(log.map((a) => a.kind), ["sell", "buy", "buy", "watch"]);
});

test("the cap keeps the newest, whichever source they came from", () => {
  const trades = [];
  for (let d = 1; d <= 20; d++) {
    trades.push(trade(`2026-09-${String(d).padStart(2, "0")}`, `T${d}`, "BUY", 1, 1));
  }
  const log = buildActionLog(trades, [], { limit: 3 });
  assert.equal(log.length, 3);
  assert.deepEqual(log.map((a) => a.date), ["2026-09-20", "2026-09-19", "2026-09-18"]);
});

test("a row with no ticker is dropped rather than keyed on nothing", () => {
  const log = buildActionLog(
    [trade("2026-09-01", "", "BUY", 1, 1)],
    [decision("2026-09-01T00:00:00+00:00", "", "WATCH")],
  );
  assert.deepEqual(log, []);
});

test("an empty history is an empty log, not a crash", () => {
  assert.deepEqual(buildActionLog([], []), []);
  assert.deepEqual(byDay([]), []);
});

test("the log groups into days in the same order", () => {
  const log = buildActionLog(
    [
      trade("2026-09-01", "A", "BUY", 1, 1),
      trade("2026-09-02", "B", "BUY", 1, 1),
      trade("2026-09-02", "C", "BUY", 1, 1),
    ],
    [],
  );
  const days = byDay(log);
  assert.deepEqual(days.map(([d, rows]) => [d, rows.length]), [
    ["2026-09-02", 2],
    ["2026-09-01", 1],
  ]);
});

test("the input arrays are not mutated", () => {
  // They come from a loader that caches them.
  const trades = [
    trade("2026-09-02", "B", "BUY", 1, 1),
    trade("2026-09-01", "A", "BUY", 1, 1),
  ];
  const before = JSON.stringify(trades);
  buildActionLog(trades, []);
  assert.equal(JSON.stringify(trades), before);
});
