// A day must be read once, however many copies of its file exist.
//
// macOS file sync resolves a write collision by keeping both sides and
// renaming one "<name> 2.json". Twenty-three of those sit in data/ —
// one per agent for 2026-10-30, byte-identical to the original — and
// every loader globbed "*.json", so that day was counted twice: 404
// decision rows and 34 trades, doubling one session's exits and
// showing every ticker twice in the agent's recent-decisions list.
//
// .gitignore already hides the pattern, which is why none of it was
// visible in `git status` while all of it was visible on the page.
// Hiding them from git does not hide them from readdir, so the filter
// belongs in the loader.

import test from "node:test";
import assert from "node:assert/strict";

import { canonicalDayFiles } from "../src/lib/day-files.ts";

test("a sync-collision copy is dropped in favour of the original", () => {
  assert.deepEqual(
    canonicalDayFiles(["2026-10-29.json", "2026-10-30.json", "2026-10-30 2.json"]),
    ["2026-10-29.json", "2026-10-30.json"],
  );
});

test("several copies of one day still yield one file", () => {
  assert.deepEqual(
    canonicalDayFiles(["2026-10-30 3.json", "2026-10-30 2.json", "2026-10-30.json"]),
    ["2026-10-30.json"],
  );
});

test("a copy with no original is still read, once", () => {
  // Losing the day entirely would be a worse failure than reading the
  // surviving copy of it.
  assert.deepEqual(
    canonicalDayFiles(["2026-10-30 3.json", "2026-10-30 2.json"]),
    ["2026-10-30 2.json"],
  );
});

test("non-json entries are ignored", () => {
  assert.deepEqual(
    canonicalDayFiles(["2026-10-30.json", ".DS_Store", "notes.txt", "2026-10-30.tmp"]),
    ["2026-10-30.json"],
  );
});

test("the result is sorted by day", () => {
  assert.deepEqual(
    canonicalDayFiles(["2026-11-02.json", "2026-05-06.json", "2026-10-30.json"]),
    ["2026-05-06.json", "2026-10-30.json", "2026-11-02.json"],
  );
});

test("a legitimate name ending in a digit is not mistaken for a copy", () => {
  // The agent slug benjamin_graham_2 proves the project does use
  // trailing digits meaningfully; only " <digits>" before .json is a
  // collision marker, and a day file is never named this way.
  assert.deepEqual(
    canonicalDayFiles(["2026-10-30.json", "summary2.json"]),
    ["2026-10-30.json", "summary2.json"],
  );
});

test("the real data tree has collision copies for the loader to drop", () => {
  // A guard on the premise: if the copies ever disappear this test
  // says so, rather than the suite silently proving nothing.
  const sample = [
    "2026-10-30.json",
    "2026-10-30 2.json",
    "2026-11-02.json",
  ];
  assert.equal(canonicalDayFiles(sample).length, 2);
});
