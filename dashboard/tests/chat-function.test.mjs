// Tests for the assistant endpoint's pure logic.
//
// Run with `npm test` from dashboard/, which is `node --test` — the
// runner built into Node, so this suite adds no dependency to a project
// whose only JavaScript dependency budget is the dashboard itself.
//
// What is tested here is everything in functions/api/chat.js that does
// not make a network call: the shaping of a request, the classification
// of a source, and the several ways Gemini can return HTTP 200 with no
// answer in it. The fetch itself is not mocked — a test that asserts
// against a hand-written fake of somebody else's API mostly tests the
// fake.
//
// The file under test lives outside dashboard/ because it is deployed
// by Cloudflare Pages from the repository root. It cannot hold its own
// test: every module under functions/ becomes a route, and a route
// called /api/chat.test is not something to publish.

import test from "node:test";
import assert from "node:assert/strict";

import {
  TRUSTED_DOMAINS,
  hostOf,
  isTrusted,
  keyProblem,
  readApiKey,
  readReply,
  readSources,
  sanitizeMessage,
  toGeminiContents,
} from "../../functions/api/chat.js";

test("readApiKey prefers the documented name", () => {
  const found = readApiKey({ GEMINI_API_KEY: "abc", GEMINI2_API_KEY: "xyz" });
  assert.equal(found.key, "abc");
  assert.equal(found.name, "GEMINI_API_KEY");
});

test("readApiKey finds a single differently-named Gemini binding", () => {
  // The case this exists for: a key labelled where it was issued rather
  // than where it is read.
  const found = readApiKey({ SITE_PASSWORD: "s", GEMINI2_API_KEY: "xyz" });
  assert.equal(found.key, "xyz");
  assert.equal(found.name, "GEMINI2_API_KEY");
});

test("readApiKey trims surrounding whitespace", () => {
  // A key pasted into a web form arrives with a trailing newline often
  // enough that this is worth handling rather than debugging as a 401.
  assert.equal(readApiKey({ GEMINI_API_KEY: "  abc\n" }).key, "abc");
});

test("readApiKey refuses to choose between two candidates", () => {
  const found = readApiKey({ GEMINI_ONE: "a", GEMINI_TWO: "b" });
  assert.equal(found.key, null);
  assert.deepEqual(found.candidates, ["GEMINI_ONE", "GEMINI_TWO"]);
});

test("readApiKey ignores bindings that are present but empty", () => {
  // Cloudflare will happily hold a variable set to "". Treating that as
  // configured produces a 401 from Google instead of a usable message.
  assert.equal(readApiKey({ GEMINI_API_KEY: "   " }).key, null);
  assert.equal(readApiKey({ GEMINI_API_KEY: undefined }).key, null);
  assert.equal(readApiKey({}).key, null);
});

test("keyProblem names competing candidates but never a value", () => {
  const message = keyProblem(["GEMINI_ONE", "GEMINI_TWO"]);
  assert.match(message, /GEMINI_ONE, GEMINI_TWO/);
  assert.match(message, /name it GEMINI_API_KEY/);
});

test("keyProblem tells the reader a redeploy is needed", () => {
  // A variable added after the last build is bound to the next one, and
  // "I already added it" is the likeliest thing the reader is thinking.
  assert.match(keyProblem([]), /redeploy/);
});

test("sanitizeMessage keeps a well-formed turn", () => {
  assert.deepEqual(sanitizeMessage({ role: "user", content: "  hello  " }), {
    role: "user",
    content: "hello",
  });
});

test("sanitizeMessage coerces any unknown role to user", () => {
  // Only "assistant" may become an assistant turn. A browser sending
  // role: "system" must not be able to inject one.
  assert.equal(sanitizeMessage({ role: "system", content: "x" }).role, "user");
  assert.equal(sanitizeMessage({ role: "model", content: "x" }).role, "user");
  assert.equal(
    sanitizeMessage({ role: "assistant", content: "x" }).role,
    "assistant",
  );
});

test("sanitizeMessage rejects anything without usable text", () => {
  assert.equal(sanitizeMessage(null), null);
  assert.equal(sanitizeMessage("a string"), null);
  assert.equal(sanitizeMessage({ role: "user" }), null);
  assert.equal(sanitizeMessage({ role: "user", content: "   " }), null);
  assert.equal(sanitizeMessage({ role: "user", content: 42 }), null);
});

test("sanitizeMessage truncates a very long turn", () => {
  const long = "a".repeat(10_000);
  assert.equal(sanitizeMessage({ role: "user", content: long }).content.length, 4000);
});

test("toGeminiContents renames the assistant's turns to model", () => {
  // Gemini rejects role: "assistant" outright, so this rename is the
  // difference between a working conversation and a 400.
  assert.deepEqual(
    toGeminiContents([
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ]),
    [
      { role: "user", parts: [{ text: "hi" }] },
      { role: "model", parts: [{ text: "hello" }] },
    ],
  );
});

test("hostOf prefers an explicit domain field", () => {
  assert.equal(
    hostOf({ domain: "Reuters.com", title: "Apple beats", uri: "https://x/y" }),
    "reuters.com",
  );
});

test("hostOf accepts a title that is a bare hostname", () => {
  assert.equal(hostOf({ title: "www.sec.gov" }), "sec.gov");
  assert.equal(hostOf({ title: "fred.stlouisfed.org." }), "fred.stlouisfed.org");
});

test("hostOf ignores a title that is prose", () => {
  // "Reuters: Apple Inc. beats" contains a dot but is not a hostname.
  // Treating it as one would produce a nonsense domain that then fails
  // the allowlist for the wrong reason.
  assert.equal(hostOf({ title: "Reuters: Apple Inc. beats estimates" }), null);
  assert.equal(hostOf({ title: "Q3 2026 results" }), null);
});

