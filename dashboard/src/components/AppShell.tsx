"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useUI } from "./Providers";
import { formatRelativeTime } from "../lib/timestamps";

const NAV = [
  { href: "/", key: "nav_overview" },
  { href: "/agents", key: "nav_agents" },
  // Its own entry rather than a twelfth row under /agents: it produces
  // no weights and carries no comparable return, so listing it beside
  // the eleven would invite exactly the comparison it cannot support.
  { href: "/council", key: "nav_council" },
  { href: "/history", key: "nav_history" },
  { href: "/backtest", key: "nav_backtest" },
  { href: "/watchlist", key: "nav_watchlist" },
  { href: "/journal", key: "nav_journal" },
  { href: "/brain", key: "nav_brain" },
  { href: "/insights", key: "nav_insights" },
  { href: "/compare", key: "nav_compare" },
  { href: "/glossary", key: "nav_glossary" },
] as const;

/** Sun and moon, drawn rather than typed.
 *
 * The emoji that stood here rendered in whatever the platform shipped
 * — a flat glyph on one machine, a full-colour sticker on another —
 * so the one control in the header that is pure chrome was the only
 * thing on the page whose weight nobody could predict. These inherit
 * the text colour and the stroke width of everything around them. */
function ThemeIcon({ dark }: { dark: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-4 w-4"
      aria-hidden="true"
    >
      {dark ? (
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
      ) : (
        <>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </>
      )}
    </svg>
  );
}

/** "Last updated", rendered against the reader's clock rather than the
 *  build's.
 *
 *  First paint is the absolute stamp the server computed, so the HTML
 *  is stable and hydration has nothing to disagree about. The effect
 *  then replaces it with the relative form — "today 16:35", "3 days
 *  ago" — using the browser's real `now`. In a static export that is
 *  the only clock that is not frozen: the published site rendered a
 *  relative label at build time and said "today 13:31" for two and a
 *  half months.
 */
function LastUpdated({
  iso,
  absolute,
  locale,
  stale,
  label,
}: {
  iso: string;
  absolute: string;
  locale: "en" | "he";
  stale: boolean;
  label: string;
}) {
  const [shown, setShown] = useState(absolute);
  useEffect(() => {
    if (!iso) return;
    const tick = () => setShown(formatRelativeTime(iso, locale, new Date()));
    tick();
    // A page left open overnight should stop claiming it is current.
    const timer = setInterval(tick, 60_000);
    return () => clearInterval(timer);
  }, [iso, locale]);

  if (!iso) return null;
  return (
    <span
      className={`text-xs whitespace-nowrap me-1 ${
        stale ? "text-rose-600 dark:text-rose-400 font-medium" : "text-muted"
      }`}
      // The exact instant, for a reader who wants it rather than a
      // rounded "3 days ago".
      title={absolute}
    >
      {label}: {shown}
    </span>
  );
}

