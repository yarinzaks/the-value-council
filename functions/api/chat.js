// The dashboard assistant's endpoint, on Claude.
//
// Runs as a Cloudflare Pages Function so the key stays in the secret
// store. This repository is public; a key in the browser bundle would
// be a key on the internet.
//
// Why raw fetch and not @anthropic-ai/sdk
// ---------------------------------------
//
// The official SDK is the better choice in a normal Node project. It is
// the wrong one here, for a reason specific to this deployment: Pages
// builds this site with `cd dashboard && npm ci`, so the only
// node_modules in the build live under dashboard/. This file sits in
// functions/ at the repository root, where there is no package.json for
// an import to resolve against. Adding the SDK would mean a root
// package.json and an edit to the build command — which is configured in
// the Cloudflare UI, not in this repository, and so cannot be tested
// from here. Changing a working deploy path blind is the less reliable
// option, not the more professional one.
//
// What the SDK would have provided is written out below instead, and
// tested: retry with backoff on 429 and 5xx, errors separated by class,
// and a pinned API version.
//
// What changed when this moved off Gemini
// ---------------------------------------
//
// Gemini's search tool took no domain filter. The allowlist could only
// be a request in the prompt plus a check after the fact, so this file
// carried a classifier, a correction prompt, and a one-shot retry when
// every source came back off-list — roughly a hundred lines whose whole
// job was to notice the rule had been broken.
//
// Claude's web search takes `allowed_domains`. The rule is now enforced
// by the API: an off-list source cannot come back at all. The classifier,
// the correction, and the retry are gone. Sources are still listed for
// the reader, because showing the work is worth keeping on its own.

const API_URL = "https://api.anthropic.com/v1/messages";

// Pinned deliberately. The API is versioned by this header, and letting
// it float would mean a silent shape change arriving on a day nobody
// deployed anything.
const API_VERSION = "2023-06-01";

const MODEL = "claude-sonnet-5-5";

// Reasoning depth. Sonnet 5.5 defaults to `high`; `medium` is set
// explicitly because this is question-answering over data already
// assembled for it, not open-ended analysis — and because the context
// below is large enough that effort is the lever with the clearest
// effect on cost. One constant to change if answers lose their edge.
const EFFORT = "medium";

// A chat bubble. Deliberately short: 4096 tokens is already a long
// answer in this surface, and the cap is the backstop against a runaway
// reply, not a target.
const MAX_OUTPUT_TOKENS = 4096;

// Searches per question. Enough for the several narrow queries the
// system prompt asks for, bounded so one question cannot spend the
// budget of ten.
const MAX_SEARCHES = 6;

// Retries on a failure that might not repeat. 429 and 5xx only — a 400
// means the request was wrong and will be wrong again.
const MAX_ATTEMPTS = 3;
const BACKOFF_MS = 600;

/** The only sources an answer may be built from.
 *
 *  Passed to the search tool as `allowed_domains`, so this is now a
 *  filter rather than an instruction. Subdomains of each entry are
 *  included; the two entries that are themselves subdomains are listed
 *  that way on purpose, to admit one part of a larger site and not the
 *  rest.
 */
export const TRUSTED_DOMAINS = [
  // Regulators and primary filings — the source of record.
  "sec.gov",
  "annualreports.com",
  "finra.org",
  "investor.gov",
  // Macro and rates, published by the institutions that set them.
  "federalreserve.gov",
  "fred.stlouisfed.org",
  "treasury.gov",
  "bls.gov",
  "bea.gov",
  // Exchanges.
  "nasdaq.com",
  "nyse.com",
  "cboe.com",
  // Wires and financial press with published corrections policies.
  //
  // Reuters, the WSJ, the FT, Barron's and MarketWatch are deliberately
  // absent, and must not be added back. All five block Anthropic's
  // crawler, and the Messages API rejects the whole request — not the
  // one domain — with "the following domains are not accessible to our
  // user agent". One of them in this list makes every question a 400.
  // A test below holds them out.
  "bloomberg.com",
  "cnbc.com",
  "morningstar.com",
  "spglobal.com",
  "moodys.com",
  // Price and filing aggregators, for quotes rather than analysis.
  "finance.yahoo.com",
  "stockanalysis.com",
];

