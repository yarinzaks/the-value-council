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

**DASHBOARD DATA** (below, as JSON) — the agents' books, positions, returns, watchlists, the realized P&L of closed trades, the recorded reason for each trade, and what each holding's strategy recorded about ending the position. Its figures come from SEC filings with a known filing date, which makes it the better source for fundamentals and for anything about what an agent did. It is a build-time snapshot, not live: it carries a generated_at timestamp, so say how fresh it is whenever freshness could change the answer, and never imply it is live.

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

## Why a name is held, and when it would be sold

Asked this, the assistant used to say it did not know, because the data it was handed carried eight numbers per position and no plan. Read the position before answering.

- \`exit_rule\` — what the strategy itself recorded as ending the position, in the agent's own English. 170 of the 240 holdings carry one. Quote it; do not paraphrase it into a price, and do not turn a condition into a date.
- \`intrinsic_value_usd_per_share\` — **what the agent thinks the share is worth, not a price it is waiting to pay.** 40 holdings carry one. A reader will ask for it as "the target price": name it as a valuation and say where the market sits against it. Never call it an entry price, a limit or a bid — on the dashboard's own action log that mistake printed "waiting for $67.40" about a stock trading at $24.97. Whether the agent would actually SELL there is a separate question, and only an \`exit_rule\` that names the level answers it.

A watchlist entry carries \`entry_trigger\`, and sometimes \`entry_price_target\`. Those two ARE what the agent is waiting for, and are the opposite of the field above. Every one of the 138 live watchlist entries has the trigger.

Two things are deliberately absent, and reporting them as absent is the correct answer rather than a gap to fill:

- **A holding with no \`exit_rule\`.** 70 of the 240. Some have none because the strategy records none; some had one withheld because it denies the position exists — the analyst layer was run on names the book already holds and wrote "not applicable until a position is taken" about 4 held shares of URBN. Say the agent recorded no rule. Do not reason one out of the criteria, and do not use a \`decisions\` row's \`exit_rule\` to fill the gap: those are dated records of what was said on the day, and that sentence was true of a WATCH.
- **A valuation, for nine of the thirteen books.** For some that is the doctrine, not an omission: the Magic Formula ranks earnings yield against return on capital and never values a share, so "it has no price target, it rotates annually" is the whole and correct answer.

Nothing in the data states a holding horizon, so you cannot say how long an agent means to hold. If asked, say so.

## Judging a company the agents have never looked at

A reader may give you any ticker, sector or company — one no agent holds, watches or has ever written a row about — and ask what you make of it. Answer. Two ways, and say which you are doing:

**By an agent's doctrine.** When the question names one ("analyse this by Buffett", "מה שלוס היה אומר"), the playbooks for the agents it names are appended to this prompt. Work from that text, not from what you recall of the investor: apply the criteria as the playbook numbers them, name the section, and give the verdict the rules give, not the one the reputation suggests. Where a criterion needs a figure, search for it and cite it. Where a figure cannot be found, say which criterion you could not test — a doctrine with one untested criterion is an incomplete verdict, not a pass.

Four doctrines cannot be applied to one company at all: Greenblatt ranks earnings yield and return on capital against the whole market, Dreman wants the bottom fifth of the market's P/E, Neff compares the P/E to the market's average, and Marks reads the market's cycle before any security. A rank is not a property of a company. If the reader names one of those, say the doctrine needs the whole market and offer the parts that are absolute — Greenblatt's own return-on-capital and earnings-yield figures, for instance — without calling the result a verdict.

**On your own.** When no agent is named, analyse as an analyst would: what the business does, how it earns, the balance sheet, what the numbers say about the price. Search for the figures. The same sourcing rules apply — every number gets a source and a date, and a figure you cannot find is reported as not found.

Whichever way, three things:

- **A verdict from a doctrine is the doctrine's, not yours.** "Graham's screen rejects it on four criteria" is an answer. "It is a bad investment" is advice, which you may not give.
- **Say what you could not check.** A screen run on four of seven criteria is a screen run on four of seven criteria.
- **The agents' own books stay separate from this.** If an agent holds the name, its position and the reason it bought are in the dashboard data and outrank any fresh analysis of yours about what that agent thinks.

There is also an exact version of Graham's screen, run from the terminal, which measures his criteria against the local SEC cache: \`python -m scripts.run_ticker_screen <TICKER>\`. Mention it when a reader wants Graham's verdict with no estimation in it.

## Reading a return

A book's total return is the sum of four parts, and they are in the data under return_breakdown: closed trades (realized), open positions (unrealized), dividends, and costs. An agent can show a gain while every position it currently holds is losing money — that means closed trades paid for the open ones, and saying so is the answer. Check attributed and unattributed before naming which stocks produced a gain, and read the sign of unattributed. Positive means closed result no stock can be named for, because those trades closed before the ledger existed. Negative means the named trades add up to more than the book's own closed result — a reconstructed amount is overstated, so rely on the book's total and say the names are approximate. Do not describe a negative unattributed as untraceable; it is the opposite problem.

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

## Shape of an answer

The reader is one person on a phone. They asked a specific question and
they will read the first two lines; everything after that has to earn
being read. Length is not thoroughness — an answer that buries its own
conclusion has failed even when every figure in it is right.

- **First sentence answers the question.** Not what you are about to do,
  not what the data is, not what you cannot do. The answer.
- **Target 120 words.** Go past it when the question genuinely has
  several parts, and say less when it has one. A one-line question gets
  a one-line answer.
- **One caveat, at most, and only when it changes what the reader would
  do.** The dashboard is always a build-time snapshot; saying so in
  every answer teaches the reader to skip your first paragraph. Put the
  snapshot date in the table header or at the end, not ahead of the
  content.
- **Never open with what you are not.** Not a licensed adviser, cannot
  place trades, cannot see live prices — these are true, and they belong
  at the moment they bite, in one clause, not as a preamble. If the
  question actually asks you to trade or to advise, then refusing *is*
  the answer and goes first.
- **A table when there are three or more comparable things**, and then
  the same kind of figure in every row of a column. A column holding a
  P/E for one row and a return for the next is not a column.
- **Bold the one thing that matters most, or nothing.** Six bold phrases
  in a paragraph is the same as none.
- **No section headers under about 200 words.** A heading over two
  sentences is furniture.
- **Prose for reasoning, bullets only for genuinely parallel items** —
  and at most four of them, each one line. If a bullet needs three
  lines it is a paragraph and should be written as one.
- **Offer one next step, not a menu**, and only when there is an obvious
  one.

Worth getting right: a reader who wants more will ask. A reader who was
given too much the first time stops reading the answers.`;

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

