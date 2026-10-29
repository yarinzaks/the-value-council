// Tests for the assistant endpoint's pure logic.
//
// Run with `npm test` from dashboard/, which is `node --test` — the
// runner built into Node, so this suite adds no dependency to a project
// whose only JavaScript dependency budget is the dashboard itself.
//
// What is tested here is everything in functions/api/chat.js that does
// not require a live model: the shaping of a request, the reading of a
// response, and the retry logic — which this file owns rather than the
// SDK, and therefore has to earn. The retry tests inject a fake fetch;
// everything else is a pure function over a response body.
//
// The bodies here are hand-written and do not prove Anthropic's wire
// format. They prove this file's handling of it, which is the part that
// can regress without anyone noticing.
//
// The file under test lives outside dashboard/ because it is deployed
// by Cloudflare Pages from the repository root. It cannot hold its own
// test: every module under functions/ becomes a route, and a route
// called /api/chat.test is not something to publish.

import test from "node:test";
import assert from "node:assert/strict";

import {
  TRUSTED_DOMAINS,
  askClaude,
  hostOf,
  isRetryable,
  isTrusted,
  keyProblem,
  providerReason,
  readApiKey,
  readQueries,
  readReply,
  readSources,
  sanitizeMessage,
  toMessages,
  upstreamProblem,
} from "../../functions/api/chat.js";

// ---------------------------------------------------------------- the key

test("readApiKey prefers the documented name", () => {
  const found = readApiKey({
    ANTHROPIC_API_KEY: "abc",
    CLAUDE_KEY: "xyz",
  });
  assert.equal(found.key, "abc");
  assert.equal(found.name, "ANTHROPIC_API_KEY");
});

test("readApiKey finds a single differently-named Anthropic binding", () => {
  // The case this exists for: a key labelled where it was issued rather
  // than where it is read.
  const found = readApiKey({ SITE_PASSWORD: "s", CLAUDE_API_KEY: "xyz" });
  assert.equal(found.key, "xyz");
  assert.equal(found.name, "CLAUDE_API_KEY");
});

test("readApiKey trims surrounding whitespace", () => {
  // A key pasted into a web form arrives with a trailing newline often
  // enough that this is worth handling rather than debugging as a 401.
  assert.equal(readApiKey({ ANTHROPIC_API_KEY: "  abc\n" }).key, "abc");
});

test("readApiKey refuses to choose between two candidates", () => {
  const found = readApiKey({ ANTHROPIC_ONE: "a", ANTHROPIC_TWO: "b" });
  assert.equal(found.key, null);
  assert.deepEqual(found.candidates, ["ANTHROPIC_ONE", "ANTHROPIC_TWO"]);
});

test("readApiKey ignores bindings that are present but empty", () => {
  // Cloudflare will happily hold a variable set to "". Treating that as
  // configured produces a 401 from the API instead of a usable message.
  assert.equal(readApiKey({ ANTHROPIC_API_KEY: "   " }).key, null);
  assert.equal(readApiKey({ ANTHROPIC_API_KEY: undefined }).key, null);
  assert.equal(readApiKey({}).key, null);
});

test("readApiKey does not mistake an unrelated binding for a key", () => {
  assert.equal(readApiKey({ GEMINI_API_KEY: "old" }).key, null);
});

test("keyProblem names competing candidates but never a value", () => {
  const message = keyProblem(["ANTHROPIC_ONE", "ANTHROPIC_TWO"]);
  assert.match(message, /ANTHROPIC_ONE, ANTHROPIC_TWO/);
  assert.match(message, /name it ANTHROPIC_API_KEY/);
});

test("keyProblem tells the reader a redeploy is needed", () => {
  // A variable added after the last build is bound to the next one, and
  // "I already added it" is the likeliest thing the reader is thinking.
  assert.match(keyProblem([]), /redeploy/);
});

// ------------------------------------------------------------- the request

test("sanitizeMessage keeps a well-formed turn", () => {
  assert.deepEqual(sanitizeMessage({ role: "user", content: "  hello  " }), {
    role: "user",
    content: "hello",
  });
});