//: The conversation the browser may send back. Long enough for a real
//: exchange, short enough that a tab left open all day cannot grow an
//: unbounded prompt.
const MAX_HISTORY_MESSAGES = 20;
const MAX_MESSAGE_CHARS = 4000;

export const SYSTEM = `You are the assistant for a private paper-trading dashboard called The Value Council.

Twelve AI agents each run a $10,000 paper portfolio under a different value-investing doctrine — Warren Buffett, Benjamin Graham, Walter Schloss, Seth Klarman, Peter Lynch, Philip Fisher, John Neff, David Dreman, Howard Marks, Joel Greenblatt's Magic Formula, a market-core index tracker, and Mohnish Pabrai. No real money is involved anywhere.

You have two sources, and they are good at different things.

**DASHBOARD DATA** (below, as JSON) — the agents' books, positions, returns, watchlists, the realized P&L of closed trades, and the recorded reason for each trade. Its figures come from SEC filings with a known filing date, which makes it the better source for fundamentals and for anything about what an agent did. It is a build-time snapshot, not live: it carries a generated_at timestamp, so say how fresh it is whenever freshness could change the answer, and never imply it is live.

**WEB SEARCH** — better for anything current: today's price, this week's news, a filing made after the snapshot. If a search returns nothing, say so; do not fall back on memory for a figure.

## Which sources you may use

Your search is restricted at the API to this list of domains and their subdomains:

${TRUSTED_DOMAINS.join(", ")}

A source outside it cannot be returned to you, so you will never have to judge one. Two things follow:

- **A search that comes back empty usually means the figure is not on an acceptable source**, not that it does not exist. Say that, rather than reaching for memory.
- **Put a site: operator on your searches anyway.** Prefer \`site:sec.gov\`, \`site:bloomberg.com\` and the like over an open query, and issue several narrow searches rather than one broad one. The filter decides what you may read; the operator decides what you find.

## How to answer a question about a company or a number

1. **Check the dashboard data first.** If it has the figure, note it and its date.
2. **Search** for the same figure.
3. **Compare them, and say plainly whether they agree.**

When they agree, say so in a clause and give one number — do not print the same figure twice under two headings. That is padding.

When they **disagree**, that is the interesting case and worth the space. Show both with their dates and sources, and say which you would rely on for this particular question. Then explain the likely cause, because a disagreement is usually not an error:
- different as-of dates — a filing-date figure against a live quote
- trailing twelve months against a fiscal year
- a restatement the snapshot predates
- split or dividend adjustment
- genuinely different definitions of the same word (net debt, free cash flow, EV)

If you cannot explain the gap, say that too — an unexplained discrepancy is a real finding, not a failure.

## Reading a return

A book's total return is the sum of four parts, and they are in the data under return_breakdown: closed trades (realized), open positions (unrealized), dividends, and costs. An agent can show a gain while every position it currently holds is losing money — that means closed trades paid for the open ones, and saying so is the answer. Check attributed and unattributed before naming which stocks produced a gain: unattributed is the part no stock can be named for, because those trades closed before the ledger existed.

## Sourcing

- **Name your source for every figure**, with its date. A number with no source does not go in the answer.
- Prefer a filing or a regulator over a news story about a filing; prefer a news story over an aggregator.
- **Never cite a source you did not actually retrieve in this conversation.** Do not reconstruct a URL from memory.
- If a single source is all you have, say the figure is unconfirmed rather than presenting it as settled.

## Limits

- Never invent a number. If neither source has it, say so plainly.
- You cannot place trades, change any portfolio, or run an agent. If asked, say so and stop.
- You are not a licensed financial adviser and must not give personalised investment advice. Explaining what an agent did and why it did it is fine; telling the reader what they should buy is not.
- Answer in the language the question was asked in.
- Lead with the answer. Keep it short unless a discrepancy needs explaining.`;

