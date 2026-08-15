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
// Why Gemini, and what that costs
// -------------------------------
//
// The brief for this project has always been that it must not cost
// anything (see the same reasoning in ../_middleware.js), and Gemini
// has a free tier. That choice is not free of consequences, and the
// two that matter are recorded here rather than discovered later:
//
// 1. NO DOMAIN ALLOWLIST. Gemini's search tool takes no parameter for
//    restricting which sites it may read — it is declared as
//    `{"google_search": {}}` and that is the whole of its
//    configuration. Where the source list used to be a network
//    constraint the model could not cross, it is now three weaker
//    things layered together: an instruction in the system prompt, a
//    classification of every source the model actually used, and one
//    corrective retry when nothing trustworthy came back. See
//    `readSources` below. The reader is shown the domains either way,
//    which is the part that does not depend on the model cooperating.
//
// 2. GROUNDING IS FREE ONLY ON 2.5 FLASH. On the free tier, search
//    grounding is unavailable on the 3.x models and capped at 500
//    requests a day on Gemini 2.5 Flash. That fixes the model choice
//    below; it is not a judgement that 2.5 Flash is the best model for
//    the job.
//
// Why raw fetch rather than the SDK
// ---------------------------------
//
// Pages Functions in this project are plain modules with no bundler and
// no node_modules; adding a vendor SDK would mean adding a build
// pipeline for functions/ and a dependency to audit. This endpoint is
// one HTTP call, so it makes it directly.

const API_HOST = "https://generativelanguage.googleapis.com";
const API_VERSION = "v1beta";

// Forced by the free tier: grounding with Google Search is not offered
// on the 3.x models without a billing account, and is free here up to
// 500 requests a day. Raise this only alongside a paid key.
const MODEL = "gemini-2.5-flash";

// Generous on purpose. This model thinks before it answers and the
// thinking is drawn from the same budget as the reply, so a tight
// ceiling produces an empty answer with finishReason MAX_TOKENS rather
// than a short one. That case is handled explicitly in readReply, but
// the cheaper fix is not to provoke it.
const MAX_OUTPUT_TOKENS = 8192;

// The sources the assistant is told to rely on.
//
// This was an allowlist enforced at the network layer and is now a
// list the model is asked to honour and is measured against. The
// reasoning behind the membership is unchanged: regulators and
// exchanges are primary; the wires and the financial press are
// secondary but accountable — they publish corrections and can be sued
// for getting it wrong. What a stock question attracts otherwise —
// scraped-quote mirrors, SEO farms carrying stale or invented figures,
// "analyst" blogs with no filing behind them — is exactly what nobody
// has enumerated, which is why the list names what is good rather than
// what is bad.
//
// A domain here also matches its subdomains. Add by editing this list;
// there is no runtime override, on purpose.
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

//: The conversation the browser may send back. Long enough for a real
//: exchange, short enough that a tab left open all day cannot grow an
//: unbounded prompt.
const MAX_HISTORY_MESSAGES = 20;
const MAX_MESSAGE_CHARS = 4000;

const SYSTEM = `You are the assistant for a private paper-trading dashboard called The Value Council.

Twelve AI agents each run a $10,000 paper portfolio under a different value-investing doctrine — Warren Buffett, Benjamin Graham, Walter Schloss, Seth Klarman, Peter Lynch, Philip Fisher, John Neff, David Dreman, Howard Marks, Joel Greenblatt's Magic Formula, a market-core index tracker, and Mohnish Pabrai. No real money is involved anywhere.

You have two sources, and they are good at different things.

**DASHBOARD DATA** (below, as JSON) — the agents' books, positions, returns, watchlists, and the recorded reason for each trade. Its figures come from SEC filings with a known filing date, which makes it the better source for fundamentals and for anything about what an agent did. It is a build-time snapshot, not live: it carries a generated_at timestamp, so say how fresh it is whenever freshness could change the answer, and never imply it is live.

**GOOGLE SEARCH** — better for anything current: today's price, this week's news, a filing made after the snapshot. If a search returns nothing, say so; do not fall back on memory for a figure.

## Which sources you may use

Search is not restricted for you, so restricting it is your job.

Only these domains, and their subdomains, count as acceptable sources:

${TRUSTED_DOMAINS.join(", ")}

Rules that follow from that:

- **Put a site: operator on your searches.** Prefer \`site:sec.gov\`, \`site:reuters.com\` and the like over an open query. Issue several narrow searches rather than one broad one.
- **Do not state a figure sourced from a domain outside that list.** If the only thing you can find is on some other site, say what you were looking for and that no acceptable source carried it. That is a better answer than a sourced-looking number from a mirror.
- The reader is shown every domain you actually read, marked as acceptable or not, so an off-list source will be visible whatever you write.

## How to answer a question about a company or a number

1. **Check the dashboard data first.** If it has the figure, note it and its date.
2. **Search** for the same figure from an acceptable source.
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

## Limits

- Never invent a number. If neither source has it, say so plainly.
- You cannot place trades, change any portfolio, or run an agent. If asked, say so and stop.
- You are not a licensed financial adviser and must not give personalised investment advice. Explaining what an agent did and why it did it is fine; telling the reader what they should buy is not.
- Answer in the language the question was asked in.
- Lead with the answer. Keep it short unless a discrepancy needs explaining.`;

