// Rebasing has to survive the data it will actually meet.
//
// Snapshots and price files do not share a calendar: the runner wrote
// rows on four 2026 holidays the schedule mistook for sessions, and a
// name bought last week has no closes at the start of a 1M window.
// Every test here is one of those shapes.

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildCompareRows,
  drawableKeys,
  rebase,
} from "../src/lib/compare-series.ts";

const DATES = ["2026-10-01", "2026-10-02", "2026-10-05"];

test("the first value in the window is the zero point", () => {
  const out = rebase(DATES, [
    { d: "2026-10-01", c: 100 },
    { d: "2026-10-02", c: 110 },
    { d: "2026-10-05", c: 90 },
  ]);
  assert.deepEqual(out, [0, 10, -10]);
});

test("a missing day carries the last close forward", () => {
  // 10-02 has no close — a holiday the runner still wrote a row for.
  const out = rebase(DATES, [
    { d: "2026-10-01", c: 100 },
    { d: "2026-10-05", c: 120 },
  ]);
  assert.deepEqual(out, [0, 0, 20]);
});

test("a series that starts mid-window is null before it starts", () => {
  const out = rebase(DATES, [
    { d: "2026-10-02", c: 50 },
    { d: "2026-10-05", c: 75 },
  ]);
  assert.deepEqual(out, [null, 0, 50]);
});

test("a close before the window sets the base", () => {
  // The year-wide file holds days the 1M window does not show. The
  // base is the last close at or before the first visible date, so
  // the line does not pretend to start flat.
  const out = rebase(DATES, [
    { d: "2026-09-28", c: 200 },
    { d: "2026-10-02", c: 220 },
  ]);
  assert.deepEqual(out, [0, 10, 10]);
});

test("unsorted points are handled", () => {
  const out = rebase(DATES, [
    { d: "2026-10-05", c: 90 },
    { d: "2026-10-01", c: 100 },
    { d: "2026-10-02", c: 110 },
  ]);
  assert.deepEqual(out, [0, 10, -10]);
});

test("a zero close never becomes the base", () => {
  // Would make every later point Infinity.
  const out = rebase(DATES, [
    { d: "2026-10-01", c: 0 },
    { d: "2026-10-02", c: 50 },
    { d: "2026-10-05", c: 75 },
  ]);
  assert.deepEqual(out, [null, 0, 50]);
});

test("no dates yields no values", () => {
  assert.deepEqual(rebase([], [{ d: "2026-10-01", c: 100 }]), []);
});

test("no points yields all null", () => {
  assert.deepEqual(rebase(DATES, []), [null, null, null]);
});

test("rows carry one key per drawable series", () => {
  const rows = buildCompareRows(
    DATES,
    {
      PORTFOLIO: [
        { d: "2026-10-01", c: 10000 },
        { d: "2026-10-05", c: 11000 },
      ],
      SPY: [
        { d: "2026-10-01", c: 600 },
        { d: "2026-10-05", c: 606 },
      ],
    },
    ["PORTFOLIO", "SPY"],
  );

  assert.deepEqual(rows[0], { date: "2026-10-01", PORTFOLIO: 0, SPY: 0 });
  assert.equal(rows[2].PORTFOLIO, 10);
  assert.equal(rows[2].SPY, 1);
});

test("a selected series with no file is left out, not drawn flat", () => {
  // An absent line and a line that did not move must not look alike.
  const rows = buildCompareRows(DATES, { SPY: [{ d: "2026-10-01", c: 600 }] }, [
    "SPY",
    "QQQ",
  ]);
  assert.ok(!("QQQ" in rows[0]), JSON.stringify(rows[0]));
  assert.deepEqual(drawableKeys(DATES, { SPY: [{ d: "2026-10-01", c: 600 }] }, ["SPY", "QQQ"]), [
    "SPY",
  ]);
});

test("a series with points only outside the window is left out", () => {
  // This test used to assert the opposite, on the reasoning that a
  // close before the window is a valid base and the resulting flat
  // line is "the truth about a name whose file stops". It is not: a
  // flat line says the share did not move, and a stopped feed says
  // nothing at all. The review that caught it found eight holdings
  // drawn this way on the live store.
  const series = { OLD: [{ d: "2025-01-01", c: 10 }] };
  assert.deepEqual(drawableKeys(DATES, series, ["OLD"]), []);

  const future = { NEW: [{ d: "2027-01-01", c: 10 }] };
  assert.deepEqual(drawableKeys(DATES, future, ["NEW"]), []);
});

test("selection order is preserved", () => {
  const series = {
    A: [{ d: "2026-10-01", c: 1 }],
    B: [{ d: "2026-10-01", c: 1 }],
  };
  assert.deepEqual(drawableKeys(DATES, series, ["B", "A"]), ["B", "A"]);
});

test("every row has the date, even with nothing selected", () => {
  const rows = buildCompareRows(DATES, {}, []);
  assert.deepEqual(rows, [
    { date: "2026-10-01" },
    { date: "2026-10-02" },
    { date: "2026-10-05" },
  ]);
});

test("a feed that stopped before the window does not draw a flat line", () => {
  // The worst failure this chart can have: a dead feed and a share
  // that did not move look identical. Carrying a close forward is
  // right across a weekend and wrong across two months.
  const out = rebase(DATES, [{ d: "2026-08-17", c: 56.87 }]);
  assert.deepEqual(out, [null, null, null]);
});

test("a close is carried across a long weekend", () => {
  // 10-01 is a Thursday here; a Friday close must still cover Monday.
  const out = rebase(
    ["2026-10-01", "2026-10-02", "2026-10-05"],
    [{ d: "2026-10-01", c: 100 }],
  );
  assert.deepEqual(out, [0, 0, 0]);
});

test("a feed that dies mid-window stops rather than flatlining", () => {
  const dates = ["2026-10-01", "2026-10-05", "2026-10-20", "2026-11-02"];
  const out = rebase(dates, [
    { d: "2026-10-01", c: 100 },
    { d: "2026-10-05", c: 110 },
  ]);
  assert.deepEqual(out.slice(0, 2), [0, 10]);
  assert.equal(out[2], null, "15 days past the last close is not a price");
  assert.equal(out[3], null);
});

test("the staleness bound is adjustable", () => {
  const dates = ["2026-10-01", "2026-10-20"];
  assert.deepEqual(rebase(dates, [{ d: "2026-10-01", c: 100 }], 30), [0, 0]);
  assert.deepEqual(rebase(dates, [{ d: "2026-10-01", c: 100 }], 7), [0, null]);
});

test("a wholly stale series is not drawable", () => {
  const series = { DEAD: [{ d: "2026-08-17", c: 56.87 }] };
  assert.deepEqual(drawableKeys(DATES, series, ["DEAD"]), []);
  const rows = buildCompareRows(DATES, series, ["DEAD"]);
  assert.ok(!("DEAD" in rows[0]));
});
