// The live ranking over a chosen window.
//
// The overview ranked by return since seed only. These pin the window
// math that lets the same table rank a week, a month, the stretch since
// the August fix, and everything.

import test from "node:test";
import assert from "node:assert/strict";

import {
  FIX_DATE,
  RANKING_PERIODS,
  rankOne,
  rankOverWindow,
  windowStart,
} from "../src/lib/ranking-window.ts";

const snap = (date, nav) => ({
  agent: "x",
  date,
  nav,
  cash: 0,
  invested: nav,
  pnl_usd: nav - 10000,
  pnl_pct: (nav / 10000 - 1) * 100,
  position_count: 1,
  watchlist_count: 0,
  buys: [],
  sells: [],
  trade_count: 0,
});

// Mon 2026-11-02 .. Thu 2026-11-12, with the weekend gap.
const SERIES = [
  snap("2026-11-02", 10000),
  snap("2026-11-03", 10100),
  snap("2026-11-04", 10200),
  snap("2026-11-05", 10150),
  snap("2026-11-06", 10300),
  snap("2026-11-09", 10400),
  snap("2026-11-10", 10350),
  snap("2026-11-11", 10500),
  snap("2026-11-12", 10600),
];

test("a week opens on the last session on or before seven days back", () => {
  // 2026-11-12 minus 7 is Thursday 2026-11-05, which has a snapshot.
  const r = rankOne("x", SERIES, "1W");
  assert.equal(r.startDate, "2026-11-05");
  assert.equal(r.endDate, "2026-11-12");
  assert.equal(r.startNav, 10150);
  assert.equal(r.pnlUsd, 450);
  assert.ok(Math.abs(r.returnPct - (450 / 10150) * 100) < 1e-9);
  assert.equal(r.sessions, 6);
  assert.equal(r.clipped, false);
});

test("a window landing on a weekend opens on the Friday before it", () => {
  // From Wed 2026-11-11, seven days back is Wed 2026-11-04 — present.
  // From Mon 2026-11-09, seven days back is Mon 2026-11-02 — present.
  // Cut the series so the window start falls on Sat 2026-11-07.
  const toSat = SERIES.filter((s) => s.date <= "2026-11-12" && s.date !== "2026-11-06");
  // 11-12 minus 7 = 11-05, present; so instead test the explicit rule:
  assert.equal(windowStart("1W", "2026-11-14"), "2026-11-07");
  const r = rankOne("x", [...toSat, snap("2026-11-13", 10700), snap("2026-11-14", 10800)], "1W");
  // 11-07 is a Saturday with no snapshot; the last on or before it is 11-05.
  assert.equal(r.startDate, "2026-11-05");
});

test("ALL opens on the first snapshot; FIX on the fix date's last prior session", () => {
  assert.equal(windowStart("ALL", "2026-11-12"), null);
  assert.equal(windowStart("FIX", "2026-11-12"), FIX_DATE);
  const all = rankOne("x", SERIES, "ALL");
  assert.equal(all.startDate, "2026-11-02");
  assert.equal(all.sessions, SERIES.length);
  assert.equal(all.clipped, false);
});

test("ALL measures from the seed, so it agrees with the book's own return", () => {
  // Neff's first snapshot is already a day of market: $10,004 against
  // a $10,000 seed. The overview said +2.90% while his page said +2.94%.
  const late = [snap("2026-05-07", 10004), snap("2026-05-08", 10294)];
  const r = rankOne("x", late, "ALL", 10000);
  assert.equal(r.startNav, 10000);
  assert.equal(r.pnlUsd, 294);
  assert.ok(Math.abs(r.returnPct - 2.94) < 1e-9);
  // Only ALL: a week is measured between its two snapshots.
  assert.equal(rankOne("x", late, "1W", 10000).startNav, 10004);
  // A seed that is not a number falls back to the first snapshot.
  assert.equal(rankOne("x", late, "ALL", NaN).startNav, 10004);
  assert.equal(rankOne("x", late, "ALL", 0).startNav, 10004);
});

test("a book younger than the window is ranked from its first day, and flagged", () => {
  const young = SERIES.slice(-3);
  const r = rankOne("x", young, "3M");
  assert.equal(r.clipped, true);
  assert.equal(r.startDate, "2026-11-10");
  assert.equal(r.sessions, 3);
  // ALL is never clipped: there is nothing older to want.
  assert.equal(rankOne("x", young, "ALL").clipped, false);
});

test("the ranking sorts best first and drops the control book", () => {
  const by = new Map([
    ["benjamin_graham", SERIES],
    ["walter_schloss", SERIES.map((s) => ({ ...s, nav: 20000 - s.nav }))],
    ["benjamin_graham_2", SERIES],
  ]);
  const rows = rankOverWindow(by, "1W", (a) => a === "benjamin_graham_2");
  assert.deepEqual(
    rows.map((r) => r.agent),
    ["benjamin_graham", "walter_schloss"],
  );
  assert.ok(rows[0].returnPct > rows[1].returnPct);
});

test("a book with no snapshots is left out, not ranked at zero", () => {
  const by = new Map([
    ["benjamin_graham", SERIES],
    ["peter_lynch", []],
  ]);
  assert.deepEqual(
    rankOverWindow(by, "ALL").map((r) => r.agent),
    ["benjamin_graham"],
  );
});

test("a snapshot with a broken NAV cannot open or close a window", () => {
  const broken = [...SERIES, snap("2026-11-13", 0), snap("2026-11-16", NaN)];
  const r = rankOne("x", broken, "1W");
  assert.equal(r.endDate, "2026-11-12");
});

test("every period the pills offer is handled", () => {
  for (const p of RANKING_PERIODS) {
    assert.ok(rankOne("x", SERIES, p), p);
  }
});

test("the window never changes the input order", () => {
  const copy = [...SERIES].reverse();
  const before = copy.map((s) => s.date);
  rankOne("x", copy, "1M");
  assert.deepEqual(
    copy.map((s) => s.date),
    before,
  );
});
