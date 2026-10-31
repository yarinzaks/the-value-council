import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
import { ChatWidget } from "@/components/ChatWidget";
import { Providers } from "@/components/Providers";
import { loadCouncilLive } from "@/lib/data";
import { readLocale } from "@/lib/locale-server";
import {
  formatAbsolute,
  isStale,
  nextScheduledLabel,
} from "@/lib/timestamps";

export const metadata: Metadata = {
  title: "The Value Council",
  description:
    "Ten AI agents modeled after legendary value investors. Live alpha tracking + decision journal.",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Read locale from cookie at SSR time so the first paint is in the
  // correct language and direction — no flash from English to Hebrew.
  const locale = readLocale();
  const dir = locale === "he" ? "rtl" : "ltr";

  // Fetch the most recent live-portfolio sync once per request and
  // pass it down to AppShell. Cheap (<10ms) — just a few JSON reads.
  const live = await loadCouncilLive();
  const mostRecent = live.portfolios.reduce(
    (acc, p) => (p.last_updated > acc ? p.last_updated : acc),
    "",
  );
  const now = new Date();
  // The *instant*, not a label. `now` here is build time in a static
  // export, so a relative label ("today 13:31") freezes and then lies
  // — the published site said "today" for two and a half months. The
  // shell renders the absolute stamp and upgrades it to a relative one
  // in the browser, against the reader's own clock.
  const lastSyncAbsolute = mostRecent ? formatAbsolute(mostRecent, locale) : "";
  const stale = mostRecent ? isStale(mostRecent, 24, now) : true;
  const nextUsLabel = nextScheduledLabel("US", locale, now);
  const nextTaseLabel = nextScheduledLabel("TASE", locale, now);

  return (
    <html lang={locale} dir={dir} suppressHydrationWarning>
      <body>
        <Providers initialLocale={locale}>
          <AppShell
            lastSyncIso={mostRecent}
            lastSyncAbsolute={lastSyncAbsolute}
            stale={stale}
            nextUsLabel={nextUsLabel}
            nextTaseLabel={nextTaseLabel}
          >
            {children}
          </AppShell>
          <ChatWidget />
        </Providers>
      </body>
    </html>
  );
}
