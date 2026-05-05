import { PageTitle, EmptyState } from "@/components/Cards";
import { CompareView } from "@/components/CompareView";
import { loadCouncilOverview } from "@/lib/data";
import { getServerI18n } from "@/lib/locale-server";
import { metaLocalized } from "@/lib/agents";

export const dynamic = "force-dynamic";

export default async function ComparePage() {
  const { locale, t } = getServerI18n();
  const overview = await loadCouncilOverview();
  if (overview.agents.length === 0) {
    return (
      <>
        <PageTitle title={t("compare_title")} />
        <EmptyState>{t("no_backtest_data")}</EmptyState>
      </>
    );
  }
  return (
    <>
      <PageTitle title={t("compare_title")} subtitle={t("compare_subtitle")} />
      <CompareView
        agents={overview.agents.map((a) => ({
          slug: a.slug,
          display_name: metaLocalized(a.slug, locale)?.display ?? a.slug,
          metrics: a.summary.strategy_metrics,
          benchmark: a.summary.benchmark_metrics,
        }))}
      />
    </>
  );
}
