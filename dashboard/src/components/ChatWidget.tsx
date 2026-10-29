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
 * The search itself is now restricted at the API to the endpoint's
 * allowlist, so an unacceptable source cannot come back — this list no
 * longer has anything to catch. It stays because showing the work is
 * worth keeping on its own: a reader can see which filing or wire a
 * figure came from, and a source marked unacceptable here would mean
 * the filter had stopped working, which is worth seeing rather than
 * assuming.
 */
function SourceList({
  sources,
  searchedLabel,
  t,
}: {
  sources: Source[];
  searchedLabel: string | null;
  t: Copy;
}) {
  if (!searchedLabel && sources.length === 0) return null;

  return (
    <div className="mt-1.5 space-y-1.5">
      {sources.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted">{t.sources}:</span>
          {sources.map((source, i) => {
            const label = source.domain ?? t.unidentified;
            const className = source.trusted
              ? "rounded-full border border-council-200 bg-council-50 px-2 py-0.5 text-xs text-council-700 dark:border-council-700 dark:bg-council-800 dark:text-council-200"
              : "rounded-full border border-amber-500/50 bg-amber-50 px-2 py-0.5 text-xs text-amber-800 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-400";
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
        <div className="text-xs text-muted">{searchedLabel}</div>
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
        title={t.open}
        /* The one solid control on the page, so it carries the same
           inverted treatment as the active nav item rather than the
           sky blue it had — that blue belonged to no other element
           here and read as a widget bolted on from somewhere else. */
        className="pressable focus-ring fixed bottom-5 end-5 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-council-900 text-white shadow-elev-3 hover:bg-council-800 dark:bg-council-100 dark:text-council-900 dark:hover:bg-white"
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
    /* The panel opens out of the button that opened it — same corner,
       same anchor — so the two read as one object in two states
       rather than a panel that happened to appear. Enter and exit
       share the path, which is what makes the corner feel like where
       this thing lives. */
    <div className="animate-material-in origin-bottom-end fixed bottom-5 end-5 z-50 flex h-[min(32rem,80vh)] w-[min(24rem,calc(100vw-2.5rem))] flex-col overflow-hidden rounded-2xl border border-council-200 bg-white shadow-elev-3 dark:border-council-800 dark:bg-council-900">
      <header className="flex items-center justify-between border-b border-council-200 px-4 py-3 dark:border-council-800">
        <span className="text-sm font-semibold text-council-900 dark:text-council-100">
          {t.title}
        </span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label={t.close}
          className="pressable focus-ring rounded-full p-1.5 text-muted hover:bg-council-100 hover:text-council-900 dark:hover:bg-council-800 dark:hover:text-council-100"
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
          <p className="text-sm leading-relaxed text-muted">{t.empty}</p>
        )}
        {turns.map((turn, i) => (
          <div
            key={i}
            className={turn.role === "user" ? "text-end" : "text-start"}
          >
            <div
              className={
                turn.role === "user"
                  ? "inline-block max-w-[85%] rounded-2xl bg-council-900 px-3 py-2 text-sm text-white dark:bg-council-100 dark:text-council-900"
                  : "inline-block max-w-[95%] whitespace-pre-wrap rounded-2xl bg-council-100 px-3 py-2 text-sm leading-relaxed text-council-900 dark:bg-council-800 dark:text-council-100"
              }
            >
              {turn.content}
            </div>
            {turn.role === "assistant" && (
              <SourceList
                sources={turn.sources ?? []}
                searchedLabel={turn.searched ? t.searched : null}
                t={t}
              />
            )}
          </div>
        ))}
        {busy && (
          /* Ongoing status is one of the four kinds of feedback a
             reader is owed, and a static word is a weak way to give
             it — three dots that are actually moving say the request
             is alive in a way "Thinking…" alone does not. */
          <p className="flex items-center gap-1.5 text-sm text-muted">
            <span className="flex gap-1" aria-hidden="true">
              <span className="h-1.5 w-1.5 rounded-full bg-muted motion-safe:animate-bounce [animation-delay:-0.3s]" />
              <span className="h-1.5 w-1.5 rounded-full bg-muted motion-safe:animate-bounce [animation-delay:-0.15s]" />
              <span className="h-1.5 w-1.5 rounded-full bg-muted motion-safe:animate-bounce" />
            </span>
            {t.thinking}
          </p>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
        className="flex gap-2 border-t border-council-200 p-3 dark:border-council-800"
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t.placeholder}
          disabled={busy}
          className="min-w-0 flex-1 rounded-xl border border-council-200 bg-council-50 px-3 py-2 text-sm text-council-900 transition-colors duration-quick ease-standard placeholder:text-muted focus:border-council-400 focus:bg-white focus:outline-none disabled:opacity-60 dark:border-council-700 dark:bg-council-800 dark:text-council-100 dark:focus:border-council-500 dark:focus:bg-council-800"
        />
        <button
          type="submit"
          disabled={busy || !draft.trim()}
          className="pressable focus-ring rounded-xl bg-council-900 px-3 py-2 text-sm font-medium text-white hover:bg-council-800 disabled:opacity-40 dark:bg-council-100 dark:text-council-900 dark:hover:bg-white"
        >
          {t.send}
        </button>
      </form>
    </div>
  );
}
