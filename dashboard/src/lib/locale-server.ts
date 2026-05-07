// Server-side locale resolution.
//
// The Providers (client) writes a cookie `council:locale` whenever
// the user toggles language. Server components read that cookie at
// request time so SSR'd HTML is already in the correct language —
// no flash of English-then-Hebrew on first paint.
//
// Fallback: "en". The cookie is httpOnly=false so the client can read
// it too if it ever wants to.

import { cookies } from "next/headers";
import type { Locale } from "./i18n";
import { t as translate } from "./i18n";

export const LOCALE_COOKIE = "council:locale";

export function readLocale(): Locale {
  const value = cookies().get(LOCALE_COOKIE)?.value;
  return value === "he" ? "he" : "en";
}

/** Curry a `t()` function for server components. */
export function makeTranslator(locale: Locale): (key: string) => string {
  return (key: string) => translate(locale, key);
}

/** Convenience: read locale and return `(t, locale)` tuple. */
export function getServerI18n(): {
  locale: Locale;
  t: (key: string) => string;
} {
  const locale = readLocale();
  return { locale, t: makeTranslator(locale) };
}