test("sanitizeMessage coerces any unknown role to user", () => {
  // Only "assistant" may become an assistant turn. A browser sending
  // role: "system" must not be able to inject an operator instruction —
  // and on this API a system turn in `messages` is a real thing, which
  // makes the coercion load-bearing rather than tidy.
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

test("toMessages passes roles through unchanged", () => {
  // Gemini needed assistant renamed to "model"; this API does not. The
  // step stays so a future divergence has somewhere to live.
  assert.deepEqual(
    toMessages([
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ]),
    [
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ],
  );
});

// -------------------------------------------------------------- the sources

test("hostOf reads the publisher from a real url", () => {
  assert.equal(hostOf({ url: "https://www.sec.gov/Archives/x.htm" }), "sec.gov");
  assert.equal(hostOf({ url: "https://fred.stlouisfed.org/series/T10Y2Y" }), "fred.stlouisfed.org");
});

test("hostOf returns null rather than guessing", () => {
  assert.equal(hostOf(null), null);
  assert.equal(hostOf({}), null);
  assert.equal(hostOf({ url: "not a url" }), null);
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
  // stray https:// would silently trust nothing, and now that the list
  // is sent to the API as a filter, a malformed entry is a rejected
  // request rather than a quiet miss.
  for (const domain of TRUSTED_DOMAINS) {
    assert.equal(isTrusted(domain), true, `${domain} should match itself`);
  }
});

test("the allowlist is plain hostnames, as the search tool requires", () => {
  for (const domain of TRUSTED_DOMAINS) {
    assert.doesNotMatch(domain, /^https?:|\/|\s/, `${domain} is not a bare host`);
  }
});

test("readSources returns nothing when the model did not search", () => {
  assert.deepEqual(readSources(undefined), []);
  assert.deepEqual(readSources([]), []);
  assert.deepEqual(readSources([{ type: "text", text: "hi" }]), []);
});

test("readSources classifies each source and deduplicates by host", () => {
  const sources = readSources([
    { type: "text", text: "Apple's revenue is X." },
    {
      type: "web_search_tool_result",
      content: [
        { type: "web_search_result", url: "https://www.sec.gov/a", title: "10-K" },
        { type: "web_search_result", url: "https://www.sec.gov/b", title: "10-Q" },
        { type: "web_search_result", url: "https://reuters.com/x", title: "Reuters" },
      ],
    },
  ]);

  assert.equal(sources.length, 2);
  assert.deepEqual(
    sources.map((s) => [s.domain, s.trusted]),
    [
      ["sec.gov", true],
      ["reuters.com", true],
    ],
  );
});

test("readSources survives a failed search instead of throwing", () => {
  // A web_search_tool_result's content is a list on success and a
  // single error object on failure. Indexing it blind would throw
  // inside the handler and turn a thin answer into a 500.
  const sources = readSources([
    { type: "web_search_tool_result", content: { error_code: "max_uses_exceeded" } },
  ]);
  assert.deepEqual(sources, []);
});

test("readSources keeps an unidentifiable source rather than dropping it", () => {
  // Dropping it would make an answer look better sourced than it is.
  const sources = readSources([
    { type: "web_search_tool_result", content: [{ type: "web_search_result", url: "::::" }] },
  ]);
  assert.equal(sources.length, 1);
  assert.equal(sources[0].domain, null);
  assert.equal(sources[0].trusted, false);
});

test("readQueries reports what the model actually searched for", () => {
  const queries = readQueries([
    { type: "server_tool_use", name: "web_search", input: { query: "site:sec.gov AAPL 10-Q" } },
    { type: "web_search_tool_result", content: [] },
    { type: "server_tool_use", name: "web_search", input: { query: "AAPL revenue" } },
  ]);
  assert.deepEqual(queries, ["site:sec.gov AAPL 10-Q", "AAPL revenue"]);
});

test("readQueries ignores a tool use that is not a search", () => {
  assert.deepEqual(
    readQueries([{ type: "server_tool_use", name: "other", input: { query: "x" } }]),
    [],
  );
});

// ---------------------------------------------------------------- the reply

test("readReply joins the text blocks of a normal answer", () => {
  const { text, problem } = readReply({
    stop_reason: "end_turn",
    content: [
      { type: "text", text: "Apple's " },
      { type: "text", text: "revenue is X." },
    ],
  });
  assert.equal(text, "Apple's revenue is X.");
  assert.equal(problem, null);
});

test("readReply ignores non-text blocks when assembling the answer", () => {
  const { text } = readReply({
    content: [
      { type: "thinking", thinking: "" },
      { type: "server_tool_use", name: "web_search", input: { query: "x" } },
      { type: "web_search_tool_result", content: [] },
      { type: "text", text: "The answer." },
    ],
  });
  assert.equal(text, "The answer.");
});

test("readReply names a refusal and its category", () => {
  const { text, problem } = readReply({
    stop_reason: "refusal",
    stop_details: { type: "refusal", category: "cyber" },
    content: [],
  });
  assert.equal(text, null);
  assert.match(problem, /declined/);
  assert.match(problem, /cyber/);
});

test("readReply handles a refusal with no category", () => {
  const { problem } = readReply({ stop_reason: "refusal", content: [] });
  assert.match(problem, /declined/);
});

test("readReply explains an answer that ran out of room", () => {
  // The model reasons before it answers out of the same budget, so an
  // empty reply with max_tokens is the expected shape of "the question
  // was too big" — not a bug, and not an empty bubble.
  const { text, problem } = readReply({ stop_reason: "max_tokens", content: [] });
  assert.equal(text, null);
  assert.match(problem, /ran out of room/);
});

test("readReply explains a turn that paused mid-search", () => {
  const { problem } = readReply({ stop_reason: "pause_turn", content: [] });
  assert.match(problem, /paused/);
});

test("readReply handles a response with no content at all", () => {
  const { text, problem } = readReply({});
  assert.equal(text, null);
  assert.match(problem, /no answer/);
});

test("a refusal wins over text that came before it", () => {
  // Partial text ahead of a decline would otherwise be served as if it
  // were the answer.
  const { text, problem } = readReply({
    stop_reason: "refusal",
    stop_details: { category: "bio" },
    content: [{ type: "text", text: "Here is how to" }],
  });
  assert.equal(text, null);
  assert.match(problem, /declined/);
});

// --------------------------------------------------------------- the retry

test("isRetryable covers what a second attempt can fix", () => {
  assert.equal(isRetryable(429), true);
  assert.equal(isRetryable(500), true);
  assert.equal(isRetryable(503), true);
  assert.equal(isRetryable(408), true);
});

test("isRetryable refuses to retry a request that was wrong", () => {
  // A 400 will be a 400 again, and a 401 will not fix itself either.
  assert.equal(isRetryable(400), false);
  assert.equal(isRetryable(401), false);
  assert.equal(isRetryable(403), false);
  assert.equal(isRetryable(404), false);
});

/** A fetch stub that returns the given statuses in order. */
function stubFetch(statuses) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body), headers: init.headers });
    const status = statuses[Math.min(calls.length - 1, statuses.length - 1)];
    return { ok: status < 400, status, async text() { return ""; } };
  };
  impl.calls = calls;
  return impl;
}