// Appended to a second attempt when the first one came back sourced
// entirely from domains outside the list. It names the offenders,
// because "try again" without them tends to produce the same search.
function correctionFor(domains) {
  return (
    `Your previous answer used only these sources, none of which are acceptable: ` +
    `${domains.join(", ")}. Search again, restricting every query with a site: ` +
    `operator drawn from the acceptable list. If no acceptable source carries ` +
    `the figure, say so plainly instead of citing those domains.`
  );
}

/** A message the browser sent, reduced to something safe to forward. */
export function sanitizeMessage(message) {
  if (!message || typeof message !== "object") return null;
  const role = message.role === "assistant" ? "assistant" : "user";
  const text = typeof message.content === "string" ? message.content.trim() : "";
  if (!text) return null;
  return { role, content: text.slice(0, MAX_MESSAGE_CHARS) };
}

/**
 * Turn the browser's transcript into Gemini's `contents`.
 *
 * Gemini calls the assistant's turns "model", not "assistant"; sending
 * the wrong role is a 400, not a soft failure.
 */
export function toGeminiContents(messages) {
  return messages.map((message) => ({
    role: message.role === "assistant" ? "model" : "user",
    parts: [{ text: message.content }],
  }));
}

/**
 * The publisher host for one grounding chunk, or null if it cannot be
 * established.
 *
 * This is deliberately suspicious of its input. A grounding chunk's
 * `uri` is usually a Vertex redirect that reveals nothing about the
 * publisher, and `title` is usually — but not always — the bare
 * domain. So: take an explicit `domain` if the API gave one, else a
 * `title` that actually looks like a hostname, else a `uri` that is a
 * real URL rather than a redirect. Anything else returns null, and
 * null is treated as untrusted by the caller. Guessing in the other
 * direction would let an unidentifiable source pass as acceptable.
 */
export function hostOf(web) {
  if (!web || typeof web !== "object") return null;

  const normalise = (value) =>
    String(value).trim().toLowerCase().replace(/^www\./, "").replace(/\.$/, "");

  if (typeof web.domain === "string" && web.domain.trim()) {
    return normalise(web.domain);
  }

  // A hostname and nothing else: dot-separated labels ending in a
  // plausible TLD. "reuters.com" passes; "Reuters: Apple beats" does
  // not, and neither does a sentence that happens to contain a dot.
  //
  // The test runs on the normalised form rather than the raw title,
  // because a hostname is also legitimately written with a trailing
  // root dot — "fred.stlouisfed.org." — and matching before stripping
  // it rejected a real source.
  if (typeof web.title === "string") {
    const candidate = normalise(web.title);
    if (/^([a-z0-9-]+\.)+[a-z]{2,}$/.test(candidate)) return candidate;
  }

  if (typeof web.uri === "string") {
    try {
      const host = normalise(new URL(web.uri).hostname);
      // Google's own redirector is not the publisher, and reporting it
      // as one would put "google.com" in the source list of every
      // answer.
      if (!host.endsWith("google.com") && !host.endsWith("googleapis.com")) {
        return host;
      }
    } catch {
      // Not a URL. Fall through to null.
    }
  }

  return null;
}

/** Is `host` on the list, or a subdomain of something on it? */
export function isTrusted(host) {
  if (!host) return false;
  return TRUSTED_DOMAINS.some(
    (domain) => host === domain || host.endsWith(`.${domain}`),
  );
}

/**
 * Every web source the model actually read, classified.
 *
 * This is the part of the old allowlist that survives the move to
 * Gemini. It cannot stop the model reading a bad site, but it can
 * refuse to let one pass unnoticed: the caller returns this list to
 * the browser, which shows each domain and marks the ones that are not
 * acceptable.
 */
export function readSources(groundingMetadata) {
  const chunks = groundingMetadata?.groundingChunks;
  if (!Array.isArray(chunks)) return [];

  const seen = new Map();
  for (const chunk of chunks) {
    const web = chunk?.web;
    if (!web) continue;
    const host = hostOf(web);
    const key = host ?? `unidentified:${seen.size}`;
    if (seen.has(key)) continue;
    seen.set(key, {
      domain: host,
      trusted: isTrusted(host),
      title: typeof web.title === "string" ? web.title : null,
      uri: typeof web.uri === "string" ? web.uri : null,
    });
  }
  return [...seen.values()];
}

/**
 * The reply text, or a description of why there isn't one.
 *
 * Gemini has several ways of returning HTTP 200 with nothing usable in
 * it, and each looks like an empty bubble to the reader unless it is
 * named here.
 */