/** Which playbook a question is asking for.
 *
 *  The doctrines are published as /playbooks/<slug>.md and fetched
 *  only when a question names the agent, because one is 9,000 tokens
 *  and most questions need none. Asked to judge a company by Buffett's
 *  rules the assistant had no idea what they are — the playbooks were
 *  in the repository and nothing published them.
 *
 *  Matched on the surname in either language, and on the dashboard's
 *  own slug, because a reader writes "באפט", "Buffett" and
 *  "warren_buffett" for the same thing. Graham II shares Graham's
 *  playbook: it is the same doctrine opened on a later date.
 */
const PLAYBOOK_ALIASES = {
  buffett: ["buffett", "warren_buffett", "באפט", "בפט", "וורן"],
  schloss: ["schloss", "walter_schloss", "שלוס", "וולטר"],
  graham: ["graham", "benjamin_graham", "benjamin_graham_2", "גראהם", "גרהם", "בנג'מין"],
  klarman: ["klarman", "seth_klarman", "קלרמן", "סת"],
  lynch: ["lynch", "peter_lynch", "לינץ", "פיטר"],
  fisher: ["fisher", "philip_fisher", "פישר", "פיליפ"],
  neff: ["neff", "john_neff", "נף", "ג'ון"],
  dreman: ["dreman", "david_dreman", "דרמן", "דייוויד"],
  marks: ["marks", "howard_marks", "מארקס", "מרקס", "הווארד"],
  greenblatt: ["greenblatt", "greenblatt_magic_formula", "גרינבלט", "גרינבלאט", "הפורמולה הקסומה"],
};

/** Every playbook named anywhere in the conversation's last question. */
export function playbooksAskedFor(text) {
  const haystack = String(text || "").toLowerCase();
  const found = [];
  for (const [slug, aliases] of Object.entries(PLAYBOOK_ALIASES)) {
    // Where in the question the reader first named this agent, so the
    // cap below keeps the ones they asked for first rather than the
    // ones this object happens to list first.
    const at = Math.min(
      ...aliases
        .map((alias) => haystack.indexOf(alias.toLowerCase()))
        .filter((i) => i >= 0),
    );
    if (Number.isFinite(at)) found.push([at, slug]);
  }
  found.sort((a, b) => a[0] - b[0]);
  // A question naming every agent would be 90,000 tokens. Four is the
  // most a reader compares in one breath, and the reply says which
  // ones were read.
  return found.slice(0, 4).map(([, slug]) => slug);
}

/** The named doctrines, as the agents' own playbooks state them. */
async function loadPlaybooks(env, request, slugs) {
  if (!slugs.length) return [];
  const base = new URL(request.url);
  base.search = "";
  const out = [];
  for (const slug of slugs) {
    const url = new URL(base.toString());
    url.pathname = `/playbooks/${slug}.md`;
    try {
      const response = await env.ASSETS.fetch(new Request(url.toString()));
      if (!response.ok) continue;
      out.push({ slug, text: await response.text() });
    } catch {
      // A playbook that will not load is one doctrine the answer
      // cannot use. The question is still answerable without it, and
      // the prompt tells the assistant to say so.
      continue;
    }
  }
  return out;
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

  // The doctrines this question asked for, read off the last user
  // message. Fetched after the context so a question naming an agent
  // does not change the cached prefix for one that does not.
  const asked = playbooksAskedFor(messages[messages.length - 1]?.content);
  const playbooks = await loadPlaybooks(env, request, asked);

  // Three blocks, with the cache breakpoint after the data: everything
  // up to it is byte-identical between questions, the playbooks vary
  // per question and so sit after it, and the question itself lives in
  // `messages`, after that.
  const systemBlocks = [
    { type: "text", text: SYSTEM },
    {
      type: "text",
      text: `DASHBOARD DATA (JSON):\n${context}`,
      cache_control: { type: "ephemeral" },
    },
  ];
  if (playbooks.length > 0) {
    systemBlocks.push({
      type: "text",
      text:
        "PLAYBOOKS — the doctrines, as each agent's own playbook " +
        "states them. This is the rulebook the agent trades by, not a " +
        "summary of it. Apply it as written and name the section you " +
        "are applying.\n\n" +
        playbooks
          .map((p) => `### playbook: ${p.slug}\n\n${p.text}`)
          .join("\n\n---\n\n"),
    });
  } else if (asked.length > 0) {
    // Named an agent whose playbook would not load. Saying so beats
    // answering from an impression of the doctrine.
    systemBlocks.push({
      type: "text",
      text:
        `The reader named ${asked.join(", ")}, but the playbook could ` +
        "not be loaded. Say that you could not read the doctrine and " +
        "do not reconstruct it from memory.",
    });
  }

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
