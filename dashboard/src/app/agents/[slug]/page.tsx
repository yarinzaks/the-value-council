import { notFound } from "next/navigation";
import Link from "next/link";
import {
  Card,
  EmptyState,
  Money,
  PctCell,
  PageTitle,
} from "@/components/Cards";
import { NavChart } from "@/components/NavChart";
import { LivePositionsTable } from "@/components/LivePositionsTable";
import { AgentPerformanceChart } from "@/components/AgentPerformanceChart";
import { Term } from "@/components/Term";
import {
  loadAgentLatest,
  loadCompanyNames,
  loadJournal,
  loadLivePortfolio,
  loadSnapshots,
} from "@/lib/data";
import { AGENTS, metaLocalized } from "@/lib/agents";
import type { AgentSlug } from "@/lib/types";
import { getServerI18n } from "@/lib/locale-server";
import { translateRationale, translateTrigger } from "@/lib/translate-dynamic";
import { decisionLabel, narrative } from "@/lib/narrative";

export const dynamic = "force-dynamic";

export function generateStaticParams() {
  return AGENTS.map((a) => ({ slug: a.slug }));
}

export default async function AgentDrillPage({
  params,
}: {
  params: { slug: string };
}) {
  const { locale, t } = getServerI18n();
  const slug = params.slug as AgentSlug;
  const meta = metaLocalized(slug, locale);
  if (!meta) return notFound();

  const [run, recent, live, companyNames, snapshots] = await Promise.all([
    loadAgentLatest(slug),
    loadJournal({ agent: slug, limit: 30 }),
    loadLivePortfolio(slug),
    loadCompanyNames(),
    loadSnapshots(slug),
  ]);

  if (!run && !live) {
    return (
      <>
        <PageTitle title={meta.display} subtitle={meta.school_label} />
        <EmptyState>{t("drilldown_no_data")}</EmptyState>
      </>
    );
  }

  return (
    <>
      <PageTitle title={meta.display} subtitle={meta.school_label} />

      {live && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <Card>
              <div className="text-xs text-council-500">{t("col_nav")}</div>
              <div className="text-2xl font-semibold">
                <Money value={live.total_nav} />
              </div>
              <div className="text-xs text-council-500 mt-1">
                {t("seed_dollars").replace("{amount}", live.initial_cash.toLocaleString())}
              </div>
            </Card>
            <Card>
              <div className="text-xs text-council-500">{t("cash_available")}</div>
              <div className="text-2xl font-semibold">
                <Money value={live.cash} />
              </div>
              <div className="text-xs text-council-500 mt-1">
                {((live.cash / live.total_nav) * 100).toFixed(1)}% {t("pct_of_nav")}
              </div>
            </Card>
            <Card>
              <div className="text-xs text-council-500">{t("col_invested")}</div>
              <div className="text-2xl font-semibold">
                <Money value={live.invested} />
              </div>
              <div className="text-xs text-council-500 mt-1">
                {t("positions_count").replace("{n}", String(live.positions.length))}
              </div>
            </Card>
            <Card className={live.cumulative_return_pct > 0 ? "ring-1 ring-gain/30" : ""}>
              <div className="text-xs text-council-500">{t("total_pnl")}</div>
              <div className="text-2xl font-semibold">
                <Money value={live.total_nav - live.initial_cash} signed digits={2} />
              </div>
              <div className="text-xs mt-1">
                <PctCell value={live.cumulative_return_pct} />
              </div>
            </Card>
          </div>

          {/* Yahoo-Finance-style performance chart with period selector. */}
          {snapshots.length > 0 && (
            <div className="mb-6">
              <AgentPerformanceChart
                snapshots={snapshots.filter((s) =>
                  // Only live-era snapshots (year >= 2026 in our case)
                  // — older year-end backfilled rows mix scales weirdly.
                  s.date >= "2026-04-01"
                )}
                benchmark={run?.nav ?? []}
                agentColor={meta.color}
                agentName={meta.display}
              />
            </div>
          )}

          <Card className="mb-6">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold">{t("drilldown_live_positions")}</h3>
              <span className="text-xs text-council-500">
                {t("drilldown_updated_prefix")} {live.last_updated?.replace("T", " ").slice(0, 19) ?? "—"} UTC
              </span>
            </div>
            <LivePositionsTable
              positions={live.positions}
              agentSlug={slug}
              companyNames={companyNames}
            />
          </Card>

          {live.watchlist.length > 0 && (
            <Card className="mb-6">
              <h3 className="text-sm font-semibold mb-3">{t("drilldown_watchlist")}</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wider text-council-500 border-b border-council-200 dark:border-council-800">
                      <th className="py-2 pr-3">{t("col_ticker")}</th>
                      <th className="py-2 pr-3">{t("company_name")}</th>
                      <th className="py-2 pr-3 text-right">{t("col_rank_short")}</th>
                      <th className="py-2 pr-3 text-right">{t("entry_price_target")}</th>
                      <th className="py-2 pr-3 text-right">{t("distance_from_trigger")}</th>
                      <th className="py-2 pr-3">{t("col_trigger")}</th>
                      <th className="py-2 pl-3">{t("col_why")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {live.watchlist.slice(0, 30).map((w) => {
                      // Distance to trigger: only meaningful if entry_price_target
                      // is set and non-null. Sign indicates which side of the
                      // trigger the price currently sits.
                      const target = w.entry_price_target;
                      const distLabel = target !== null && target !== undefined
                        ? "—"
                        : "—";
                      return (
                        <tr
                          key={w.ticker}
                          className="border-b border-council-100 dark:border-council-800 last:border-b-0"
                        >
                          <td className="py-2 pr-3 font-mono font-medium tabular">
                            {w.ticker}
                          </td>
                          <td className="py-2 pr-3 text-xs text-council-700 dark:text-council-300">
                            {companyNames[w.ticker] ?? "—"}
                          </td>
                          <td className="py-2 pr-3 text-right tabular">
                            {w.current_rank ?? "—"}
                          </td>
                          <td className="py-2 pr-3 text-right tabular text-xs text-council-500">
                            {target !== null && target !== undefined
                              ? `$${target.toFixed(2)}`
                              : "—"}
                          </td>
                          <td className="py-2 pr-3 text-right tabular text-xs text-council-500">
                            {distLabel}
                          </td>
                          <td className="py-2 pr-3 text-xs text-council-600 dark:text-council-400">
                            {translateTrigger(w.entry_trigger, locale)}
                          </td>
                          <td className="py-2 pl-3 text-xs text-council-600 dark:text-council-400">
                            {translateRationale(
                              locale === "he" ? w.why_he : w.why_en,
                              locale,
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </>
      )}

      {run && (
        <>
          <Card className="mb-6">
            <h3 className="text-sm font-semibold mb-3">
              <Term k="cagr">{t("col_cagr")}</Term> {run.summary.strategy_metrics.cagr_pct.toFixed(2)}% &middot;
              <Term k="alpha"> α</Term> {(run.summary.strategy_metrics.cagr_pct - run.summary.benchmark_metrics.cagr_pct).toFixed(2)}%
              {" "}({run.summary.config.start_date} → {run.summary.config.end_date})
            </h3>
            <NavChart data={run.nav} agentColor={meta.color} />
          </Card>

          <Card className="mb-6">
            <h3 className="text-sm font-semibold mb-3">{t("drilldown_annual_returns")}</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wider text-council-500 border-b border-council-200 dark:border-council-800">
                    <th className="py-2 pr-3">{t("col_year")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_strategy")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_sp500")}</th>
                    <th className="py-2 pl-3 text-right">
                      <Term k="alpha">{t("col_alpha_short")}</Term>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {run.annual_returns.map((r) => (
                    <tr key={r.year} className="border-b border-council-100 dark:border-council-800 last:border-b-0">
                      <td className="py-2 pr-3 tabular">{r.year}</td>
                      <td className="py-2 pr-3 text-right">
                        <PctCell value={r.strategy_return_pct} />
                      </td>
                      <td className="py-2 pr-3 text-right">
                        <PctCell value={r.benchmark_return_pct} />
                      </td>
                      <td className="py-2 pl-3 text-right font-semibold">
                        <PctCell value={r.alpha_pct} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      <Card>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold">{t("drilldown_recent_decisions")}</h3>
          <Link
            href={`/journal?agent=${slug}`}
            className="text-xs text-council-500 hover:underline"
          >
            {t("view_all")}
          </Link>
        </div>
        {recent.length === 0 ? (
          <p className="text-sm text-council-500">{t("no_decisions")}</p>
        ) : (
          <ul className="divide-y divide-council-100 dark:divide-council-800">
            {recent.slice(0, 10).map((d, i) => {
              const story = narrative(d, locale, {
                companyName: companyNames[d.ticker] ?? "",
              });
              return (
                <li
                  key={`${d.ticker}-${d.timestamp}-${i}`}
                  className="py-2 flex items-start gap-3"
                >
                  <span
                    className={`text-xs font-semibold w-14 px-1.5 py-0.5 rounded text-center whitespace-nowrap mt-0.5 ${
                      d.decision === "BUY"
                        ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300"
                        : d.decision === "SELL"
                          ? "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300"
                          : "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300"
                    }`}
                  >
                    {decisionLabel(d.decision, locale)}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span className="font-medium tabular">{d.ticker}</span>
                      <span className="text-xs text-council-500">
                        {d.timestamp.split("T")[0]}
                      </span>
                    </div>
                    <p className="text-xs text-council-600 dark:text-council-400 mt-1 leading-relaxed">
                      {story}
                    </p>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </>
  );
}
