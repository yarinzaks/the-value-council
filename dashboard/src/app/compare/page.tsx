import { PageTitle, EmptyState } from "@/components/Cards";
import { BacktestCaveat } from "@/components/BacktestCaveat";
import { CompareView } from "@/components/CompareView";
import { loadCouncilOverview } from "@/lib/data";
import { getServerI18n } from "@/lib/locale-server";
import { metaLocalized } from "@/lib/agents";

// Static in the two-language export, where "force-dynamic" is a hard
// error; dynamic under a live server (next dev). NEXT_PUBLIC_* is
// inlined at build time, so this folds to a constant.
export const dynamic = process.env.NEXT_PUBLIC_SITE_LOCALE
  ? "force-static"
  : "force-dynamic";

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

      <BacktestCaveat title={t("caveat_title")} body={t("caveat_body")} />
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