/** The API key, by name, without ever logging its value.
 *
 *  ANTHROPIC_API_KEY is the documented name and the one checked first.
 *  A key bound under some other Anthropic-ish name is still found, and
 *  named back to the reader, because the likeliest mistake is a binding
 *  labelled where the key was issued rather than where it is read.
 */
export function readApiKey(env) {
  const usable = (name) =>
    typeof env?.[name] === "string" && env[name].trim().length > 0;

  if (usable("ANTHROPIC_API_KEY")) {
    return {
      key: env.ANTHROPIC_API_KEY.trim(),
      name: "ANTHROPIC_API_KEY",
      candidates: [],
    };
  }

  const candidates = Object.keys(env ?? {}).filter(
    (name) => /anthropic|claude/i.test(name) && usable(name),
  );
  if (candidates.length === 1) {
    return {
      key: env[candidates[0]].trim(),
      name: candidates[0],
      candidates: [],
    };
  }
  return { key: null, name: null, candidates };
}

/** What to tell the reader when no key resolved. Never a value. */
export function keyProblem(candidates) {
  if (candidates.length > 1) {
    return (
      `Several Anthropic-looking variables are set (${candidates.join(", ")}), ` +
      `so it is not clear which to use. Keep one, and name it ANTHROPIC_API_KEY.`
    );
  }
  return (
    "The assistant has no API key. Add it in the Cloudflare dashboard " +
    "(Settings → Variables and Secrets) as ANTHROPIC_API_KEY, to Production " +
    "and Preview, then redeploy — a variable added after the last build is " +
    "only bound to the next one."
  );
}

/** One turn from the browser, or null when it carries no usable text.
 *
 *  Only "assistant" may become an assistant turn. A browser sending
 *  role: "system" must not be able to inject an operator instruction.
 */
export function sanitizeMessage(message) {
  if (!message || typeof message !== "object") return null;
  const content = typeof message.content === "string" ? message.content.trim() : "";
  if (!content) return null;
  return {
    role: message.role === "assistant" ? "assistant" : "user",
    content: content.slice(0, MAX_MESSAGE_CHARS),
  };
}

/** The Messages API takes `user`/`assistant` directly — no rename.
 *
 *  Kept as a named step anyway: the browser's shape and the wire shape
 *  are different contracts that happen to agree today, and a caller
 *  that reaches past this would not notice when they stop agreeing.
 */
export function toMessages(messages) {
  return messages.map(({ role, content }) => ({ role, content }));
}

/** The publisher's host, or null when the URL is unusable.
 *
 *  Simpler than it had to be on Gemini, which returned a redirector and
 *  left the real address to be guessed. A web_search_result carries the
 *  publisher's own URL.
 */
export function hostOf(result) {
  if (!result || typeof result.url !== "string") return null;
  try {
    return new URL(result.url).hostname.replace(/^www\./, "").toLowerCase();
  } catch {
    return null;
  }
}

/** Whether a host is on the list, by domain rather than by substring.
 *
 *  The search filter already enforces this, so nothing here gates an
 *  answer. It survives as a display label and as a second opinion: a
 *  false reading on this screen would be visible, which is the point of
 *  showing sources at all.
 */
export function isTrusted(host) {
  if (!host) return false;
  return TRUSTED_DOMAINS.some(
    (domain) => host === domain || host.endsWith(`.${domain}`),
  );
}

/** Every page the answer was actually built from, deduplicated by host.
 *
 *  A web_search_tool_result's `content` is a list on success and a
 *  single error object on failure — so the shape is checked before it
 *  is indexed, and a failed search contributes no sources rather than
 *  throwing.
 */
