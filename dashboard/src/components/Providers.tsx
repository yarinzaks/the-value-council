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
