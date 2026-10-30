// The agent registry, and the one rule that decides what a ranking may
// contain.
//
// Imports the TypeScript module directly — Node strips the types, and
// `agents.ts` has no runtime imports of its own (its only import is
// `import type`, which is erased), so nothing needs building first.
//
// Why these tests exist: the live overview ranks books by return since
// seed and prints medals against the order. A book opened later places
// by its start date under a heading that says it placed by its method,
// which reads as a verdict on the method. The registry's `control` flag
// is what prevents that, and a flag nothing checks is a flag that stops
// working silently.

import test from "node:test";
import assert from "node:assert/strict";

import {
  AGENTS,
  CONTROL_SLUGS,
  isControl,
  metaFor,
  rankByReturn,
} from "../src/lib/agents.ts";

const FIRST = "benjamin_graham";
const SECOND = "benjamin_graham_2";

// ------------------------------------------------------------ the registry

test("both Grahams are registered, with distinct slugs", () => {
  assert.ok(metaFor(FIRST), "the first Graham is missing from the registry");
  assert.ok(metaFor(SECOND), "the second Graham is missing from the registry");
  assert.notEqual(metaFor(FIRST).slug, metaFor(SECOND).slug);
});

test("every slug in the registry is unique", () => {
  const slugs = AGENTS.map((a) => a.slug);
  assert.equal(new Set(slugs).size, slugs.length, "duplicate slug in AGENTS");
});

test("every agent carries both languages", () => {
  for (const a of AGENTS) {
    for (const field of [
      "display_name",
      "display_name_he",
      "description",
      "description_he",
      "school",
      "school_he",
      "color",
    ]) {
      assert.ok(
        typeof a[field] === "string" && a[field].length > 0,
        `${a.slug} is missing ${field}`,
      );
    }
  }
});

test("the two Grahams read as a pair but are told apart", () => {
  const first = metaFor(FIRST);
  const second = metaFor(SECOND);
  // Same school: it is the same method.
  assert.equal(second.school, first.school);
  assert.equal(second.school_he, first.school_he);
  // Different name and colour: a reader must not confuse the two books.
  assert.notEqual(second.display_name, first.display_name);
  assert.notEqual(second.display_name_he, first.display_name_he);
  assert.notEqual(second.color, first.color);
});

// ------------------------------------------------------------- the control

test("the second Graham is flagged as a control; the first is not", () => {
  assert.equal(isControl(SECOND), true);
  assert.equal(isControl(FIRST), false);
});

test("CONTROL_SLUGS is derived from the registry, not listed twice", () => {
  const flagged = AGENTS.filter((a) => a.control).map((a) => a.slug);
  assert.deepEqual([...CONTROL_SLUGS].sort(), flagged.sort());
});

test("isControl is false for an unknown slug rather than throwing", () => {
  assert.equal(isControl("not_an_agent"), false);
  assert.equal(isControl(""), false);
});

// ------------------------------------------------------------- the ranking

const book = (agent, pct) => ({ agent, cumulative_return_pct: pct });

test("rankByReturn drops the control book", () => {
  // The case this exists for: a control one day old, down slightly, would
  // otherwise land last and read as the worst method on the page.
  const ranked = rankByReturn([
    book(FIRST, 19.34),
    book("philip_fisher", 16.72),
    book(SECOND, -0.6),
  ]);
  assert.deepEqual(
    ranked.map((p) => p.agent),
    [FIRST, "philip_fisher"],
  );
});

test("rankByReturn drops a control that would have ranked first", () => {
  // Not only the bottom: a control that happened to start well would
  // take the gold medal on a week of history.
  const ranked = rankByReturn([book(FIRST, 19.34), book(SECOND, 95.0)]);
  assert.deepEqual(
    ranked.map((p) => p.agent),
    [FIRST],
  );
});

test("rankByReturn sorts the rest best first", () => {
  const ranked = rankByReturn([
    book("walter_schloss", -2.82),
    book(FIRST, 19.34),
    book("warren_buffett", 4.02),
  ]);
  assert.deepEqual(
    ranked.map((p) => p.agent),
    [FIRST, "warren_buffett", "walter_schloss"],
  );
});

test("rankByReturn does not mutate its input", () => {
  const input = [book("warren_buffett", 4.02), book(FIRST, 19.34)];
  const before = input.map((p) => p.agent);
  rankByReturn(input);
  assert.deepEqual(
    input.map((p) => p.agent),
    before,
    "the caller's array was reordered in place",
  );
});

test("rankByReturn tolerates an empty book list", () => {
  assert.deepEqual(rankByReturn([]), []);
});

test("rankByReturn keeps every non-control book", () => {
  const books = AGENTS.map((a, i) => book(a.slug, i));
  const kept = rankByReturn(books).length;
  assert.equal(kept, AGENTS.length - CONTROL_SLUGS.size);
});