test("askClaude sends the pinned version and the key as a header", async () => {
  const fetchImpl = stubFetch([200]);
  await askClaude("secret", [{ type: "text", text: "sys" }], [], { fetchImpl });

  const [call] = fetchImpl.calls;
  assert.equal(call.headers["anthropic-version"], "2023-06-01");
  assert.equal(call.headers["x-api-key"], "secret");
});

test("askClaude asks for search restricted to the allowlist", async () => {
  const fetchImpl = stubFetch([200]);
  await askClaude("k", [], [], { fetchImpl });

  const [tool] = fetchImpl.calls[0].body.tools;
  assert.equal(tool.type, "web_search_20260209");
  assert.deepEqual(tool.allowed_domains, TRUSTED_DOMAINS);
});

test("askClaude sends no temperature and forces no tool", async () => {
  // Current models reject temperature outright, and Sonnet 5.5 rejects
  // the forcing tool_choice forms. Both would be a 400 on every single
  // request — the kind of thing a test should catch once.
  const fetchImpl = stubFetch([200]);
  await askClaude("k", [], [], { fetchImpl });

  const body = fetchImpl.calls[0].body;
  assert.equal("temperature" in body, false);
  assert.equal("tool_choice" in body, false);
});

test("askClaude retries a 429 and returns the eventual success", async () => {
  const fetchImpl = stubFetch([429, 429, 200]);
  const response = await askClaude("k", [], [], { fetchImpl });

  assert.equal(response.status, 200);
  assert.equal(fetchImpl.calls.length, 3);
});

