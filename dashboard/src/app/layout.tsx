import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
import { Providers } from "@/components/Providers";
import { loadCouncilLive } from "@/lib/data";
import { readLocale } from "@/lib/locale-server";
import {
  formatRelativeTime,
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
  const lastSyncLabel = mostRecent ? formatRelativeTime(mostRecent, locale, now) : "";
  const stale = mostRecent ? isStale(mostRecent, 24, now) : true;
  const nextUsLabel = nextScheduledLabel("US", locale, now);
  const nextTaseLabel = nextScheduledLabel("TASE", locale, now);

  return (
    <html lang={locale} dir={dir} suppressHydrationWarning>
      <body>
        <Providers initialLocale={locale}>
          <AppShell
            lastSyncLabel={lastSyncLabel}
            stale={stale}
            nextUsLabel={nextUsLabel}
            nextTaseLabel={nextTaseLabel}
          >
            {children}
          </AppShell>
        </Providers>
      </body>
    </html>
  );
}