test("hostOf falls back to a real uri when the title is prose", () => {
  assert.equal(
    hostOf({ title: "Apple Inc. 10-Q", uri: "https://www.sec.gov/Archives/x.htm" }),
    "sec.gov",
  );
});

test("hostOf refuses to name Google's redirector as the publisher", () => {
  // Grounding chunks usually carry a redirect rather than the source
  // URL. Reporting it would put google.com in the source list of every
  // answer and, worse, would not be on the allowlist — so every answer
  // would look badly sourced.
  assert.equal(
    hostOf({
      uri: "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
    }),
    null,
  );
});

test("hostOf returns null rather than guessing", () => {
  assert.equal(hostOf(null), null);
  assert.equal(hostOf({}), null);
  assert.equal(hostOf({ uri: "not a url" }), null);
});

test("isTrusted matches a listed domain and its subdomains", () => {
  assert.equal(isTrusted("sec.gov"), true);
  assert.equal(isTrusted("www.sec.gov"), true);
  assert.equal(isTrusted("efts.sec.gov"), true);
  assert.equal(isTrusted("reuters.com"), true);
});

test("isTrusted rejects a domain that merely contains a listed one", () => {
  // The case this list exists to stop: anyone may register
  // sec.gov.example.com, and a substring test would trust it.
  assert.equal(isTrusted("sec.gov.attacker.com"), false);
  assert.equal(isTrusted("sec-gov.com"), false);
  assert.equal(isTrusted("notsec.gov"), false);
  assert.equal(isTrusted("fakereuters.com"), false);
});

test("isTrusted honours a subdomain-scoped entry", () => {
  // finance.yahoo.com is listed; the rest of Yahoo is not.
  assert.equal(isTrusted("finance.yahoo.com"), true);
  assert.equal(isTrusted("yahoo.com"), false);
  assert.equal(isTrusted("news.yahoo.com"), false);
});

test("isTrusted treats an unknown host as untrusted", () => {
  assert.equal(isTrusted(null), false);
  assert.equal(isTrusted(""), false);
  assert.equal(isTrusted("some-stock-blog.example"), false);
});

test("every listed domain is trusted by its own rule", () => {
  // Guards against a typo in the list itself — a leading dot or a
  // stray https:// would silently trust nothing.
  for (const domain of TRUSTED_DOMAINS) {
    assert.equal(isTrusted(domain), true, `${domain} should match itself`);
  }
});

test("readSources returns nothing when the model did not search", () => {
  assert.deepEqual(readSources(undefined), []);
  assert.deepEqual(readSources({}), []);
  assert.deepEqual(readSources({ groundingChunks: "not an array" }), []);
});

test("readSources classifies each source and deduplicates by host", () => {
  const sources = readSources({
    groundingChunks: [
      { web: { domain: "sec.gov", title: "10-K", uri: "https://redirect/1" } },
      { web: { domain: "sec.gov", title: "10-Q", uri: "https://redirect/2" } },
      { web: { domain: "some-blog.example", title: "AAPL to $500" } },
    ],
  });

  assert.equal(sources.length, 2);
  assert.deepEqual(
    sources.map((s) => [s.domain, s.trusted]),
    [
      ["sec.gov", true],
      ["some-blog.example", false],
    ],
  );
});

test("readSources keeps unidentifiable sources separate and untrusted", () => {
  // Two chunks nobody can attribute are two problems, not one, and
  // neither may collapse into the other or be silently dropped —
  // dropping them would make an answer look better sourced than it is.
  const sources = readSources({
    groundingChunks: [
      { web: { uri: "https://vertexaisearch.cloud.google.com/redirect/a" } },
      { web: { uri: "https://vertexaisearch.cloud.google.com/redirect/b" } },
    ],
  });

  assert.equal(sources.length, 2);
  assert.deepEqual(sources.map((s) => s.trusted), [false, false]);
  assert.deepEqual(sources.map((s) => s.domain), [null, null]);
});

test("readSources skips chunks that carry no web source at all", () => {
  const sources = readSources({
    groundingChunks: [{ retrievedContext: {} }, null, { web: { domain: "ft.com" } }],
  });
  assert.deepEqual(sources.map((s) => s.domain), ["ft.com"]);
});

test("readReply returns the text of a normal answer", () => {
  const { text, problem } = readReply({
    candidates: [
      { content: { parts: [{ text: "Apple's " }, { text: "revenue is X." }] } },
    ],
  });
  assert.equal(text, "Apple's revenue is X.");
  assert.equal(problem, null);
});

test("readReply names a blocked prompt", () => {
  const { text, problem } = readReply({ promptFeedback: { blockReason: "SAFETY" } });
  assert.equal(text, null);
  assert.match(problem, /blocked \(SAFETY\)/);
});

test("readReply explains an answer that ran out of tokens", () => {
  // This model thinks before it answers out of the same budget, so an
  // empty reply with MAX_TOKENS is the expected shape of "the question
  // was too big" — not a bug, and not an empty bubble.
  const { text, problem } = readReply({
    candidates: [{ finishReason: "MAX_TOKENS", content: { parts: [] } }],
  });
  assert.equal(text, null);
  assert.match(problem, /ran out of room/);
});

test("readReply explains a safety stop and a recitation stop", () => {
  assert.match(
    readReply({ candidates: [{ finishReason: "SAFETY" }] }).problem,
    /can't answer/,
  );
  assert.match(
    readReply({ candidates: [{ finishReason: "RECITATION" }] }).problem,
    /copyrighted/,
  );
});

test("readReply handles a response with no candidates", () => {
  const { text, problem } = readReply({});
  assert.equal(text, null);
  assert.match(problem, /no answer/);
});
