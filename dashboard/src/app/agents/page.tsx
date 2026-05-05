import Link from "next/link";
import { Card, EmptyState, NumCell, PctCell, PageTitle } from "@/components/Cards";
import { Term } from "@/components/Term";
import { loadCouncilOverview } from "@/lib/data";
import { metaLocalized } from "@/lib/agents";
import { getServerI18n } from "@/lib/locale-server";

export const dynamic = "force-dynamic";

export default async function AgentsPage() {
  const { locale, t } = getServerI18n();
  const overview = await loadCouncilOverview();
  if (overview.agents.length === 0) {
    return (
      <>
        <PageTitle title={t("nav_agents")} />
        <EmptyState>{t("no_backtest_data")}</EmptyState>
      </>
    );
  }
  return (
    <>
      <PageTitle title={t("nav_agents")} subtitle={t("agents_subtitle")} />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {overview.agents.map((a) => {
          const meta = metaLocalized(a.slug, locale);
          const m = a.summary.strategy_metrics;
          const b = a.summary.benchmark_metrics;
          const alpha = m.cagr_pct - b.cagr_pct;
          return (
            <Link key={a.slug} href={`/agents/${a.slug}`} className="block">
              <Card className="hover:ring-2 hover:ring-council-300 dark:hover:ring-council-600 transition-all h-full">
                <div className="flex items-center gap-2 mb-2">
                  <span
                    className="inline-block w-3 h-3 rounded-full"
                    style={{ backgroundColor: meta?.color ?? "#999" }}
                  />
                  <h3 className="font-semibold">{meta?.display ?? a.slug}</h3>
                </div>
                <div className="text-xs text-council-500 mb-4">{meta?.school_label}</div>
                <p className="text-sm text-council-600 dark:text-council-300 mb-4">
                  {meta?.description_label}
                </p>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <div className="text-xs text-council-500">
                      <Term k="cagr">{t("col_cagr")}</Term>
                    </div>
                    <div className="font-semibold">
                      <PctCell value={m.cagr_pct} />
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-council-500">
                      <Term k="alpha">{t("alpha_vs_sp")}</Term>
                    </div>
                    <div className="font-semibold">
                      <PctCell value={alpha} />
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-council-500">
                      <Term k="sharpe">{t("col_sharpe")}</Term>
                    </div>
                    <div>
                      <NumCell value={m.sharpe} />
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-council-500">
                      <Term k="max_dd">{t("col_max_dd")}</Term>
                    </div>
                    <div>
                      <PctCell value={m.max_drawdown_pct} />
                    </div>
                  </div>
                </div>
                <div className="mt-3 text-[11px] text-council-500">
                  {a.summary.config.start_date} → {a.summary.config.end_date} · {a.summary.n_trades} {t("col_trades").toLowerCase()}
                </div>
              </Card>
            </Link>
          );
        })}
      </div>
    </>
  );
}
