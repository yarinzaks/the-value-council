// Fundamentals the agents read were stale (audit FUND-2).
//
// Every live decision from about May 2026 read filings frozen at
// 2026-04-28 and no page said so. The open scan now records, on every
// book's snapshot, the newest filing it read and whether that was over
// 120 days old; this is the rule the site-wide banner reads it by.

import test from "node:test";
import assert from "node:assert/strict";

import { fundamentalsStaleness } from "../src/lib/fundamentals-freshness.ts";

const snap = (date, extra = {}) => ({ date, ...extra });

test("the latest measured run being stale raises the banner", () => {
  assert.deepEqual(
    fundamentalsStaleness([
      snap("2026-11-13", { fundamentals_stale: true, fundamentals_newest_filed: "2026-04-28" }),
      snap("2026-11-13", { fundamentals_stale: true, fundamentals_newest_filed: "2026-04-28" }),
    ]),
    { date: "2026-11-13", newestFiled: "2026-04-28" },
  );
});

test("a fresh latest run shows nothing, whatever an older one said", () => {
  assert.equal(
    fundamentalsStaleness([
      snap("2026-11-12", { fundamentals_stale: true, fundamentals_newest_filed: "2026-04-28" }),
      snap("2026-11-13", { fundamentals_stale: false, fundamentals_newest_filed: "2026-11-12" }),
    ]),
    null,
  );
});

test("snapshots that never measured are not evidence either way", () => {
  // Written before the field existed, or a book with no snapshot yet.
  assert.equal(fundamentalsStaleness([snap("2026-11-13"), null]), null);
  assert.equal(fundamentalsStaleness([]), null);
  // An unmeasured newer file does not hide a measured stale one.
  assert.deepEqual(
    fundamentalsStaleness([
      snap("2026-11-13", { fundamentals_stale: null }),
      snap("2026-11-12", { fundamentals_stale: true, fundamentals_newest_filed: "2026-04-28" }),
    ]),
    { date: "2026-11-12", newestFiled: "2026-04-28" },
  );
});

test("a run that read no fundamentals at all is stale with no date", () => {
  assert.deepEqual(
    fundamentalsStaleness([
      snap("2026-11-13", { fundamentals_stale: true, fundamentals_newest_filed: null }),
    ]),
    { date: "2026-11-13", newestFiled: null },
  );
});
