// "Today's activity" section on the Overview page.
//
// For each agent, shows: NAV change (USD + %), tickers bought today,
// tickers sold today. Pulled from the latest snapshot delta.

import Link from "next/link";
import { Card, Money, PctCell } from "./Cards";
import type { AgentDailyDelta, AgentSlug } from "@/lib/types";
import { metaLocalized } from "@/lib/agents";
import type { Locale } from "@/lib/i18n";
import { t as translate } from "@/lib/i18n";

interface Row {
  slug: AgentSlug;
  delta: AgentDailyDelta;
}

export function TodaysActivity({
  rows,
  locale,
}: {
  rows: Row[];
  locale: Locale;
}) {
  const t = (k: string) => translate(locale, k);
  if (rows.length === 0) return null;
  return (
    <Card className="mb-6">
      <h2 className="text-lg font-semibold mb-4">{t("todays_activity")}</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-muted border-b border-council-200 dark:border-council-800">
              <th className="py-2 pr-3">{t("col_agent")}</th>
              <th className="py-2 pr-3 text-right">{t("nav_change_today")}</th>
              <th className="py-2 pr-3">{t("bought_today")}</th>
              <th className="py-2 pl-3">{t("sold_today")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ slug, delta }) => {
              const meta = metaLocalized(slug, locale);
              const buys = delta.today.buys;
              const sells = delta.today.sells;
              const noActivity = buys.length === 0 && sells.length === 0;
              return (
                <tr
                  key={slug}
                  className="border-b border-council-100 dark:border-council-800 last:border-b-0"
                >
                  <td className="py-2.5 pr-3">
                    <Link
                      href={`/agents/${slug}`}
                      className="flex items-center gap-2 hover:underline"
                    >
                      <span
                        className="inline-block w-2 h-2 rounded-full"
                        style={{ backgroundColor: meta?.color ?? "#999" }}
                      />
                      <span className="font-medium">{meta?.display ?? slug}</span>
                    </Link>
                  </td>
                  <td className="py-2.5 pr-3 text-right">
                    <span className="block">
                      <Money value={delta.nav_change_usd} signed digits={2} />
                    </span>
                    <span className="block text-xs">
                      <PctCell value={delta.nav_change_pct} />
                    </span>
                  </td>
                  <td className="py-2.5 pr-3">
                    {buys.length === 0 ? (
                      <span className="text-xs text-muted">—</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {/* `buys` is a plain string[] and a ticker can
                            appear in it more than once — one agent
                            filling the same name twice in a day is a
                            rotation, not a bug. Keying on the ticker
                            alone made React treat the second PAYC as a
                            duplicate of the first, which it warns about
                            and which lets it drop or reorder chips. The
                            index is what actually distinguishes them;
                            the slug and action keep the key readable if
                            these lists are ever merged. */}
                        {buys.map((tk, i) => (
                          <span
                            key={`${slug}-buy-${tk}-${i}`}
                            className="text-[11px] px-2 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300 font-mono"
                          >
                            {tk}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="py-2.5 pl-3">
                    {sells.length === 0 ? (
                      <span className="text-xs text-muted">—</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {sells.map((tk, i) => (
                          <span
                            key={`${slug}-sell-${tk}-${i}`}
                            className="text-[11px] px-2 py-0.5 rounded bg-red-100 dark:bg-red-900/40 text-red-800 dark:text-red-300 font-mono"
                          >
                            {tk}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