export function readReply(data) {
  const blockReason = data?.promptFeedback?.blockReason;
  if (blockReason) {
    return { text: null, problem: `The question was blocked (${blockReason}).` };
  }

  const candidate = data?.candidates?.[0];
  if (!candidate) {
    return { text: null, problem: "The model returned no answer." };
  }

  const text = (candidate.content?.parts ?? [])
    .filter((part) => typeof part?.text === "string")
    .map((part) => part.text)
    .join("")
    .trim();

  if (text) return { text, problem: null };

  const reason = candidate.finishReason;
  if (reason === "MAX_TOKENS") {
    return {
      text: null,
      problem:
        "The model ran out of room before it wrote an answer. Ask something narrower.",
    };
  }
  if (reason === "SAFETY" || reason === "PROHIBITED_CONTENT") {
    return { text: null, problem: "I can't answer that one. Try rephrasing." };
  }
  if (reason === "RECITATION") {
    return {
      text: null,
      problem: "The answer was too close to a copyrighted source to return.",
    };
  }
  return { text: null, problem: "The model returned an empty answer." };
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

/**
 * One call to Gemini.
 *
 * The key travels in a header, never in the query string: a URL is the
 * thing most likely to end up in a log, a referrer, or an error report.
 */
async function askGemini(apiKey, systemText, contents) {
  const response = await fetch(
    `${API_HOST}/${API_VERSION}/models/${MODEL}:generateContent`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-goog-api-key": apiKey,
      },
      body: JSON.stringify({
        // A single stable prefix, sent first, so this model's implicit
        // context caching can recognise it across the turns of a
        // conversation. Nothing else in the request is stable.
        systemInstruction: { parts: [{ text: systemText }] },
        contents,
        tools: [{ google_search: {} }],
        generationConfig: {
          maxOutputTokens: MAX_OUTPUT_TOKENS,
          // Low, not zero: this endpoint reports figures and compares
          // them, and there is nothing here that benefits from
          // invention.
          temperature: 0.2,
        },
      }),
    },
  );
  return response;
}

export async function onRequestPost({ request, env }) {
  if (!env.GEMINI_API_KEY) {
    // Said plainly rather than as a generic 500: the fix is one setting
    // in the Cloudflare dashboard, and the reader is the person who can
    // apply it.
    return json(
      {
        error:
          "The assistant is not configured. Add GEMINI_API_KEY as a secret " +
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

  const systemText = `${SYSTEM}\n\nDASHBOARD DATA (JSON):\n${context}`;
  const contents = toGeminiContents(messages);

  let upstream = await askGemini(env.GEMINI_API_KEY, systemText, contents);
  if (!upstream.ok) {
    const detail = await upstream.text();
    // 429 on this tier is almost always the free grounding cap rather
        // than a burst, and "try again in a second" would be wrong advice.
    const message =
      upstream.status === 429
        ? "The Gemini free tier's daily limit is used up (500 grounded " +
          "requests a day). It resets tomorrow."
        : `The model API returned ${upstream.status}.`;
    return json({ error: message, detail: detail.slice(0, 500) }, 502);
  }

  let data = await upstream.json();
  let sources = readSources(data?.candidates?.[0]?.groundingMetadata);

  // The one enforcement step available without a domain filter: if the
  // model searched and every source it used was off the list, say so
  // and make it search again with the offenders named. Only once — the
  // free tier allows 500 grounded requests a day and a retry loop would
  // spend them on a question that may simply have no acceptable source.
  let retried = false;
  const usedOnlyUntrusted =
    sources.length > 0 && sources.every((source) => !source.trusted);
  if (usedOnlyUntrusted) {
    retried = true;
    const offenders = sources.map((s) => s.domain ?? "an unidentified site");
    const secondAttempt = await askGemini(env.GEMINI_API_KEY, systemText, [
      ...contents,
      { role: "user", parts: [{ text: correctionFor(offenders) }] },
    ]);
    if (secondAttempt.ok) {
      const retryData = await secondAttempt.json();
      const retrySources = readSources(
        retryData?.candidates?.[0]?.groundingMetadata,
      );
      // Keep the retry only if it actually improved the sourcing.
      if (retrySources.some((source) => source.trusted)) {
        data = retryData;
        sources = retrySources;
      }
    }
    // A failed retry is not an error: the first answer still stands,
    // and its sources are still shown to the reader marked as
    // unacceptable.
  }

  const { text, problem } = readReply(data);
  const grounding = data?.candidates?.[0]?.groundingMetadata;

  return json({
    reply: text ?? problem,
    searched: sources.length > 0,
    sources,
    untrusted: sources.filter((source) => !source.trusted).length,
    retried,
    // Displaying Google's Search Suggestions is a condition of using
    // grounding with Google Search, and this is the compliant markup
    // they supply for it. The widget renders it verbatim.
    searchEntryPoint: grounding?.searchEntryPoint?.renderedContent ?? null,
    queries: Array.isArray(grounding?.webSearchQueries)
      ? grounding.webSearchQueries
      : [],
  });
}

/** Anything other than POST is a mistake worth naming. */
export async function onRequest({ request }) {
  if (request.method === "POST") return;
  return json({ error: "POST a JSON body with a `messages` array." }, 405);
}
