"use client";

// The assistant's front end: a button in the corner, a panel above it.
//
// It holds no key and knows no model. Everything it does is POST the
// conversation to /api/chat, which runs as a Pages Function with the
// key in Cloudflare's secret store. Putting the call in the browser
// would put the key in the bundle, and this repository is public.
//
// The panel keeps its own transcript in component state and sends the
// whole thing each turn, because the endpoint is stateless. Nothing is
// persisted: a reload starts a new conversation, which is the right
// default for a page that renders somebody's portfolio.

import { useEffect, useRef, useState } from "react";
import { useUI } from "@/components/Providers";

type Role = "user" | "assistant";

/** One web page the model actually read, as classified by the endpoint. */
interface Source {
  /** The publisher's host, or null when it could not be established. */
  domain: string | null;
  /** Whether that host is on the endpoint's list of acceptable sources. */
  trusted: boolean;
  title: string | null;
  uri: string | null;
}

interface Turn {
  role: Role;
  content: string;
  /** True when the reply used web search — shown so the reader knows
   *  the answer left this site's own data. */
  searched?: boolean;
  /** Every domain the answer was built from, so an unacceptable one is
   *  visible whatever the model wrote about it. */
  sources?: Source[];
  /** The model was asked to search again because its first attempt was
   *  sourced entirely off the list. Worth surfacing: it means the
   *  question was hard to source, not that the answer is wrong. */
  retried?: boolean;
  /** Google's own Search Suggestions markup. Displaying it is a
   *  condition of using grounding with Google Search, so it is rendered
   *  verbatim rather than summarised. */
  searchEntryPoint?: string | null;
}

const COPY = {
  en: {
    open: "Ask about the agents",
    title: "Dashboard assistant",
    close: "Close",
    placeholder: "Ask about an agent, a position, or a stock…",
    send: "Send",
    thinking: "Thinking…",
    searched: "checked the web",
    sources: "Sources",
    unlisted: "not on the accepted list",
    unidentified: "unidentified source",
    retried: "First attempt was badly sourced — searched again.",
    empty:
      "Ask me about the agents' positions and why they hold them, or about a " +
      "company — I check this dashboard's filings data and the web, and tell " +
      "you when they disagree.",
    failed: "Something went wrong. Try again.",
  },
  he: {
    open: "שאל על הסוכנים",
    title: "עוזר הדשבורד",
    close: "סגור",
    placeholder: "שאל על סוכן, פוזיציה או מניה…",
    send: "שלח",
    thinking: "חושב…",
    searched: "בדק באינטרנט",
    sources: "מקורות",
    unlisted: "לא ברשימת המקורות המאושרים",
    unidentified: "מקור לא מזוהה",
    retried: "הניסיון הראשון הסתמך על מקורות לא מאושרים — חיפש שוב.",
    empty:
      "אפשר לשאול אותי על הפוזיציות של הסוכנים ולמה הם מחזיקים בהן, או על " +
      "חברה מסוימת — אני בודק גם את נתוני ההגשות בדשבורד וגם את האינטרנט, " +
      "ואומר לך כשיש ביניהם הבדל.",
    failed: "משהו השתבש. נסה שוב.",
  },
} as const;

type Copy = (typeof COPY)["en" | "he"];

/**
 * What the answer was actually built from.
 *
 * This exists because the model's search cannot be restricted to a list
 * of domains — Gemini's search tool takes no such parameter, so the
 * endpoint classifies the sources after the fact instead (see
 * functions/api/chat.js). Showing them is what makes that classification
 * worth anything: an answer sourced from somewhere unaccountable is
 * marked as such on the screen, whatever the prose above it claims.
 */
