import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
import { Providers } from "@/components/Providers";
import { readLocale } from "@/lib/locale-server";

export const metadata: Metadata = {
  title: "The Value Council",
  description:
    "Ten AI agents modeled after legendary value investors. Live alpha tracking + decision journal.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // Read locale from cookie at SSR time so the first paint is in the
  // correct language and direction — no flash from English to Hebrew.
  const locale = readLocale();
  const dir = locale === "he" ? "rtl" : "ltr";
  return (
    <html lang={locale} dir={dir} suppressHydrationWarning>
      <body>
        <Providers initialLocale={locale}>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