export function readSources(content) {
  if (!Array.isArray(content)) return [];
  const seen = new Set();
  const out = [];
  for (const block of content) {
    if (block?.type !== "web_search_tool_result") continue;
    const results = block.content;
    if (!Array.isArray(results)) continue; // an error object, not results
    for (const result of results) {
      if (result?.type !== "web_search_result") continue;
      const host = hostOf(result);
      const key = host ?? `?${out.length}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        domain: host,
        trusted: isTrusted(host),
        title: typeof result.title === "string" ? result.title : null,
        uri: typeof result.url === "string" ? result.url : null,
      });
    }
  }
  return out;
}

/** The search queries the model actually issued, for the reader. */
export function readQueries(content) {
  if (!Array.isArray(content)) return [];
  return content
    .filter((block) => block?.type === "server_tool_use" && block.name === "web_search")
    .map((block) => block?.input?.query)
    .filter((query) => typeof query === "string" && query.length > 0);
}

/** The answer, or a plain statement of why there isn't one.
 *
 *  Every branch here is an HTTP 200 with no usable text in it — the
 *  shapes that would otherwise reach the reader as an empty bubble.
 */
export function readReply(data) {
  if (data?.stop_reason === "refusal") {
    const category = data?.stop_details?.category;
    return {
      text: null,
      problem: category
        ? `The model declined to answer this one (${category}).`
        : "The model declined to answer this one.",
    };
  }

  const blocks = Array.isArray(data?.content) ? data.content : [];
  const text = blocks
    .filter((block) => block?.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("")
    .trim();

  if (text) return { text, problem: null };

  if (data?.stop_reason === "max_tokens") {
    return {
      text: null,
      problem:
        "The answer ran out of room before any of it was written. Ask for " +
        "a narrower slice of the question.",
    };
  }
  if (data?.stop_reason === "pause_turn") {
    return {
      text: null,
      problem: "The model paused mid-search and returned nothing. Ask again.",
    };
  }
  return { text: null, problem: "The model returned no answer." };
}

/** Whether a failed response is worth sending again. */
export function isRetryable(status) {
  return status === 429 || status === 408 || status >= 500;
}

/** The provider's own reason for refusing, when it gave one.
 *
 *  Anthropic answers a rejected request with
 *  `{"type":"error","error":{"type":...,"message":...}}`, and that
 *  message is the only thing separating the several causes of one status
 *  code: a 400 is a disabled capability, an unsupported field, or a
 *  malformed value, and the fix differs for each. Dropping it turns a
 *  stated cause into a guess.
 *
 *  Returns "" for anything that is not that shape, so a gateway's HTML
 *  error page is not pasted into the conversation.
 */
export function providerReason(detail) {
  if (typeof detail !== "string" || !detail.trim()) return "";
  let parsed;
  try {
    parsed = JSON.parse(detail);
  } catch {
    return "";
  }
  const message = parsed?.error?.message;
  if (typeof message !== "string") return "";
  return message.trim().slice(0, 300);
}

/** What to tell the reader when the model API refused the request. */
export function upstreamProblem(status, detail) {
  const sentence =
    status === 401 || status === 403
      ? "The Anthropic API rejected the key. Check ANTHROPIC_API_KEY in the " +
        "Cloudflare dashboard, then redeploy."
      : status === 429
        ? "Rate limited by the Anthropic API, and still limited after three " +
          "attempts. Try again shortly."
        : `The model API returned ${status}.`;
  const reason = providerReason(detail);
  return reason ? `${sentence} It said: ${reason}` : sentence;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

/** The site's own published context file, fetched from its own assets. */
async function loadContext(env, request) {
  const url = new URL(request.url);
  url.pathname = "/chat-context.json";
  url.search = "";
  const response = await env.ASSETS.fetch(new Request(url.toString()));
  if (!response.ok) {
    throw new Error(`chat-context.json is ${response.status}`);
  }
  return await response.text();
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** One call to the Messages API, retried only where a retry can help.
 *
 *  This is the part the SDK would have owned. A 429 or a 5xx is sent
 *  again after a widening pause; a 400 is returned as-is, because the
 *  request was wrong and will be wrong again.
 */
export async function askClaude(apiKey, systemBlocks, messages, { fetchImpl = fetch } = {}) {
  let last = null;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
    last = await fetchImpl(API_URL, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": API_VERSION,
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: MAX_OUTPUT_TOKENS,
        // The system prompt and the dashboard JSON are identical on
        // every question from every reader, so the whole prefix caches.
        // Without this each question pays full price for ~47,000 tokens
        // it has already sent a hundred times.
        system: systemBlocks,
        messages,
        output_config: { effort: EFFORT },
        tools: [
          {
            type: "web_search_20260209",
            name: "web_search",
            max_uses: MAX_SEARCHES,
            // The allowlist, enforced by the API. Not a preference.
            allowed_domains: TRUSTED_DOMAINS,
          },
        ],
        // No temperature: current models reject it. No tool_choice:
        // Sonnet 5.5 rejects the forcing forms, and the prompt asks for
        // the searches instead.
      }),
    });

    if (last.ok || !isRetryable(last.status)) return last;
    if (attempt + 1 < MAX_ATTEMPTS) await sleep(BACKOFF_MS * (attempt + 1));
  }
  return last;
}

export async function onRequestPost({ request, env }) {
  // Said plainly rather than as a generic 500: the fix is one setting in
  // the Cloudflare dashboard, and the reader is the person who can apply
  // it.
  const { key: apiKey, candidates } = readApiKey(env);
  if (!apiKey) {
    return json({ error: keyProblem(candidates) }, 503);
  }

  let payload;
  try {
    payload = await request.json();
  } catch {
    return json({ error: "Expected a JSON body." }, 400);
  }

  const history = Array.isArray(payload.messages) ? payload.messages : [];
  const messages = history
    .slice(-MAX_HISTORY_MESSAGES)
    .map(sanitizeMessage)
    .filter(Boolean);

  if (messages.length === 0 || messages[messages.length - 1].role !== "user") {
    return json({ error: "Send at least one user message." }, 400);
  }

  let context;
  try {
    context = await loadContext(env, request);
  } catch (error) {
    return json({ error: `Dashboard data unavailable: ${error.message}` }, 503);
  }

  // Two blocks, with the cache breakpoint after the data: everything
  // before it is byte-identical between questions, and the question
  // itself lives in `messages`, after the breakpoint.
  const systemBlocks = [
    { type: "text", text: SYSTEM },
    {
      type: "text",
      text: `DASHBOARD DATA (JSON):\n${context}`,
      cache_control: { type: "ephemeral" },
    },
  ];

  const upstream = await askClaude(apiKey, systemBlocks, toMessages(messages));
  if (!upstream.ok) {
    const detail = await upstream.text();
    return json(
      {
        error: upstreamProblem(upstream.status, detail),
        detail: detail.slice(0, 500),
      },
      502,
    );
  }

  const data = await upstream.json();
  const sources = readSources(data?.content);
  const { text, problem } = readReply(data);

  return json({
    reply: text ?? problem,
    searched: sources.length > 0,
    sources,
    // Enforced upstream, so this should always be zero. Reported anyway:
    // a non-zero count here would mean the filter had stopped working,
    // and that is worth seeing rather than assuming.
    untrusted: sources.filter((source) => !source.trusted).length,
    queries: readQueries(data?.content),
  });
}

/** Anything other than POST is a mistake worth naming. */
export async function onRequest({ request }) {
  if (request.method === "POST") return;
  return json({ error: "POST a JSON body with a `messages` array." }, 405);
}