function SourceList({
  sources,
  retried,
  searchEntryPoint,
  searchedLabel,
  t,
}: {
  sources: Source[];
  retried: boolean;
  searchEntryPoint: string | null;
  searchedLabel: string | null;
  t: Copy;
}) {
  if (!searchedLabel && sources.length === 0) return null;

  return (
    <div className="mt-1.5 space-y-1.5">
      {retried && <div className="text-xs text-amber-500/90">{t.retried}</div>}

      {sources.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-slate-500">{t.sources}:</span>
          {sources.map((source, i) => {
            const label = source.domain ?? t.unidentified;
            const className = source.trusted
              ? "rounded border border-slate-700 bg-slate-800 px-1.5 py-0.5 text-xs text-slate-300"
              : "rounded border border-amber-700/60 bg-amber-950/40 px-1.5 py-0.5 text-xs text-amber-400";
            // The uri is Google's redirect rather than the publisher's
            // own address, so it is worth following but not worth
            // showing — the domain beside it is the honest label.
            return source.uri ? (
              <a
                key={i}
                href={source.uri}
                target="_blank"
                rel="noreferrer noopener"
                title={source.trusted ? (source.title ?? label) : t.unlisted}
                className={`${className} transition hover:brightness-125`}
              >
                {source.trusted ? label : `⚠ ${label}`}
              </a>
            ) : (
              <span key={i} className={className} title={t.unlisted}>
                {source.trusted ? label : `⚠ ${label}`}
              </span>
            );
          })}
        </div>
      )}

      {searchedLabel && sources.length === 0 && (
        <div className="text-xs text-slate-500">{searchedLabel}</div>
      )}

      {/* Google supplies this markup and requires that it be shown
          wherever grounded answers are, so it is displayed as given
          rather than summarised.

          In a sandboxed iframe, though, and not injected into this
          document. It is third-party HTML built partly from the search
          queries the model wrote, which in turn come from whatever was
          typed above — so trusting it inline means trusting Google's
          escaping with this page's origin. The sandbox costs nothing
          here: without allow-scripts nothing in it executes, without
          allow-same-origin it cannot read this page, and the chips are
          links, which still work. */}
      {searchEntryPoint && (
        <iframe
          srcDoc={searchEntryPoint}
          sandbox="allow-popups allow-popups-to-escape-sandbox"
          title={t.sources}
          // Google's chip bar is a single row; the scroll is there so an
          // unexpectedly tall variant is reachable rather than clipped.
          className="h-[76px] w-full overflow-auto rounded border-0 bg-white"
        />
      )}
    </div>
  );
}

export function ChatWidget() {
  const { locale } = useUI();
  const t = COPY[locale === "he" ? "he" : "en"];

  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns, busy]);

  async function send() {
    const question = draft.trim();
    if (!question || busy) return;

    const next: Turn[] = [...turns, { role: "user", content: question }];
    setTurns(next);
    setDraft("");
    setBusy(true);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          messages: next.map((turn) => ({
            role: turn.role,
            content: turn.content,
          })),
        }),
      });
      const body = await response.json();
      setTurns([
        ...next,
        {
          role: "assistant",
          // The endpoint's own error text is more useful than a generic
          // failure — it names the missing secret or the stale build.
          content: body.reply ?? body.error ?? t.failed,
          searched: Boolean(body.searched),
          sources: Array.isArray(body.sources) ? body.sources : [],
          retried: Boolean(body.retried),
          searchEntryPoint:
            typeof body.searchEntryPoint === "string" ? body.searchEntryPoint : null,
        },
      ]);
    } catch {
      setTurns([...next, { role: "assistant", content: t.failed }]);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t.open}
        className="fixed bottom-5 end-5 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-sky-600 text-white shadow-lg transition hover:bg-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-400"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.8}
          className="h-6 w-6"
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M8 10h8M8 14h5m7-2a8 8 0 0 1-8 8H8l-4 3v-4.6A8 8 0 1 1 20 12Z"
          />
        </svg>
      </button>
    );
  }

  return (
    <div className="fixed bottom-5 end-5 z-50 flex h-[min(32rem,80vh)] w-[min(24rem,calc(100vw-2.5rem))] flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
      <header className="flex items-center justify-between border-b border-slate-700 px-4 py-3">
        <span className="text-sm font-semibold text-slate-100">{t.title}</span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label={t.close}
          className="rounded p-1 text-slate-400 transition hover:text-slate-100"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            className="h-4 w-4"
            aria-hidden="true"
          >
            <path strokeLinecap="round" d="m6 6 12 12M18 6 6 18" />
          </svg>
        </button>
      </header>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {turns.length === 0 && (
          <p className="text-sm leading-relaxed text-slate-400">{t.empty}</p>
        )}
        {turns.map((turn, i) => (
          <div
            key={i}
            className={turn.role === "user" ? "text-end" : "text-start"}
          >
            <div
              className={
                turn.role === "user"
                  ? "inline-block max-w-[85%] rounded-2xl bg-sky-600 px-3 py-2 text-sm text-white"
                  : "inline-block max-w-[95%] whitespace-pre-wrap rounded-2xl bg-slate-800 px-3 py-2 text-sm leading-relaxed text-slate-100"
              }
            >
              {turn.content}
            </div>
            {turn.role === "assistant" && (
              <SourceList
                sources={turn.sources ?? []}
                retried={Boolean(turn.retried)}
                searchEntryPoint={turn.searchEntryPoint ?? null}
                searchedLabel={turn.searched ? t.searched : null}
                t={t}
              />
            )}
          </div>
        ))}
        {busy && <p className="text-sm text-slate-500">{t.thinking}</p>}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
        className="flex gap-2 border-t border-slate-700 p-3"
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t.placeholder}
          disabled={busy}
          className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-500 focus:outline-none disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || !draft.trim()}
          className="rounded-lg bg-sky-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:opacity-40"
        >
          {t.send}
        </button>
      </form>
    </div>
  );
}
