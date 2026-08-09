"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { Locale } from "@/lib/i18n";
import { t as translate } from "@/lib/i18n";

type Theme = "light" | "dark";

interface UIContextShape {
  locale: Locale;
  setLocale: (l: Locale) => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
  t: (key: string) => string;
}

const UIContext = createContext<UIContextShape | null>(null);

export function useUI(): UIContextShape {
  const ctx = useContext(UIContext);
  if (!ctx) throw new Error("useUI must be used inside <Providers>");
  return ctx;
}

const STORAGE_LOCALE = "council:locale";
const STORAGE_THEME = "council:theme";
const COOKIE_LOCALE = "council:locale";

function writeLocaleCookie(locale: Locale): void {
  // 1-year expiry, root path, lax — server reads on every request.
  const expires = new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toUTCString();
  document.cookie = `${COOKIE_LOCALE}=${locale}; expires=${expires}; path=/; SameSite=Lax`;
}

/**
 * Set when this is one of the two static language builds (see
 * lib/locale-server.ts). Those are published side by side under /en and
 * /he, so switching language is a navigation between two sites rather
 * than a cookie the server will read on the next request — there is no
 * server to read it.
 */
const FIXED_LOCALE = process.env.NEXT_PUBLIC_SITE_LOCALE;

/** `/en/agents/x` -> `/he/agents/x`; `/en` -> `/he`. */
export function swapLocalePath(pathname: string, next: Locale): string {
  return `/${next}${pathname.replace(/^\/(en|he)(?=\/|$)/, "")}`;
}

export function Providers({
  children,
  initialLocale = "en",
}: {
  children: React.ReactNode;
  initialLocale?: Locale;
}) {
  // Hydrate locale from server (cookie) so SSR + first paint match.
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const [theme, setThemeState] = useState<Theme>("light");

  // Hydrate theme from localStorage / system on mount.
  useEffect(() => {
    try {
      const storedTheme = localStorage.getItem(STORAGE_THEME) as Theme | null;
      if (storedTheme === "light" || storedTheme === "dark") {
        setThemeState(storedTheme);
      } else if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
        setThemeState("dark");
      }
    } catch {
      /* localStorage unavailable */
    }
  }, []);

  // Apply theme class + dir/lang to <html> (server already set dir/lang
  // from cookie, but this keeps them in sync after the user toggles).
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.lang = locale;
    root.dir = locale === "he" ? "rtl" : "ltr";
  }, [theme, locale]);

  const setLocale = (l: Locale) => {
    if (typeof window !== "undefined" && FIXED_LOCALE) {
      // Static build: cross to the same page on the other language site,
      // keeping any query and fragment. Deliberately does not set state
      // first — the destination is already rendered in `l`, and a repaint
      // here would only flash the new language through the old layout.
      const { pathname, search, hash } = window.location;
      window.location.href = `${swapLocalePath(pathname, l)}${search}${hash}`;
      return;
    }
    setLocaleState(l);
    try {
      localStorage.setItem(STORAGE_LOCALE, l);
    } catch {
      /* ignore */
    }
    writeLocaleCookie(l);
    // Force a hard navigation so server components re-render with the
    // new locale — otherwise SSR'd English text remains until the page
    // reloads on its own.
    if (typeof window !== "undefined") {
      window.location.reload();
    }
  };

  const setTheme = (newTheme: Theme) => {
    setThemeState(newTheme);
    try {
      localStorage.setItem(STORAGE_THEME, newTheme);
    } catch {
      /* ignore */
    }
  };

  const value = useMemo<UIContextShape>(
    () => ({
      locale,
      setLocale,
      theme,
      setTheme,
      t: (key: string) => translate(locale, key),
    }),
    [locale, theme],
  );

  return <UIContext.Provider value={value}>{children}</UIContext.Provider>;
}
