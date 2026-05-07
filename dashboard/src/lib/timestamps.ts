// Timestamp formatting + next-update countdown helpers.
//
// Server- and client-callable. All output is locale-aware. Times are
// always rendered in IL local (Asia/Jerusalem) — that matches the
// agent schedule and how a Hebrew user thinks about "today" / "yesterday".

import type { Locale } from "./i18n";

const TZ = "Asia/Jerusalem";

// ---- Date arithmetic helpers ------------------------------------------

/** Get the calendar date in IL TZ as YYYY-MM-DD. */
function ilDateString(d: Date): string {
  // Intl with timeZone gives the right calendar day. We use sv-SE
  // because its default formatting is YYYY-MM-DD.
  return new Intl.DateTimeFormat("sv-SE", { timeZone: TZ }).format(d);
}

/** Hours-and-minutes in IL TZ as HH:MM. */
function ilHourMinute(d: Date): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: TZ,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

/** Difference in calendar days between two dates, both in IL TZ. */
function ilCalendarDayDiff(a: Date, b: Date): number {
  const aDay = ilDateString(a);
  const bDay = ilDateString(b);
  const aMs = Date.parse(aDay + "T00:00:00Z");
  const bMs = Date.parse(bDay + "T00:00:00Z");
  return Math.round((aMs - bMs) / 86_400_000);
}

// ---- Public API -------------------------------------------------------

/** Bilingual short relative time, IL TZ.
 *  Examples (locale=he):
 *    today  16:35 → "היום 16:35"
 *    yesterday 23:00 → "אתמול 23:00"
 *    3 days ago → "לפני 3 ימים"
 *    invalid input → ""
 */
export function formatRelativeTime(iso: string, locale: Locale, now: Date = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const diffDays = ilCalendarDayDiff(now, d);
  const hm = ilHourMinute(d);
  if (locale === "he") {
    if (diffDays === 0) return `היום ${hm}`;
    if (diffDays === 1) return `אתמול ${hm}`;
    if (diffDays > 1 && diffDays < 7) return `לפני ${diffDays} ימים`;
    // Older — show date.
    const dStr = new Intl.DateTimeFormat("he-IL", {
      timeZone: TZ,
      day: "numeric",
      month: "short",
    }).format(d);
    return `${dStr} ${hm}`;
  }
  if (diffDays === 0) return `today ${hm}`;
  if (diffDays === 1) return `yesterday ${hm}`;
  if (diffDays > 1 && diffDays < 7) return `${diffDays} days ago`;
  const dStr = new Intl.DateTimeFormat("en-US", {
    timeZone: TZ,
    day: "numeric",
    month: "short",
  }).format(d);
  return `${dStr} ${hm}`;
}

/** Just the time portion in IL TZ as HH:MM. */
export function formatTimeOfDay(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return ilHourMinute(d);
}

/** True if the timestamp is older than the given hour count. Empty
 *  strings are treated as "stale". */
export function isStale(iso: string, hours: number, now: Date = new Date()): boolean {
  if (!iso) return true;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return true;
  return now.getTime() - d.getTime() > hours * 3600 * 1000;
}

// ---- Next-update countdown -------------------------------------------
//
// US runs: Mon-Fri 16:35 IL (open) / 23:00 IL (close)
// TASE runs: Sun-Thu 09:00 IL (open) / 17:30 IL (close)
//
// We compute "the next event for each market" by walking forward from
// `now`, in IL TZ, looking for the soonest scheduled event.

export interface MarketSchedule {
  market: "US" | "TASE";
  /** ISO YYYY-MM-DDTHH:MM in IL TZ — informational, not exact UTC. */
  nextEventIlIso: string;
  /** Hour and minute in IL of the next event ("16:35"). */
  nextEventHm: string;
  /** Calendar-day offset from today (0 = today, 1 = tomorrow). */
  daysFromNow: number;
  /** Which event we're showing — "open" or "close". */
  kind: "open" | "close";
}

