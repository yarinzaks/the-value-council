import { Card, EmptyState, PageTitle } from "@/components/Cards";
import { loadCompanyNames, loadWatchlist } from "@/lib/data";
import { metaLocalized } from "@/lib/agents";
import { getServerI18n } from "@/lib/locale-server";

export const dynamic = "force-dynamic";

export default async function WatchlistPage() {
  const { locale, t } = getServerI18n();
  const [entries, companyNames] = await Promise.all([
    loadWatchlist(),
    loadCompanyNames(),
  ]);
  if (entries.length === 0) {
    return (
      <>
        <PageTitle title={t("watchlist_title")} subtitle={t("watchlist_subtitle")} />
        <EmptyState>{t("drilldown_no_watchlist")}</EmptyState>
      </>
    );
  }

  return (
    <>
      <PageTitle title={t("watchlist_title")} subtitle={t("watchlist_subtitle")} />
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-council-500 border-b border-council-200 dark:border-council-800">
                <th className="py-2 pr-3">{t("col_ticker")}</th>
                <th className="py-2 pr-3">{t("col_watching")}</th>
                <th className="py-2 pr-3 text-right">{t("col_agents")}</th>
                <th className="py-2 pl-3 text-right">{t("col_last_update")}</th>
              </tr>
            </thead>
            <tbody>
              {entries.slice(0, 200).map((e) => (
                <tr
                  key={e.ticker}
                  className="border-b border-council-100 dark:border-council-800 last:border-b-0"
                >
                  <td className="py-2.5 pr-3">
                    <span className="font-mono font-medium tabular">
                      {e.ticker}
                    </span>
                    {/* A one-letter ticker like "G" or "M" is
                        unreadable on its own; the name is what makes
                        the row scannable. */}
                    {companyNames[e.ticker] && (
                      <span className="ms-2 text-xs font-normal text-council-500">
                        {companyNames[e.ticker]}
                      </span>
                    )}
                  </td>
                  <td className="py-2.5 pr-3">
                    <div className="flex flex-wrap gap-1.5">
                      {e.agents.map((a) => {
                        const meta = metaLocalized(a.slug, locale);
                        return (
                          <span
                            key={a.slug}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-council-50 dark:bg-council-800"
                            title={a.rationale ?? ""}
                          >
                            <span
                              className="inline-block w-1.5 h-1.5 rounded-full"
                              style={{ backgroundColor: meta?.color ?? "#999" }}
                            />
                            {meta?.display ?? a.slug}
                          </span>
                        );
                      })}
                    </div>
                  </td>
                  <td className="py-2.5 pr-3 text-right tabular">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs ${
                        e.agent_count >= 3
                          ? "bg-watch/20 text-watch"
                          : "bg-council-100 dark:bg-council-800 text-council-600 dark:text-council-300"
                      }`}
                    >
                      {e.agent_count}
                    </span>
                  </td>
                  <td className="py-2.5 pl-3 text-right text-xs text-council-500 tabular">
                    {e.most_recent.split("T")[0]}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
