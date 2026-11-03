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

import { buildPositionStories, movements } from "../src/lib/positions.ts";

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

test("an execution on a day already held is not a change of view", () => {
  // BUY is the intent, FILL the execution of that same intent. Both
  // are buy-kinds, so the transition between them used to render as
  // "Changed its view" — twice, on the same date, which is also two
  // list items sharing a React key.
  const s = storyFor([
    row("2026-09-03", "BUY"),
    row("2026-09-04", "BUY"),
    row("2026-09-04", "FILL"),
  ]);
  const m = movements(s);
  assert.equal(
    m.filter((x) => x.kind === "changed").length,
    0,
    `nothing changed: ${JSON.stringify(m)}`,
  );
});

test("a real change of view is still reported", () => {
  // BUY -> WATCH is the agent stepping back, and must survive.
  const s = storyFor([row("2026-09-03", "BUY"), row("2026-09-04", "WATCH")]);
  assert.equal(movements(s).filter((x) => x.kind === "changed").length, 1);
});

test("movements never repeat a date and kind", () => {
  const s = storyFor([
    row("2026-09-03", "BUY"),
    row("2026-09-04", "BUY"),
    row("2026-09-04", "FILL"),
    row("2026-09-05", "ADD"),
  ]);
  const keys = movements(s).map((m) => `${m.date}-${m.kind}`);
  assert.equal(new Set(keys).size, keys.length, keys.join(", "));
});

test("a top-up is its own event, not silence and not a change of view", () => {
  // ADD is the rebalance buying more of a name already held. It is a
  // real trade — it moved cash and shares — so it belongs on the
  // timeline, but it neither opens the position nor reconsiders it.
  const s = storyFor([
    row("2026-09-03", "BUY"),
    row("2026-11-02", "ADD"),
  ]);
  const m = movements(s);
  assert.equal(m.filter((x) => x.kind === "added").length, 1, JSON.stringify(m));
  assert.equal(m.filter((x) => x.kind === "changed").length, 0);
});

test("a trim is its own event, and does not close the position", () => {
  const s = storyFor([
    row("2026-09-03", "BUY"),
    row("2026-11-02", "TRIM"),
  ]);
  const m = movements(s);
  assert.equal(m.filter((x) => x.kind === "trimmed").length, 1, JSON.stringify(m));
  assert.equal(m.filter((x) => x.kind === "exited").length, 0, "a trim is not an exit");
  assert.equal(m.filter((x) => x.kind === "changed").length, 0);
});

test("a trim does not reset the unbroken run", () => {
  // The position was never closed, so the run continues.
  const s = storyFor([
    row("2026-09-03", "BUY"),
    row("2026-09-04", "BUY"),
    row("2026-11-02", "TRIM"),
    row("2026-11-03", "BUY"),
  ]);
  assert.equal(s.daysHeldUnbroken, 3);
});

test("a buy after a trim is not a re-entry", () => {
  const s = storyFor([
    row("2026-09-03", "BUY"),
    row("2026-11-02", "TRIM"),
    row("2026-11-03", "BUY"),
  ]);
  assert.equal(movements(s).filter((x) => x.kind === "reopened").length, 0);
});

// The oldest row is where the story starts, and the card used to call
// it "Opened the position" whatever it said. A name is screened and
// watched before it is ever bought, so on the live store that claim
// was false for 565 of 1,057 stories — including 406 cards that said
// "never opened" and "Opened the position" at once.

test("a story that starts on a watch row says so", () => {
  const s = storyFor([row("2026-09-03", "WATCH")], []);
  const m = movements(s);
  assert.equal(m.at(-1).kind, "watched", JSON.stringify(m));
  assert.equal(
    m.filter((x) => x.kind === "opened").length,
    0,
    "nothing was opened",
  );
});

test("the first purchase after a watch is an opening, not a re-entry", () => {
  // "Bought back in" is a claim about the past. Holding started out
  // false on a watch row, so the first real buy fell into the re-entry
  // branch and 158 first purchases read "נקנתה מחדש".
  const s = storyFor([row("2026-09-03", "WATCH"), row("2026-09-04", "BUY")]);
  const m = movements(s);
  assert.deepEqual(
    m.map((x) => x.kind),
    ["opened", "watched"], // newest first
    JSON.stringify(m),
  );
});

test("a watch stretch before the buy collapses to one row", () => {
  const s = storyFor([
    row("2026-09-03", "WATCH"),
    row("2026-09-04", "WATCH"),
    row("2026-09-07", "WATCH"),
    row("2026-09-08", "FILL"),
  ]);
  assert.deepEqual(
    movements(s).map((x) => x.kind),
    ["opened", "watched"],
  );
});

test("a genuine re-entry is still called one", () => {
  // The branch has to keep working: held, sold, bought again.
  const s = storyFor([
    row("2026-09-03", "BUY"),
    row("2026-09-04", "SELL"),
    row("2026-09-07", "BUY"),
  ]);
  assert.deepEqual(
    movements(s).map((x) => x.kind),
    ["reopened", "exited", "opened"],
  );
});

test("a story that starts on a purchase is unchanged", () => {
  const s = storyFor([row("2026-09-03", "BUY"), row("2026-09-04", "BUY")]);
  assert.deepEqual(
    movements(s).map((x) => x.kind),
    ["opened"],
  );
});

test("a story whose window opens on a trim does not call it an opening", () => {
  // The journal is missing 561 purchases, so a story can begin at a
  // rebalance on a name bought before the window. It was held — the
  // trim proves it — and a later buy is a re-entry, not a first one.
  const s = storyFor([
    row("2026-09-03", "TRIM"),
    row("2026-09-04", "SELL"),
    row("2026-09-07", "BUY"),
  ]);
  assert.deepEqual(
    movements(s).map((x) => x.kind),
    ["reopened", "exited", "trimmed"],
  );
});

test("a story whose window opens on a sale does not call the next buy an opening", () => {
  // The same case as the trim above, with the stronger proof. Every
  // SELL and EXIT row in the book says "Closed live position: entry
  // $X, exit $Y" — the name was held, whatever the window shows.
  const s = storyFor([row("2026-09-03", "SELL"), row("2026-09-07", "BUY")]);
  assert.deepEqual(
    movements(s).map((x) => x.kind),
    ["reopened", "exited"],
  );
});

test("an exit is proof of ownership too", () => {
  const s = storyFor([row("2026-09-03", "EXIT"), row("2026-09-07", "BUY")]);
  assert.deepEqual(
    movements(s).map((x) => x.kind),
    ["reopened", "exited"],
  );
});