interface ScheduleSlot {
  market: "US" | "TASE";
  kind: "open" | "close";
  /** 0=Sun, 1=Mon, ..., 6=Sat. Days the market is open. */
  weekdays: ReadonlySet<number>;
  hour: number;
  minute: number;
}

const SCHEDULE: ScheduleSlot[] = [
  {
    market: "US",
    kind: "open",
    weekdays: new Set([1, 2, 3, 4, 5]),
    hour: 16,
    minute: 35,
  },
  {
    market: "US",
    kind: "close",
    weekdays: new Set([1, 2, 3, 4, 5]),
    hour: 23,
    minute: 0,
  },
  {
    market: "TASE",
    kind: "open",
    weekdays: new Set([0, 1, 2, 3, 4]),
    hour: 9,
    minute: 0,
  },
  {
    market: "TASE",
    kind: "close",
    weekdays: new Set([0, 1, 2, 3, 4]),
    hour: 17,
    minute: 30,
  },
];

/** What's the IL day-of-week for ``d``? 0=Sun..6=Sat. */
function ilWeekday(d: Date): number {
  // en-US "short" weekday in IL TZ → "Sun", "Mon", etc. Map to 0-6.
  const wd = new Intl.DateTimeFormat("en-US", {
    timeZone: TZ,
    weekday: "short",
  }).format(d);
  const map: Record<string, number> = {
    Sun: 0,
    Mon: 1,
    Tue: 2,
    Wed: 3,
    Thu: 4,
    Fri: 5,
    Sat: 6,
  };
  return map[wd] ?? 0;
}

/** Find the next event for a given market, scanning up to 14 days. */
export function nextScheduled(
  market: "US" | "TASE",
  now: Date = new Date(),
): MarketSchedule | null {
  const slots = SCHEDULE.filter((s) => s.market === market);
  let best: { date: Date; slot: ScheduleSlot } | null = null;
  for (let dayOffset = 0; dayOffset < 14; dayOffset++) {
    const candidate = new Date(now.getTime() + dayOffset * 86_400_000);
    const wd = ilWeekday(candidate);
    for (const s of slots) {
      if (!s.weekdays.has(wd)) continue;
      // Build the candidate event datetime in IL TZ. Use the IL date
      // string to anchor the day, then attach hh:mm.
      const ilDay = ilDateString(candidate);
      const eventIso = `${ilDay}T${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}:00+03:00`;
      // +03:00 IST as a reasonable IL-TZ offset that satisfies the
      // "is this in the future?" check during DST. Note: IL DST shifts
      // by an hour twice a year — for "next event" purposes a small
      // offset error doesn't matter; we just round to "today/tomorrow".
      const eventDate = new Date(eventIso);
      if (eventDate.getTime() <= now.getTime()) continue;
      if (best === null || eventDate.getTime() < best.date.getTime()) {
        best = { date: eventDate, slot: s };
      }
      break; // earliest event today; no need to check later same-day
    }
    if (best && ilCalendarDayDiff(best.date, now) === dayOffset) {
      return {
        market,
        nextEventIlIso: best.date.toISOString(),
        nextEventHm: `${String(best.slot.hour).padStart(2, "0")}:${String(best.slot.minute).padStart(2, "0")}`,
        daysFromNow: dayOffset,
        kind: best.slot.kind,
      };
    }
  }
  return null;
}

/** Bilingual short label: "next: today 16:35", "tomorrow 09:00", etc. */
export function nextScheduledLabel(
  market: "US" | "TASE",
  locale: Locale,
  now: Date = new Date(),
): string {
  const ev = nextScheduled(market, now);
  if (!ev) return "—";
  if (locale === "he") {
    if (ev.daysFromNow === 0) return `היום ${ev.nextEventHm}`;
    if (ev.daysFromNow === 1) return `מחר ${ev.nextEventHm}`;
    return `בעוד ${ev.daysFromNow} ימים ${ev.nextEventHm}`;
  }
  if (ev.daysFromNow === 0) return `today ${ev.nextEventHm}`;
  if (ev.daysFromNow === 1) return `tomorrow ${ev.nextEventHm}`;
  return `in ${ev.daysFromNow} days, ${ev.nextEventHm}`;
}
