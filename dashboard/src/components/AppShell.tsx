"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useUI } from "./Providers";

const NAV = [
  { href: "/", key: "nav_overview" },
  { href: "/agents", key: "nav_agents" },
  { href: "/history", key: "nav_history" },
  { href: "/backtest", key: "nav_backtest" },
  { href: "/watchlist", key: "nav_watchlist" },
  { href: "/journal", key: "nav_journal" },
  { href: "/insights", key: "nav_insights" },
  { href: "/compare", key: "nav_compare" },
  { href: "/glossary", key: "nav_glossary" },
] as const;

export function AppShell({
  children,
  lastSyncLabel = "",
  stale = false,
  nextUsLabel = "",
  nextTaseLabel = "",
}: {
  children: React.ReactNode;
  /** Pre-formatted bilingual relative timestamp ("today 16:35"). Empty
   *  if no sync has ever occurred. */
  lastSyncLabel?: string;
  /** True when most recent sync is older than 24h. Renders the
   *  red warning banner. */
  stale?: boolean;
  /** Pre-formatted "next US update" label ("today 23:00", "tomorrow ..."). */
  nextUsLabel?: string;
  /** Pre-formatted "next TASE update" label. */
  nextTaseLabel?: string;
}) {
  const { t, locale, setLocale, theme, setTheme } = useUI();
  const pathname = usePathname();
  const isRtl = locale === "he";

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-council-200 dark:border-council-800 bg-white/80 dark:bg-council-900/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto flex items-center justify-between px-4 sm:px-6 lg:px-8 py-3">
          <Link
            href="/"
            className="flex items-center gap-2 font-semibold text-lg tracking-tight"
          >
            <span className="inline-block w-2.5 h-2.5 rounded-full bg-gain" />
            <span>{t("app_title")}</span>
          </Link>
          <nav className={`hidden md:flex items-center gap-1 ${isRtl ? "flex-row-reverse" : ""}`}>
            {NAV.map((n) => {
              const active = pathname === n.href || (n.href !== "/" && pathname.startsWith(n.href));
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                    active
                      ? "bg-council-100 dark:bg-council-800 text-council-900 dark:text-council-100"
                      : "text-council-600 dark:text-muted hover:bg-council-50 dark:hover:bg-council-800/50"
                  }`}
                >
                  {t(n.key)}
                </Link>
              );
            })}
          </nav>
          <div className="flex items-center gap-2">
            {/* Last-sync timestamp — hidden on small screens to keep
                the toggle area uncluttered. */}
            {lastSyncLabel && (
              <span
                className="hidden lg:inline-block text-xs text-muted mr-2"
                title={lastSyncLabel}
              >
                {t("last_sync")}: {lastSyncLabel}
              </span>
            )}
            <button
              onClick={() => setLocale(locale === "en" ? "he" : "en")}
              className="px-3 py-1.5 rounded-md text-xs border border-council-200 dark:border-council-700 hover:bg-council-50 dark:hover:bg-council-800"
              aria-label="Toggle language"
            >
              {t("toggle_language")}
            </button>
            <button
              onClick={() => setTheme(theme === "light" ? "dark" : "light")}
              className="px-3 py-1.5 rounded-md text-xs border border-council-200 dark:border-council-700 hover:bg-council-50 dark:hover:bg-council-800"
              aria-label={t("toggle_theme")}
            >
              {theme === "light" ? "🌙" : "☀️"}
            </button>
          </div>
        </div>
        {/* Mobile nav row */}
        <div className="md:hidden border-t border-council-100 dark:border-council-800 overflow-x-auto">
          <div className="flex gap-1 px-4 py-2">
            {NAV.map((n) => {
              const active = pathname === n.href || (n.href !== "/" && pathname.startsWith(n.href));
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  className={`px-3 py-1.5 rounded-md text-xs whitespace-nowrap ${
                    active
                      ? "bg-council-100 dark:bg-council-800"
                      : "text-council-600 dark:text-muted"
                  }`}
                >
                  {t(n.key)}
                </Link>
              );
            })}
          </div>
        </div>
      </header>

      {/* Stale-data warning — only visible when most recent sync is
          older than 24h. Red banner under the header. */}
      {stale && (
        <div className="bg-loss/10 border-b border-loss/30 text-loss">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-2 text-center text-xs font-medium">
            {t("stale_warning")}
          </div>
        </div>
      )}

      {/* Schedule banner — informs the user when the agents scan and
          where prices come from. Bilingual via the i18n dictionary. */}
      <div className="bg-council-50 dark:bg-council-900/60 border-b border-council-200 dark:border-council-800 text-xs text-council-600 dark:text-muted">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-2 text-center">
          {t("schedule_banner")}
        </div>
      </div>

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {children}
      </main>

      <footer className="border-t border-council-200 dark:border-council-800 py-4 text-center text-xs text-muted">
        <div className="mb-1">{t("footer")}</div>
        {(nextUsLabel || nextTaseLabel) && (
          <div className="text-[11px] text-muted">
            {t("next_us_update")}: {t("next_label")} {nextUsLabel}
            {" | "}
            {t("next_tase_update")}: {t("next_label")} {nextTaseLabel}
          </div>
        )}
      </footer>
    </div>
  );
}
