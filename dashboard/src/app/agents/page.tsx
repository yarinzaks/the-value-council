import Link from "next/link";
import { Card, EmptyState, Money, NumCell, PctCell, PageTitle } from "@/components/Cards";
import { Term } from "@/components/Term";
import { loadCouncilOverview, loadCouncilState } from "@/lib/data";
import { metaLocalized } from "@/lib/agents";
import { getServerI18n } from "@/lib/locale-server";

// Static in the two-language export, where "force-dynamic" is a hard
// error; dynamic under a live server (next dev). NEXT_PUBLIC_* is
// inlined at build time, so this folds to a constant.
export const dynamic = process.env.NEXT_PUBLIC_SITE_LOCALE
  ? "force-static"
  : "force-dynamic";

export default async function AgentsPage() {
  const { locale, t } = getServerI18n();
  // The Council keeps a book but publishes no backtest, so it is absent
  // from `overview.agents` and needs its own card. It is on this page
  // rather than only behind its own tab because leaving it off made it
  // read as missing rather than as unscored.
  const [overview, council] = await Promise.all([
    loadCouncilOverview(),
    loadCouncilState(),
  ]);
  const councilMeta = metaLocalized("the_council", locale);
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
                <div className="text-xs text-muted mb-4">{meta?.school_label}</div>
                <p className="text-sm text-council-600 dark:text-council-300 mb-4">
                  {meta?.description_label}
                </p>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <div className="text-xs text-muted">
                      <Term k="cagr">{t("col_cagr")}</Term>
                    </div>
                    <div className="font-semibold">
                      <PctCell value={m.cagr_pct} />
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-muted">
                      <Term k="alpha">{t("alpha_vs_sp")}</Term>
                    </div>
                    <div className="font-semibold">
                      <PctCell value={alpha} />
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-muted">
                      <Term k="sharpe">{t("col_sharpe")}</Term>
                    </div>
                    <div>
                      <NumCell value={m.sharpe} />
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-muted">
                      <Term k="max_dd">{t("col_max_dd")}</Term>
                    </div>
                    <div>
                      <PctCell value={m.max_drawdown_pct} />
                    </div>
                  </div>
                </div>
                <div className="mt-3 text-[11px] text-muted">
                  {a.summary.config.start_date} → {a.summary.config.end_date} · {a.summary.n_trades} {t("col_trades").toLowerCase()}
                </div>
              </Card>
            </Link>
          );
        })}

        {/* Same grid, same card, different numbers — because the ones
            above are backtest figures and this agent has none. Showing
            it a CAGR column with a dash would invite the comparison the
            dash is there to refuse; showing what it actually does says
            more. */}
        {council && councilMeta && (
          <Link href="/council" className="block">
            <Card className="hover:ring-2 hover:ring-council-300 dark:hover:ring-council-600 transition-all h-full">
              <div className="flex items-center gap-2 mb-2">
                <span
                  className="inline-block w-3 h-3 rounded-full"
                  style={{ backgroundColor: councilMeta.color }}
                />
                <h3 className="font-semibold">{councilMeta.display}</h3>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-council-100 dark:bg-council-800 text-muted">
                  {t("not_backtested")}
                </span>
              </div>
              <div className="text-xs text-muted mb-4">
                {councilMeta.school_label}
              </div>
              <p className="text-sm text-council-600 dark:text-council-300 mb-4">
                {councilMeta.description_label}
              </p>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <div className="text-xs text-muted">{t("col_nav")}</div>
                  <div className="font-semibold">
                    <Money value={council.nav} />
                  </div>
                </div>
                <div>
                  <div className="text-xs text-muted">{t("col_cash")}</div>
                  <div className="font-semibold">
                    {(council.cash_weight * 100).toFixed(0)}%
                  </div>
                </div>
                <div>
                  <div className="text-xs text-muted">{t("col_positions")}</div>
                  <div>{council.positions}</div>
                </div>
                <div>
                  <div className="text-xs text-muted">{t("punch_card")}</div>
                  <div>
                    {council.journal.punch_card.remaining}/
                    {council.journal.punch_card.total}
                  </div>
                </div>
              </div>
              <div className="mt-3 text-[11px] text-muted">
                {t("council_card_note")}
              </div>
            </Card>
          </Link>
        )}
      </div>
    </>
  );
}
