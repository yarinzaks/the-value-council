import { Card, EmptyState, PageTitle } from "@/components/Cards";
import { loadCouncilOverview } from "@/lib/data";
import { metaLocalized } from "@/lib/agents";
import { getServerI18n } from "@/lib/locale-server";

export const dynamic = "force-dynamic";

function alphaCellClass(alpha: number): string {
  if (Number.isNaN(alpha)) return "bg-council-100 dark:bg-council-800";
  const abs = Math.min(Math.abs(alpha), 50);
  const intensity = Math.round((abs / 50) * 9) * 100;
  if (alpha >= 0) {
    if (intensity <= 100) return "bg-green-50 dark:bg-green-900/20";
    if (intensity <= 300) return "bg-green-100 dark:bg-green-900/40";
    if (intensity <= 500) return "bg-green-200 dark:bg-green-800/60";
    if (intensity <= 700) return "bg-green-300 dark:bg-green-700/70";
    return "bg-green-400 dark:bg-green-600/80";
  }
  if (intensity <= 100) return "bg-red-50 dark:bg-red-900/20";
  if (intensity <= 300) return "bg-red-100 dark:bg-red-900/40";
  if (intensity <= 500) return "bg-red-200 dark:bg-red-800/60";
  if (intensity <= 700) return "bg-red-300 dark:bg-red-700/70";
  return "bg-red-400 dark:bg-red-600/80";
}

export default async function HeatmapPage() {
  const { locale, t } = getServerI18n();
  const overview = await loadCouncilOverview();

  if (overview.agents.length === 0) {
    return (
      <>
        <PageTitle title={t("heatmap_title")} />
        <EmptyState>{t("no_backtest_data")}</EmptyState>
      </>
    );
  }

  const yearSet = new Set<number>();
  for (const a of overview.agents) {
    for (const r of a.annual_returns) yearSet.add(r.year);
  }
  const years = Array.from(yearSet).sort();

  return (
    <>
      <PageTitle title={t("heatmap_title")} subtitle={t("heatmap_subtitle")} />
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-council-200 dark:border-council-800">
                <th className="py-2 pr-3 text-left font-medium">{t("col_agent")}</th>
                {years.map((y) => (
                  <th key={y} className="py-2 px-2 text-center text-xs font-medium tabular">
                    {y}
                  </th>
                ))}
                <th className="py-2 pl-3 text-right text-xs font-medium">{t("avg_alpha")}</th>
              </tr>
            </thead>
            <tbody>
              {overview.agents.map((a) => {
                const meta = metaLocalized(a.slug, locale);
                const byYear = new Map(a.annual_returns.map((r) => [r.year, r]));
                const alphas = a.annual_returns.map((r) => r.alpha_pct);
                const avgAlpha =
                  alphas.reduce((s, v) => s + v, 0) / Math.max(alphas.length, 1);
                return (
                  <tr key={a.slug} className="border-b border-council-100 dark:border-council-800 last:border-b-0">
                    <td className="py-2 pr-3">
                      <div className="flex items-center gap-2">
                        <span
                          className="inline-block w-2 h-2 rounded-full"
                          style={{ backgroundColor: meta?.color ?? "#999" }}
                        />
                        <span className="font-medium">{meta?.display ?? a.slug}</span>
                      </div>
                    </td>
                    {years.map((y) => {
                      const r = byYear.get(y);
                      if (!r) {
                        return (
                          <td
                            key={y}
                            className="py-2 px-2 text-center text-council-300 dark:text-council-700 tabular"
                          >
                            —
                          </td>
                        );
                      }
                      return (
                        <td
                          key={y}
                          className={`py-2 px-2 text-center text-xs tabular ${alphaCellClass(r.alpha_pct)}`}
                          title={`${t("col_strategy")} ${r.strategy_return_pct.toFixed(2)}% • ${t("col_sp500")} ${r.benchmark_return_pct.toFixed(2)}%`}
                        >
                          {r.alpha_pct >= 0 ? "+" : ""}
                          {r.alpha_pct.toFixed(1)}
                        </td>
                      );
                    })}
                    <td className="py-2 pl-3 text-right font-semibold tabular">
                      {avgAlpha >= 0 ? "+" : ""}
                      {avgAlpha.toFixed(2)}%
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="mt-4 flex items-center gap-4 text-xs text-muted flex-wrap">
          <span>{t("legend_color")}</span>
          <div className="flex items-center gap-1">
            <span className="inline-block w-4 h-4 bg-red-400 dark:bg-red-600/80 rounded" />
            <span>{t("legend_under_50")}</span>
          </div>
          <div className="flex items-center gap-1">
            <span className="inline-block w-4 h-4 bg-green-400 dark:bg-green-600/80 rounded" />
            <span>{t("legend_over_50")}</span>
          </div>
        </div>
      </Card>
    </>
  );
}
