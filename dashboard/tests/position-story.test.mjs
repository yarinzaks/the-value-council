// "Held since, unchanged, through N trading days" has to mean it.
//
// The line was given `daysAffirmed`, which is the count of distinct
// days carrying a BUY-kind decision across the whole retained journal,
// with nothing subtracted when the agent sells. For a name bought and
// sold repeatedly it is a sum over disjoint stretches, so the card
// claimed Benjamin Graham had held INGR "unchanged through 83 trading
// days" directly above a timeline of six sales and six repurchases.
// The real unbroken run was 42.
//
// daysAffirmed is kept, because the headline it feeds — how many days
// the agent has said yes to this name — is a true statement about the
// whole history. The held-unchanged line needs the current run.

import test from "node:test";
import assert from "node:assert/strict";

import { buildPositionStories } from "../src/lib/positions.ts";

const AGENT = "benjamin_graham";

function row(day, decision, ticker = "INGR") {
  return {
    ticker,
    decision,
    agent: AGENT,
    timestamp: `${day}T00:00:00+00:00`,
    criteria_met: [],
    criteria_failed: [],
    criteria_values: {},
    rationale: null,
    entry_price: 100,
    exit_trigger: null,
  };
}

function position(ticker = "INGR", entry_date = "2026-09-03") {
  return {
    ticker,
    entry_date,
    entry_price: 100,
    current_price: 100,
    pnl_pct: 0,
    weight_pct: 10,
    shares: 10,
  };
}

function storyFor(rows, positions = [position()]) {
  const stories = buildPositionStories(AGENT, rows, positions);
  return stories.find((s) => s.ticker === "INGR");
}

test("an unbroken holding counts every affirmed day", () => {
  const s = storyFor([
    row("2026-09-03", "BUY"),
    row("2026-09-04", "BUY"),
    row("2026-09-07", "BUY"),
  ]);
  assert.equal(s.daysAffirmed, 3);
  assert.equal(s.daysHeldUnbroken, 3);
});

test("a sale resets the unbroken run but not the affirmed total", () => {
  const s = storyFor([
    row("2026-05-19", "BUY"),
    row("2026-05-20", "BUY"),
    row("2026-05-21", "BUY"),
    row("2026-05-22", "SELL"),
    row("2026-09-03", "BUY"),
    row("2026-09-04", "BUY"),
  ]);
  assert.equal(s.daysAffirmed, 5, "all five yes-days still count");
  assert.equal(s.daysHeldUnbroken, 2, "only the run since the sale");
});

test("EXIT resets the run exactly as SELL does", () => {
  const s = storyFor([
    row("2026-05-19", "BUY"),
    row("2026-05-20", "BUY"),
    row("2026-05-22", "EXIT"),
    row("2026-09-03", "BUY"),
  ]);
  assert.equal(s.daysHeldUnbroken, 1);
});

test("several sales leave only the run since the last one", () => {
  const s = storyFor([
    row("2026-05-19", "BUY"),
    row("2026-05-22", "SELL"),
    row("2026-06-02", "BUY"),
    row("2026-06-10", "SELL"),
    row("2026-07-01", "BUY"),
    row("2026-07-02", "BUY"),
    row("2026-07-03", "BUY"),
  ]);
  assert.equal(s.daysAffirmed, 5);
  assert.equal(s.daysHeldUnbroken, 3);
});

test("a FILL on a day already affirmed does not count twice", () => {
  const s = storyFor([
    row("2026-09-03", "BUY"),
    row("2026-09-04", "BUY"),
    row("2026-09-04", "FILL"),
  ]);
  assert.equal(s.daysHeldUnbroken, 2);
});

test("a never-opened name has no unbroken run", () => {
  const s = storyFor([row("2026-09-03", "WATCH")], []);
  assert.equal(s.lifecycle, "never_opened");
  assert.equal(s.daysHeldUnbroken, 0);
});

test("a sale after the last buy leaves the run at zero", () => {
  const s = storyFor(
    [row("2026-09-03", "BUY"), row("2026-09-04", "SELL")],
    [],
  );
  assert.equal(s.lifecycle, "closed");
  assert.equal(s.daysHeldUnbroken, 0);
});