export function AppShell({
  children,
  lastSyncIso = "",
  lastSyncAbsolute = "",
  stale = false,
  nextUsLabel = "",
  nextTaseLabel = "",
}: {
  children: React.ReactNode;
  /** The sync instant, ISO-8601. Empty if no sync has ever occurred. */
  lastSyncIso?: string;
  /** The same instant as dd/mm/yyyy HH:MM, computed server-side so the
   *  first paint carries a true value even before JavaScript runs. */
  lastSyncAbsolute?: string;
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

  const isActive = (href: string) =>
    pathname === href || (href !== "/" && pathname.startsWith(href));

  // One shape for every control in the header strip, so the language
  // toggle and the theme toggle are the same object at two widths
  // rather than two things that happen to sit together.
  const controlClass =
    "pressable focus-ring inline-flex items-center justify-center gap-1.5 " +
    "h-8 rounded-full border border-council-200/80 dark:border-council-700/80 " +
    "text-xs font-medium text-council-700 dark:text-council-200 " +
    "hover:bg-council-100/70 dark:hover:bg-council-800/70";

  return (
    <div className="min-h-screen flex flex-col">
      {/* The header is a material, not a strip: content passes under
          it and stays readable through the blur, and where it overlaps
          the page the content fades out rather than meeting a drawn
          line. The 1px edge is light catching the rim of the glass. */}
      <header className="material material-edge scroll-edge sticky top-0 z-30 border-b">
        <div className="max-w-7xl mx-auto flex items-center justify-between gap-3 px-4 sm:px-6 lg:px-8 py-2.5">
          <Link
            href="/"
            className="pressable focus-ring flex shrink-0 items-center gap-2 rounded-lg font-semibold text-base sm:text-lg whitespace-nowrap"
          >
            <span className="relative flex h-2.5 w-2.5 shrink-0">
              {/* A live mark, not a decoration: the dot says the book
                  is being priced. It sits still under reduced motion,
                  where a pulse is exactly what must not run. */}
              <span className="absolute inset-0 rounded-full bg-gain/40 motion-safe:animate-ping" />
              <span className="relative inline-block h-2.5 w-2.5 rounded-full bg-gain" />
            </span>
            <span>{t("app_title")}</span>
          </Link>

          {/* Ten destinations, a wordmark and three controls do not fit
              on one line until xl. Measured at lg the ten pills need
              739px beside a 210px wordmark and 100px of controls, so
              they overflowed the row and ran under the language
              button. Below xl the scrolling strip underneath is the
              nav, and it can hold all ten. */}
          <nav
            className={`hidden xl:flex shrink-0 items-center gap-0.5 ${
              isRtl ? "flex-row-reverse" : ""
            }`}
          >
            {NAV.map((n) => {
              const active = isActive(n.href);
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  aria-current={active ? "page" : undefined}
                  className={`pressable focus-ring relative rounded-full px-2.5 py-1.5 text-[13px] whitespace-nowrap ${
                    active
                      ? "bg-council-900 text-white dark:bg-council-100 dark:text-council-900 font-medium"
                      : "text-council-600 dark:text-muted hover:bg-council-100/80 dark:hover:bg-council-800/60 hover:text-council-900 dark:hover:text-council-100"
                  }`}
                >
                  {t(n.key)}
                </Link>
              );
            })}
          </nav>

          <div className="flex shrink-0 items-center gap-1.5">
            {/* Shown at every width. It was `hidden 2xl:inline-block`,
                which meant invisible on every screen under 1536px —
                including the phone this is mostly read on — so the one
                line that says how old the numbers are was the one line
                nobody saw. On a narrow screen it moves to its own row
                below rather than competing with the toggles. */}
            <span className="hidden lg:inline-block">
              <LastUpdated
                iso={lastSyncIso}
                absolute={lastSyncAbsolute}
                locale={locale}
                stale={stale}
                label={t("last_sync")}
              />
            </span>
            <button
              onClick={() => setLocale(locale === "en" ? "he" : "en")}
              className={`${controlClass} px-3`}
              aria-label="Toggle language"
            >
              {t("toggle_language")}
            </button>
            <button
              onClick={() => setTheme(theme === "light" ? "dark" : "light")}
              className={`${controlClass} w-8`}
              aria-label={t("toggle_theme")}
              title={t("toggle_theme")}
            >
              <ThemeIcon dark={theme === "light"} />
            </button>
            {/* A real way out. The gate this replaced was HTTP Basic
                auth, which the browser cannot be told to forget — see
                functions/_middleware.js. /logout is served by the gate
                itself, so it is a plain link rather than a route of
                this app — a <Link> would prefetch it, and Next would
                then end the session while the reader was still
                reading. */}
            <a
              href="/logout"
              className={`${controlClass} px-3`}
              title={t("log_out")}
            >
              {t("log_out")}
            </a>
          </div>
        </div>

        {/* Below lg the header has no room for the stamp beside the
            toggles, and it is the screen this is mostly read on. Its
            own row, under the nav. */}
        <div className="lg:hidden border-t border-council-200/60 dark:border-council-800/60 px-4 py-1.5">
          <LastUpdated
            iso={lastSyncIso}
            absolute={lastSyncAbsolute}
            locale={locale}
            stale={stale}
            label={t("last_sync")}
          />
        </div>

        {/* Compact nav row — everything below lg. The scrollbar is
            hidden because the row is short enough to flick and a
            visible track on a 36px strip is noise. */}
        <div className="xl:hidden border-t border-council-200/60 dark:border-council-800/60 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          <div className="flex gap-1 px-4 py-2">
            {NAV.map((n) => {
              const active = isActive(n.href);
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  aria-current={active ? "page" : undefined}
                  className={`pressable focus-ring rounded-full px-3 py-1.5 text-xs whitespace-nowrap ${
                    active
                      ? "bg-council-900 text-white dark:bg-council-100 dark:text-council-900 font-medium"
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
          older than 24h. A warning is one of the four kinds of
          feedback and the only one here that must interrupt, so it
          keeps its colour and gains the icon that says which kind it
          is without the reader having to parse the sentence. */}
      {stale && (
        <div
          role="status"
          className="bg-loss/10 border-b border-loss/25 text-loss"
        >
          <div className="max-w-7xl mx-auto flex items-center justify-center gap-2 px-4 sm:px-6 lg:px-8 py-2 text-center text-xs font-medium">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              className="h-3.5 w-3.5 shrink-0"
              aria-hidden="true"
            >
              <path d="M12 9v4m0 4h.01M10.3 3.9 2.4 17a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
            </svg>
            {t("stale_warning")}
          </div>
        </div>
      )}

      {/* Schedule banner — informs the user when the agents scan and
          where prices come from. Status, not warning: it sits quieter
          than the stale banner above so the two are never read as
          carrying the same weight. */}
      <div className="border-b border-council-200/70 dark:border-council-800/70 bg-council-50/60 dark:bg-council-900/40 text-[11px] text-council-600 dark:text-muted">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-1.5 text-center">
          {t("schedule_banner")}
        </div>
      </div>

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {children}
      </main>

      <footer className="border-t border-council-200/70 dark:border-council-800/70 py-5 text-center text-xs text-muted">
        <div className="mb-1">{t("footer")}</div>
        {(nextUsLabel || nextTaseLabel) && (
          <div className="text-[11px] text-muted">
            {t("next_us_update")}: {t("next_label")} {nextUsLabel}
            <span className="mx-1.5 opacity-40">·</span>
            {t("next_tase_update")}: {t("next_label")} {nextTaseLabel}
          </div>
        )}
      </footer>
    </div>
  );
}
