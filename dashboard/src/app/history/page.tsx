// History tab — last-7-days NAV chart per agent + daily trades table.

import { Card, EmptyState, Money, PctCell, PageTitle } from "@/components/Cards";
import { Term } from "@/components/Term";
import { HistoryNavChart } from "@/components/HistoryNavChart";
import { loadRecentSnapshots } from "@/lib/data";
import { AGENTS, metaLocalized } from "@/lib/agents";
import { getServerI18n } from "@/lib/locale-server";

export const dynamic = "force-dynamic";

const DAYS = 7;

export default async function HistoryPage() {
  const { locale, t } = getServerI18n();
  // Load 7-day windows for every agent.
  const histories = await Promise.all(
    AGENTS.map(async (a) => ({
      slug: a.slug,
      meta: metaLocalized(a.slug, locale)!,
      snapshots: await loadRecentSnapshots(a.slug, DAYS),
    })),
  );
  const haveData = histories.some((h) => h.snapshots.length > 0);
  if (!haveData) {
    return (
      <>
        <PageTitle title={t("nav_chart_title")} subtitle={t("history_subtitle")} />
        <EmptyState>{t("no_history")}</EmptyState>
      </>
    );
  }

  // Build a single chart-data series with one row per date and one
  // numeric column per agent.
  const dateSet = new Set<string>();
  for (const h of histories) for (const s of h.snapshots) dateSet.add(s.date);
  const dates = Array.from(dateSet).sort();
  // Percent change across the window, not absolute NAV.
  //
  // Plotted in dollars the chart is unreadable: Graham is at $12.8k
  // while the other nine sit between $10.2k and $11.3k, so the axis
  // spans $3.4k to fit one line and the 1-2% moves everybody came to
  // compare collapse into a single band at the bottom.
  //
  // Rebasing each agent to 0% on its first day in the window puts them
  // on one scale and makes the question the chart actually answers —
  // who moved, and how much, over these seven days — the thing you can
  // see. Absolute NAV is already in the per-agent tables below, and on
  // the overview.
  const chartData = dates.map((d) => {
    const row: Record<string, number | string> = { date: d };
    for (const h of histories) {
      const s = h.snapshots.find((s) => s.date === d);
      const base = h.snapshots[0]?.nav;
      if (s && base) row[h.meta.display] = ((s.nav / base - 1) * 100);
    }
    return row;
  });

  return (
    <>
      <PageTitle title={t("nav_chart_title")} subtitle={t("history_subtitle")} />

      <Card className="mb-6">
        <HistoryNavChart
          data={chartData}
          series={histories.map((h) => ({
            key: h.meta.display,
            color: h.meta.color,
          }))}
        />
      </Card>

      {histories.map((h) => {
        if (h.snapshots.length === 0) return null;
        // Day-by-day delta table for this agent.
        const rows: Array<{
          date: string;
          nav: number;
          nav_change_usd: number;
          nav_change_pct: number;
          buys: string[];
          sells: string[];
        }> = [];
        for (let i = 0; i < h.snapshots.length; i++) {
          const cur = h.snapshots[i];
          const prev = i > 0 ? h.snapshots[i - 1] : null;
          const navChangeUsd = prev ? cur.nav - prev.nav : 0;
          const navChangePct = prev && prev.nav > 0 ? (navChangeUsd / prev.nav) * 100 : 0;
          rows.push({
            date: cur.date,
            nav: cur.nav,
            nav_change_usd: navChangeUsd,
            nav_change_pct: navChangePct,
            buys: cur.buys,
            sells: cur.sells,
          });
        }
        return (
          <Card key={h.slug} className="mb-6">
            <div className="flex items-center gap-2 mb-3">
              <span
                className="inline-block w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: h.meta.color }}
              />
              <h3 className="text-sm font-semibold">{h.meta.display}</h3>
              <span className="text-xs text-muted">
                · {h.meta.school_label}
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wider text-muted border-b border-council-200 dark:border-council-800">
                    <th className="py-2 pr-3">{t("col_date")}</th>
                    <th className="py-2 pr-3 text-right">
                      <Term k="cagr">{t("col_nav")}</Term>
                    </th>
                    <th className="py-2 pr-3 text-right">{t("col_nav_change")}</th>
                    <th className="py-2 pr-3">{t("col_buys")}</th>
                    <th className="py-2 pl-3">{t("col_sells")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr
                      key={r.date}
                      className="border-b border-council-100 dark:border-council-800 last:border-b-0"
                    >
                      <td className="py-2 pr-3 tabular text-xs">{r.date}</td>
                      <td className="py-2 pr-3 text-right">
                        <Money value={r.nav} />
                      </td>
                      <td className="py-2 pr-3 text-right">
                        <span className="block">
                          <Money value={r.nav_change_usd} signed digits={2} />
                        </span>
                        <span className="block text-xs">
                          <PctCell value={r.nav_change_pct} />
                        </span>
                      </td>
                      <td className="py-2 pr-3">
                        {r.buys.length === 0 ? (
                          <span className="text-xs text-muted">—</span>
                        ) : (
                          <div className="flex flex-wrap gap-1">
                            {/* The ticker alone is not unique: a snapshot
                                can list the same name twice when the day
                                held more than one fill for it, which is
                                what philip_fisher/2026-08-07 does with
                                PAYC. Same shape as TodaysActivity. */}
                            {r.buys.map((tk, i) => (
                              <span
                                key={`${h.slug}-${r.date}-buy-${tk}-${i}`}
                                className="text-[11px] px-2 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300 font-mono"
                              >
                                {tk}
                              </span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="py-2 pl-3">
                        {r.sells.length === 0 ? (
                          <span className="text-xs text-muted">—</span>
                        ) : (
                          <div className="flex flex-wrap gap-1">
                            {r.sells.map((tk, i) => (
                              <span
                                key={`${h.slug}-${r.date}-sell-${tk}-${i}`}
                                className="text-[11px] px-2 py-0.5 rounded bg-red-100 dark:bg-red-900/40 text-red-800 dark:text-red-300 font-mono"
                              >
                                {tk}
                              </span>
                            ))}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        );
      })}
    </>
  );
}