test("askClaude gives up after three attempts rather than looping", async () => {
  const fetchImpl = stubFetch([500]);
  const response = await askClaude("k", [], [], { fetchImpl });

  assert.equal(response.status, 500);
  assert.equal(fetchImpl.calls.length, 3);
});

test("askClaude does not retry a request that was rejected", async () => {
  const fetchImpl = stubFetch([400]);
  const response = await askClaude("k", [], [], { fetchImpl });

  assert.equal(response.status, 400);
  assert.equal(fetchImpl.calls.length, 1);
});

test("askClaude returns a first-try success without retrying", async () => {
  const fetchImpl = stubFetch([200]);
  await askClaude("k", [], [], { fetchImpl });
  assert.equal(fetchImpl.calls.length, 1);
});

// -------------------------------------------- the provider's own reason

// A status code alone does not say what to fix. Three different causes
// of a 400 — a capability switched off, a field the model does not take,
// a value out of range — are told apart only by the sentence the API
// sends back, so these tests exist to keep that sentence reaching the
// reader rather than being replaced by the number.

test("providerReason reads the message out of an Anthropic error body", () => {
  const detail = JSON.stringify({
    type: "error",
    error: { type: "invalid_request_error", message: "web search is not enabled for this organization" },
  });
  assert.equal(
    providerReason(detail),
    "web search is not enabled for this organization",
  );
});

test("providerReason ignores a body that is not JSON", () => {
  assert.equal(providerReason("<html><body>502 Bad Gateway</body></html>"), "");
});

test("providerReason ignores JSON without a message", () => {
  assert.equal(providerReason(JSON.stringify({ error: { type: "x" } })), "");
  assert.equal(providerReason(JSON.stringify({ ok: true })), "");
});

test("providerReason ignores a non-string message", () => {
  assert.equal(providerReason(JSON.stringify({ error: { message: 42 } })), "");
});

test("providerReason tolerates an empty or absent body", () => {
  assert.equal(providerReason(""), "");
  assert.equal(providerReason("   "), "");
  assert.equal(providerReason(undefined), "");
  assert.equal(providerReason(null), "");
});

test("providerReason caps a long message", () => {
  const detail = JSON.stringify({ error: { message: "x".repeat(900) } });
  assert.equal(providerReason(detail).length, 300);
});

test("upstreamProblem names the status and quotes the reason", () => {
  const detail = JSON.stringify({
    error: { message: "tools.0.allowed_callers: must be [\"direct\"]" },
  });
  const message = upstreamProblem(400, detail);

  assert.match(message, /returned 400/);
  assert.match(message, /allowed_callers/);
});

test("upstreamProblem keeps the key advice on 401 and still quotes the reason", () => {
  const detail = JSON.stringify({ error: { message: "invalid x-api-key" } });
  const message = upstreamProblem(401, detail);

  assert.match(message, /ANTHROPIC_API_KEY/);
  assert.match(message, /invalid x-api-key/);
});

test("upstreamProblem keeps the rate-limit advice on 429", () => {
  assert.match(upstreamProblem(429, ""), /Rate limited/);
});

test("upstreamProblem says only the sentence when there is no reason to quote", () => {
  assert.equal(upstreamProblem(500, "<html>oops</html>"), "The model API returned 500.");
});
