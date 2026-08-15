// The dashboard's assistant: answers questions about the agents, and
// searches the web when the answer isn't in this site's own data.
//
// Why a Function and not the browser
// ----------------------------------
//
// The dashboard is a static export, so there is no server to hold a
// key. Calling the model from the page would put the key in the
// bundle, and this repository is public. A Pages Function runs on
// Cloudflare's edge with the key in the project's secret store — the
// same place SITE_PASSWORD already lives (see ../_middleware.js), and
// the same place it must stay.
//
// Read-only, deliberately
// -----------------------
//
// This endpoint answers questions. It cannot trade, cannot write to any
// book, and is given no tool that could. An assistant able to move
// positions would be a second execution path around the whole exit
// table — the one thing the doctrine spent its length making
// unavoidable.
//
// Why raw fetch rather than the SDK
// ---------------------------------
//
// Pages Functions in this project are plain modules with no bundler and
// no node_modules; adding the Anthropic SDK would mean adding a build
// pipeline for functions/ and a dependency to audit. This endpoint is
// one HTTP call, so it makes it directly. If functions/ ever gains a
// build step, the SDK is the better answer.

const API_URL = "https://api.anthropic.com/v1/messages";
const API_VERSION = "2023-06-01";

// Opus 5 for the reasoning, and because web search's dynamic-filtering
// variant needs it. Thinking is on by default on this model.
const MODEL = "claude-opus-5";
const MAX_TOKENS = 4096;

// The only places the assistant may read from.
//
// An allowlist, not a blocklist. A blocklist assumes you can enumerate
// the bad sites; there are more of them every day, and the ones that
// matter for a stock question — scraped-quote mirrors, SEO farms
// carrying stale or invented figures, "analyst" blogs with no filing
// behind them — are exactly the ones nobody has listed yet. An
// allowlist inverts the burden: a source has to be named here to be
// read at all, and an unfamiliar domain is unreadable by default.
//
// Grouped by what each is authoritative FOR, because that is what the
// assistant has to weigh when two sources disagree. Regulators and
// exchanges are primary; the wires and the financial press are
// secondary but accountable — they publish corrections and can be sued
// for getting it wrong. Add to this list by editing it here; there is
// no runtime override, on purpose.
const TRUSTED_DOMAINS = [
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
  "reuters.com",
  "bloomberg.com",
  "wsj.com",
  "ft.com",
  "cnbc.com",
  "barrons.com",
  "marketwatch.com",
  "morningstar.com",
  "spglobal.com",
  "moodys.com",
  // Price and filing aggregators, for quotes rather than analysis.
  "finance.yahoo.com",
  "stockanalysis.com",
];

//: Web search runs on Anthropic's side — no key in the browser, no
//: scraping, and the results carry citations.
const WEB_SEARCH_TOOL = {
  type: "web_search_20260209",
  name: "web_search",
  allowed_domains: TRUSTED_DOMAINS,
};

//: Enough for a cross-check across two or three sources on a contested
//: number, and a ceiling on one runaway question.
const MAX_SEARCHES_PER_TURN = 8;

//: The conversation the browser may send back. Long enough for a real
//: exchange, short enough that a tab left open all day cannot grow an
//: unbounded prompt.
const MAX_HISTORY_MESSAGES = 20;
const MAX_MESSAGE_CHARS = 4000;

