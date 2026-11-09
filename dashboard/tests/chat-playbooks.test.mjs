// The assistant can judge a company the agents never looked at.
//
// Asked "what do the agents say about XMTR" it answered that none of
// them had said anything — true, and not the question. The question
// was what they *would* say, and it could not answer because it had
// never been told what Buffett's or Schloss's rules are. The
// playbooks were in the repository and nothing published them.
//
// They are 8,700 to 12,400 tokens each, so they are fetched per
// question, only for the agents that question names.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { SYSTEM, playbooksAskedFor } from "../../functions/api/chat.js";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

// ---- which playbook a question asks for ----------------------------------

test("a question names its agent in Hebrew", () => {
  assert.deepEqual(playbooksAskedFor("תנתח לי את XMTR לפי באפט"), ["buffett"]);
  assert.deepEqual(playbooksAskedFor("מה שלוס היה אומר על המניה"), ["schloss"]);
  assert.deepEqual(playbooksAskedFor("תריץ את זה לפי גראהם"), ["graham"]);
});

test("and in English, and by the dashboard's own slug", () => {
  assert.deepEqual(playbooksAskedFor("analyse NVDA with Schloss"), ["schloss"]);
  assert.deepEqual(playbooksAskedFor("what would warren_buffett do"), ["buffett"]);
});

test("several agents come back in the order the reader named them", () => {
  assert.deepEqual(playbooksAskedFor("Buffett then Schloss"), [
    "buffett",
    "schloss",
  ]);
  assert.deepEqual(playbooksAskedFor("Schloss then Buffett"), [
    "schloss",
    "buffett",
  ]);
});

test("a question naming no agent loads nothing", () => {
  // The common case, and it must stay free: a playbook is 9,000
  // tokens and most questions need none.
  for (const q of [
    "what is Graham's NAV",          // the agent is named, so this one does
    "מה השווי של התיק",
    "which stocks made the most money",
    "",
    null,
    undefined,
  ]) {
    const out = playbooksAskedFor(q);
    if (q && String(q).includes("Graham")) {
      assert.deepEqual(out, ["graham"]);
    } else {
      assert.deepEqual(out, [], JSON.stringify(q));
    }
  }
});

test("at most four, because ten would be 90,000 tokens", () => {
  const all = playbooksAskedFor(
    "Buffett Schloss Graham Klarman Lynch Fisher Neff Dreman Marks Greenblatt",
  );
  assert.equal(all.length, 4);
  // The four the reader asked for first, not the four this file lists
  // first.
  assert.deepEqual(all, ["buffett", "schloss", "graham", "klarman"]);
});

test("Graham II is Graham's doctrine", () => {
  // A second book opened on a later date under the same rules.
  assert.deepEqual(playbooksAskedFor("benjamin_graham_2"), ["graham"]);
});

// ---- every alias resolves to a playbook that exists -----------------------

test("every playbook on disk is reachable by name", () => {
  const onDisk = readdirSync(join(REPO, "agents"), { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .filter((slug) => {
      try {
        readFileSync(join(REPO, "agents", slug, "playbook.md"));
        return true;
      } catch {
        return false;
      }
    });
  assert.ok(onDisk.length >= 10, `only ${onDisk.length} playbooks found`);
  for (const slug of onDisk) {
    assert.deepEqual(
      playbooksAskedFor(`what would ${slug} say`),
      [slug],
      `${slug} has no alias`,
    );
  }
});

// ---- what the prompt tells it to do --------------------------------------

test("the prompt says it may judge a company nobody holds", () => {
  assert.match(SYSTEM, /Judging a company the agents have never looked at/);
  assert.match(SYSTEM, /one no agent holds, watches or has ever written a row about/);
});

test("the prompt sends it to the playbook rather than to its memory", () => {
  assert.match(SYSTEM, /Work from that text, not from what you recall of the investor/);
  assert.match(SYSTEM, /name the section/);
});

test("the prompt refuses a rank for one company", () => {
  // Greenblatt ranks against the whole market, Dreman wants the bottom
  // fifth of it, Neff compares to its average, Marks reads its cycle.
  // A rank is not a property of a company, and the same claim is made
  // by scripts/run_ticker_screen.
  assert.match(SYSTEM, /A rank is not a property of a company/);
  for (const agent of ["Greenblatt", "Dreman", "Neff", "Marks"]) {
    assert.ok(SYSTEM.includes(agent), `${agent} is not named as relative`);
  }
});

test("the prompt keeps a verdict as the doctrine's and not as advice", () => {
  assert.match(SYSTEM, /A verdict from a doctrine is the doctrine's, not yours/);
  // The existing limit it must not contradict.
  assert.match(SYSTEM, /not a licensed financial adviser/i);
});

test("the prompt makes it report what it could not test", () => {
  assert.match(SYSTEM, /Say what you could not check/);
  assert.match(
    SYSTEM,
    /a doctrine with one untested criterion is an incomplete verdict, not a pass/i,
  );
});

test("the prompt keeps the agents' own books above a fresh analysis", () => {
  assert.match(SYSTEM, /outrank any fresh analysis of yours/);
});

test("the prompt points at the exact screen for Graham", () => {
  assert.match(SYSTEM, /run_ticker_screen/);
});
