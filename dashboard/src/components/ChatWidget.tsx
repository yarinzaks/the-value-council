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

interface Turn {
  role: Role;
  content: string;
  /** True when the reply used web search — shown so the reader knows
   *  the answer left this site's own data. */
  searched?: boolean;
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
    empty:
      "אפשר לשאול אותי על הפוזיציות של הסוכנים ולמה הם מחזיקים בהן, או על " +
      "חברה מסוימת — אני בודק גם את נתוני ההגשות בדשבורד וגם את האינטרנט, " +
      "ואומר לך כשיש ביניהם הבדל.",
    failed: "משהו השתבש. נסה שוב.",
  },
} as const;

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
            {turn.searched && (
              <div className="mt-1 text-xs text-slate-500">{t.searched}</div>
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
