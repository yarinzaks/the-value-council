// The breakdown had no tests, and two of its claims were false.
//
// The caveat that says the named amounts are approximate was gated on
// every row being reconstructed, so a single caught-up row silenced it
// for the whole book — Neff was 87 of 94 reconstructed and said
// nothing, and the note reached 3 of the 13 agents.
//
// And the remainder note treated its own sign as decoration. Where the
// ledger sums to more than the book's own closed result, "cannot be
// traced to a stock" is false, the explanation that followed it was
// false, and the percentages were "192% named" with "−92%" underneath.

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildReturnParts,
  namedShare,
  realizedByTicker,
  remainderDirection,
} from "../src/lib/return-breakdown.ts";

/** A book whose identity leaves `realized` at the figure asked for. */
function book({ realized = 1000, positions = [] } = {}) {
  const costBasis = positions.reduce((n, p) => n + p.shares * p.entry_price, 0);
  const invested = positions.reduce(
    (n, p) => n + p.shares * p.current_price,
    0,
  );
  const unrealized = invested - costBasis;
  return {
    initial_cash: 10_000,
    invested,
    positions,
    cumulative_dividends: 0,
    cumulative_costs: 0,
    // NAV = initial + realized + unrealized + dividends - costs
    total_nav: 10_000 + realized + unrealized,
  };
}

function row(ticker, realized_pnl_usd, source = "reconstructed") {
  return { ticker, realized_pnl_usd, source };
}

test("realized is read off the identity, not summed from the ledger", () => {
  const { parts } = buildReturnParts(book({ realized: 1000 }), [
    row("AAA", 400),
  ]);
  assert.equal(Math.round(parts.realized), 1000);
  assert.equal(parts.attributed_total, 400);
  assert.equal(Math.round(parts.unattributed), 600);
});

test("one recorded row does not claim the rest were recorded", () => {
  // The bug, in its smallest form.
  const ledger = [
    row("AAA", 100),
    row("BBB", 100),
    row("CCC", 100, "caught-up"),
  ];
  const { parts } = buildReturnParts(book(), ledger);
  assert.equal(parts.reconstructed_rows, 2);
  assert.equal(parts.ledger_rows, 3);
});

test("a wholly recorded ledger reports nothing to caveat", () => {
  const { parts } = buildReturnParts(book(), [
    row("AAA", 100, "executed"),
    row("BBB", 100, "executed"),
  ]);
  assert.equal(parts.reconstructed_rows, 0);
  assert.equal(parts.ledger_rows, 2);
});

test("an empty ledger reports no rows either way", () => {
  const { parts } = buildReturnParts(book(), []);
  assert.equal(parts.reconstructed_rows, 0);
  assert.equal(parts.ledger_rows, 0);
});

test("the remainder keeps its sign", () => {
  // Positive: closed result with no name on it.
  const under = buildReturnParts(book({ realized: 1000 }), [row("AAA", 400)]);
  assert.ok(under.parts.unattributed > 0, `${under.parts.unattributed}`);

  // Negative: the names sum to more than the book's own figure. This is
  // Buffett — $477.79 realized against $914.98 attributed — and the
  // page said $437 of it "cannot be traced to a stock".
  const over = buildReturnParts(book({ realized: 477.79 }), [
    row("AAA", 914.98),
  ]);
  assert.ok(over.parts.unattributed < 0, `${over.parts.unattributed}`);
  assert.equal(Math.round(over.parts.unattributed * 100) / 100, -437.19);
});

// ---- namedShare ------------------------------------------------------------

test("a share of a gain is a share", () => {
  assert.equal(namedShare({ realized: 1000, attributed_total: 400 }), 0.4);
  assert.equal(namedShare({ realized: 1000, attributed_total: 1000 }), 1);
  assert.equal(namedShare({ realized: 1000, attributed_total: 0 }), 0);
});

test("over-attribution has no percentage answer", () => {
  // 914.98 / 477.79 = 1.915, and the card printed "192% named".
  assert.equal(
    namedShare({ realized: 477.79, attributed_total: 914.98 }),
    null,
  );
});

