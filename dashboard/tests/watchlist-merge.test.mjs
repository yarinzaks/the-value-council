// The watchlist page reads two sources, and the merge is where they
// can disagree.
//
// A WATCH row in the decision journal is the tail of a ranking. An
// entry in `portfolio.watchlist` is the newer statement — rewritten
// whole on every run, and the only one that carries the price the agent
// is waiting for. The page read the journal alone, which left
// market_core's 28 names and mohnish_pabrai's 30 stored and
// unviewable, and would have left Graham's new list the same way.
//
// Four agents write to both, so the risk the other way is double
// counting: this page ranks by how many agents agree on a name, and an
// agent present in both sources must still count once.
//
// Imports the TypeScript module directly, as agents-registry does.
// `watchlist-merge.ts` is a leaf with no runtime imports for exactly
// this reason — the loader it would otherwise live in reads the
// filesystem, and the tie-break below decides what the page puts at
// the top, which a filesystem is a poor place to test from.

import test from "node:test";
import assert from "node:assert/strict";

import { mergeWatchSignals } from "../src/lib/watchlist-merge.ts";

const journal = (slug, ticker, when, rationale = "near miss") => ({
  slug,
  display_name: slug.replace(/_/g, " "),
  ticker,
  rationale,
  when,
  entry_price_target: null,
  live: false,
});

const portfolio = (slug, ticker, when, target = null) => ({
  slug,
  display_name: slug.replace(/_/g, " "),
  ticker,
  rationale: "blocked only on the quotation",
  when,
  entry_price_target: target,
  live: true,
});

// ------------------------------------------------- both sources arrive

test("a name only in a portfolio watchlist still reaches the page", () => {
  const entries = mergeWatchSignals([portfolio("market_core", "MRK", "2026-10-29")]);

  assert.equal(entries.length, 1);
  assert.equal(entries[0].ticker, "MRK");
  assert.equal(entries[0].agent_count, 1);
});

test("a name only in the journal still reaches the page", () => {
  const entries = mergeWatchSignals([journal("peter_lynch", "GEF", "2026-10-20T00:00:00+00:00")]);

  assert.equal(entries[0].ticker, "GEF");
});

test("two agents on one name from two different sources agree on it", () => {
  const entries = mergeWatchSignals([
    journal("peter_lynch", "GEF", "2026-10-20T00:00:00+00:00"),
    portfolio("market_core", "GEF", "2026-10-29"),
  ]);

  assert.equal(entries.length, 1);
  assert.equal(entries[0].agent_count, 2);
});

// --------------------------------------------------- one agent, once

test("an agent in both sources for one name counts once", () => {
  const entries = mergeWatchSignals([
    journal("david_dreman", "LNC", "2026-10-28T00:00:00+00:00"),
    portfolio("david_dreman", "LNC", "2026-10-29"),
  ]);

  assert.equal(entries[0].agent_count, 1, "the agent was counted twice");
  assert.equal(entries[0].agents.length, 1);
});

test("the newer of an agent's two signals is the one shown", () => {
  const entries = mergeWatchSignals([
    journal("david_dreman", "LNC", "2026-10-28T00:00:00+00:00"),
    portfolio("david_dreman", "LNC", "2026-10-29", 41.75),
  ]);

  assert.equal(entries[0].agents[0].entry_price_target, 41.75);
});

test("on the same day the live list wins, because it is rewritten every run", () => {
  // A journal timestamp is "2026-10-29T00:00:00+00:00" and a portfolio
  // date is "2026-10-29". Compared as whole strings the longer one
  // sorts later, which would have handed the day to the journal row and
  // dropped the price with it.
  const entries = mergeWatchSignals([
    journal("david_dreman", "LNC", "2026-10-29T00:00:00+00:00"),
    portfolio("david_dreman", "LNC", "2026-10-29", 41.75),
  ]);

  assert.equal(entries[0].agents[0].entry_price_target, 41.75);
});

test("the live list does not override a later journal row", () => {
  const entries = mergeWatchSignals([
    portfolio("david_dreman", "LNC", "2026-10-20", 41.75),
    journal("david_dreman", "LNC", "2026-10-29T00:00:00+00:00"),
  ]);

  assert.equal(entries[0].agents[0].entry_price_target, null);
});

// ------------------------------------------------------------ ordering

test("the names the most agents agree on come first", () => {
  const entries = mergeWatchSignals([
    portfolio("market_core", "ONE", "2026-10-29"),
    portfolio("market_core", "THREE", "2026-10-29"),
    portfolio("mohnish_pabrai", "THREE", "2026-10-29"),
    journal("peter_lynch", "THREE", "2026-10-28T00:00:00+00:00"),
  ]);

  assert.equal(entries[0].ticker, "THREE");
  assert.equal(entries[0].agent_count, 3);
  assert.equal(entries[1].ticker, "ONE");
});

test("a signal with no ticker is dropped rather than keyed on nothing", () => {
  assert.deepEqual(mergeWatchSignals([portfolio("market_core", "", "2026-10-29")]), []);
});

test("every agent carries a display name", () => {
  const entries = mergeWatchSignals([portfolio("benjamin_graham", "SFD", "2026-10-29", 23.1)]);

  assert.ok(entries[0].agents[0].display_name);
  assert.notEqual(entries[0].agents[0].display_name, "");
});