const SYSTEM = `You are the assistant for a private paper-trading dashboard called The Value Council.

Twelve AI agents each run a $10,000 paper portfolio under a different value-investing doctrine — Warren Buffett, Benjamin Graham, Walter Schloss, Seth Klarman, Peter Lynch, Philip Fisher, John Neff, David Dreman, Howard Marks, Joel Greenblatt's Magic Formula, a market-core index tracker, and Mohnish Pabrai. No real money is involved anywhere.

You have two sources, and they are good at different things.

**DASHBOARD DATA** (below, as JSON) — the agents' books, positions, returns, watchlists, and the recorded reason for each trade. Its figures come from SEC filings with a known filing date, which makes it the better source for fundamentals and for anything about what an agent did. It is a build-time snapshot, not live: it carries a generated_at timestamp, so say how fresh it is whenever freshness could change the answer, and never imply it is live.

**WEB SEARCH** — restricted to an allowlist of regulators, exchanges, and financial press. Better for anything current: today's price, this week's news, a filing made after the snapshot. If a search returns nothing, say so; do not fall back on memory for a figure.

## How to answer a question about a company or a number

1. **Check the dashboard data first.** If it has the figure, note it and its date.
2. **Search** for the same figure from an authoritative source.
3. **Compare them, and say plainly whether they agree.**

When they agree, say so in a clause and give one number — do not print the same figure twice under two headings. That is padding.

When they **disagree**, that is the interesting case and worth the space. Show both with their dates and sources, and say which you would rely on for this particular question. Then explain the likely cause, because a disagreement is usually not an error:
- different as-of dates — a filing-date figure against a live quote
- trailing twelve months against a fiscal year
- a restatement the snapshot predates
- split or dividend adjustment
- genuinely different definitions of the same word (net debt, free cash flow, EV)

If you cannot explain the gap, say that too — an unexplained discrepancy is a real finding, not a failure.

## Sourcing

- **Name your source for every figure**, with its date. A number with no source does not go in the answer.
- Prefer a filing or a regulator over a news story about a filing; prefer a news story over an aggregator.
- **Never cite a source you did not actually retrieve in this conversation.** Do not reconstruct a URL from memory.
- If a single source is all you have, say the figure is unconfirmed rather than presenting it as settled.
- If the allowlist blocked what you needed, say which kind of source you were missing rather than guessing.

## Limits

- Never invent a number. If neither source has it, say so plainly.
- You cannot place trades, change any portfolio, or run an agent. If asked, say so and stop.
- You are not a licensed financial adviser and must not give personalised investment advice. Explaining what an agent did and why it did it is fine; telling the reader what they should buy is not.
- Answer in the language the question was asked in.
- Lead with the answer. Keep it short unless a discrepancy needs explaining.`;

/** A message the browser sent, reduced to something safe to forward. */
function sanitizeMessage(message) {
  if (!message || typeof message !== "object") return null;
  const role = message.role === "assistant" ? "assistant" : "user";
  const text = typeof message.content === "string" ? message.content.trim() : "";
  if (!text) return null;
  return { role, content: text.slice(0, MAX_MESSAGE_CHARS) };
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

export async function onRequestPost({ request, env }) {
  if (!env.ANTHROPIC_API_KEY) {
    // Said plainly rather than as a generic 500: the fix is one setting
    // in the Cloudflare dashboard, and the reader is the person who can
    // apply it.
    return json(
      {
        error:
          "The assistant is not configured. Add ANTHROPIC_API_KEY as a secret " +
          "in the Pages project (Settings → Variables and Secrets).",
      },
      503,
    );
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

  const upstream = await fetch(API_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": env.ANTHROPIC_API_KEY,
      "anthropic-version": API_VERSION,
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: MAX_TOKENS,
      // The system prompt is stable and the context changes only on a
      // rebuild, so caching the pair makes every follow-up question in a
      // conversation cheap.
      system: [
        { type: "text", text: SYSTEM },
        {
          type: "text",
          text: `DASHBOARD DATA (JSON):\n${context}`,
          cache_control: { type: "ephemeral" },
        },
      ],
      tools: [{ ...WEB_SEARCH_TOOL, max_uses: MAX_SEARCHES_PER_TURN }],
      messages,
    }),
  });

  if (!upstream.ok) {
    const detail = await upstream.text();
    return json(
      { error: `The model API returned ${upstream.status}.`, detail: detail.slice(0, 500) },
      502,
    );
  }

  const result = await upstream.json();

  // A refusal arrives as a successful response with no usable content —
  // check before reading the blocks, or the reader gets an empty bubble.
  if (result.stop_reason === "refusal") {
    return json({
      reply: "I can't answer that one. Try rephrasing, or ask something else.",
      searched: false,
    });
  }

  const reply = (result.content ?? [])
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("")
    .trim();

  const searched = (result.content ?? []).some(
    (block) => block.type === "server_tool_use" || block.type === "web_search_tool_result",
  );

  return json({
    reply: reply || "I could not produce an answer for that.",
    searched,
  });
}

/** Anything other than POST is a mistake worth naming. */
export async function onRequest({ request }) {
  if (request.method === "POST") return;
  return json({ error: "POST a JSON body with a `messages` array." }, 405);
}