test("a closed loss has no percentage answer", () => {
  // -45.98 / -101.90 = 0.45, which would print "45% of closed gains
  // named" about a book that closed its trades down.
  assert.equal(
    namedShare({ realized: -101.9, attributed_total: -45.98 }),
    null,
  );
  assert.equal(namedShare({ realized: -100, attributed_total: 50 }), null);
  assert.equal(namedShare({ realized: 0, attributed_total: 0 }), null);
});

test("a negative attribution against a gain has no answer either", () => {
  assert.equal(namedShare({ realized: 1000, attributed_total: -50 }), null);
});

// ---- realizedByTicker ------------------------------------------------------

test("two closes on one name are one contributor", () => {
  const out = realizedByTicker([row("AAA", 100), row("AAA", 50)]);
  assert.deepEqual(out, [{ ticker: "AAA", realized_usd: 150 }]);
});

test("a row with no realized figure is not a contributor", () => {
  assert.deepEqual(realizedByTicker([row("AAA", 0), row("BBB", 25)]), [
    { ticker: "BBB", realized_usd: 25 },
  ]);
});

test("contributors come back biggest first", () => {
  const out = realizedByTicker([
    row("SMALL", 10),
    row("BIG", 500),
    row("LOSS", -200),
  ]);
  assert.deepEqual(
    out.map((c) => c.ticker),
    ["BIG", "SMALL", "LOSS"],
  );
});

// ---- remainderDirection ----------------------------------------------------
//
// The sign of the remainder was taken for its direction, and the sign
// only means "over-attributed" when the book closed a gain. Three of
// the thirteen books closed down, and all three were told the wrong
// story. The figures below are theirs.

test("a gain the names under-cover is untraced", () => {
  // benjamin_graham: $2,886.92 closed, $1,241.00 named.
  assert.equal(
    remainderDirection({
      realized: 2886.92,
      attributed_total: 1241,
      unattributed: 1645.92,
    }),
    "untraced",
  );
});

test("a gain the names overshoot is over-attributed", () => {
  // warren_buffett: $477.79 closed, $914.98 named.
  assert.equal(
    remainderDirection({
      realized: 477.79,
      attributed_total: 914.98,
      unattributed: -437.19,
    }),
    "over",
  );
});

test("an empty ledger cannot overshoot anything", () => {
  // market_core and mohnish_pabrai in the app-support tree: a closed
  // loss and no trade rows at all. The card drew no names and told the
  // reader the names overshot the book by $102.
  assert.equal(
    remainderDirection({
      realized: -101.9,
      attributed_total: 0,
      unattributed: -101.9,
    }),
    "untraced",
  );
  assert.equal(
    remainderDirection({
      realized: -4.92,
      attributed_total: 0,
      unattributed: -4.92,
    }),
    "untraced",
  );
});

test("a loss the names overshoot is over-attributed", () => {
  // walter_schloss: -$12.26 closed, names summing to -$305.83. The
  // card said $294 "cannot be traced to a stock" directly above "62 of
  // the 62 named trades were reconstructed".
  assert.equal(
    remainderDirection({
      realized: -12.26,
      attributed_total: -305.83,
      unattributed: 293.57,
    }),
    "over",
  );
});

test("a loss the names under-cover is untraced", () => {
  // market_core in the repo tree: -$101.90 closed, -$91.96 named.
  assert.equal(
    remainderDirection({
      realized: -101.9,
      attributed_total: -91.96,
      unattributed: -9.94,
    }),
    "untraced",
  );
});

test("names of the wrong sign still answer by magnitude", () => {
  // mohnish_pabrai in the repo tree: the names sum to a gain of $271
  // against a book that closed down $4.92.
  assert.equal(
    remainderDirection({
      realized: -4.92,
      attributed_total: 271.12,
      unattributed: -276.04,
    }),
    "over",
  );
});

test("a remainder under a dollar is not a finding", () => {
  assert.equal(
    remainderDirection({
      realized: 100,
      attributed_total: 99.5,
      unattributed: 0.5,
    }),
    null,
  );
  assert.equal(
    remainderDirection({ realized: 0, attributed_total: 0, unattributed: 0 }),
    null,
  );
});
