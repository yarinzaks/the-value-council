// Backtest tab — consolidated historical analytics.
//
// 1. Headline leaderboard (CAGR / α / Sharpe / Max DD)
// 2. Year-by-year alpha heatmap
// 3. Crisis stress tests for 2008, 2020, 2022 — pulls from each
//    agent's annual_returns; if a year is outside the backtest
//    window, we show "no data" rather than fake a number.

import Link from "next/link";
import { Card, EmptyState, NumCell, PctCell, PageTitle } from "@/components/Cards";
import { BacktestCaveat } from "@/components/BacktestCaveat";
import { Term } from "@/components/Term";
import { loadCouncilOverview } from "@/lib/data";
import { metaLocalized } from "@/lib/agents";
import type { AnnualReturn } from "@/lib/types";
import { getServerI18n } from "@/lib/locale-server";

// Static in the two-language export, where "force-dynamic" is a hard
// error; dynamic under a live server (next dev). NEXT_PUBLIC_* is
// inlined at build time, so this folds to a constant.
export const dynamic = process.env.NEXT_PUBLIC_SITE_LOCALE
  ? "force-static"
  : "force-dynamic";

const CRISIS_YEARS: Array<{ year: number; key: string }> = [
  { year: 2008, key: "crisis_2008" },
  { year: 2020, key: "crisis_2020" },
  { year: 2022, key: "crisis_2022" },
];

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

function findYear(returns: AnnualReturn[], year: number): AnnualReturn | undefined {
  return returns.find((r) => r.year === year);
}

export default async function BacktestPage() {
  const { locale, t } = getServerI18n();
  const overview = await loadCouncilOverview();
  if (overview.agents.length === 0) {
    return (
      <>
        <PageTitle title={t("backtest_tab_title")} subtitle={t("backtest_tab_subtitle")} />

        <EmptyState>{t("no_backtest_data")}</EmptyState>
      </>
    );
  }
  // Sort by alpha for the leaderboard.
  const sortedByAlpha = [...overview.agents].sort(
    (a, b) =>
      b.summary.strategy_metrics.cagr_pct - b.summary.benchmark_metrics.cagr_pct -
      (a.summary.strategy_metrics.cagr_pct - a.summary.benchmark_metrics.cagr_pct),
  );
  // Years for heatmap.
  const yearSet = new Set<number>();
  for (const a of overview.agents) for (const r of a.annual_returns) yearSet.add(r.year);
  const years = Array.from(yearSet).sort();

  return (
    <>
      <PageTitle title={t("backtest_tab_title")} subtitle={t("backtest_tab_subtitle")} />

      <BacktestCaveat title={t("caveat_title")} body={t("caveat_body")} />

      {/* Leaderboard ------------------------------------------------- */}
      <Card className="mb-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">{t("backtest_leaderboard")}</h2>
          <span className="text-xs text-muted">{t("ranked_by_alpha")}</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-muted border-b border-council-200 dark:border-council-800">
                <th className="py-2 pr-3">{t("col_agent")}</th>
                <th className="py-2 pr-3 text-right">{t("col_window")}</th>
                <th className="py-2 pr-3 text-right">
                  <Term k="cagr">{t("col_cagr")}</Term>
                </th>
                <th className="py-2 pr-3 text-right">{t("col_sp")}</th>
                <th className="py-2 pr-3 text-right">
                  <Term k="alpha">{t("col_alpha_short")}</Term>
                </th>
                <th className="py-2 pr-3 text-right">
                  <Term k="sharpe">{t("col_sharpe")}</Term>
                </th>
                <th className="py-2 pl-3 text-right">
                  <Term k="max_dd">{t("col_max_dd")}</Term>
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedByAlpha.map((a) => {
                const m = a.summary.strategy_metrics;
                const b = a.summary.benchmark_metrics;
                const alpha = m.cagr_pct - b.cagr_pct;
                const meta = metaLocalized(a.slug, locale);
                return (
                  <tr key={a.slug} className="border-b border-council-100 dark:border-council-800 last:border-b-0">
                    <td className="py-2.5 pr-3">
                      <Link href={`/agents/${a.slug}`} className="flex items-center gap-2 hover:underline">
                        <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: meta?.color ?? "#999" }} />
                        <span className="font-medium">{meta?.display ?? a.slug}</span>
                      </Link>
                    </td>
                    <td className="py-2.5 pr-3 text-right text-xs text-muted">
                      {a.summary.config.start_date} → {a.summary.config.end_date}
                    </td>
                    <td className="py-2.5 pr-3 text-right"><PctCell value={m.cagr_pct} /></td>
                    <td className="py-2.5 pr-3 text-right"><PctCell value={b.cagr_pct} /></td>
                    <td className="py-2.5 pr-3 text-right font-semibold"><PctCell value={alpha} /></td>
                    <td className="py-2.5 pr-3 text-right"><NumCell value={m.sharpe} /></td>
                    <td className="py-2.5 pl-3 text-right"><PctCell value={m.max_drawdown_pct} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Heatmap ----------------------------------------------------- */}
      <Card className="mb-6">
        {/* The cells are alpha, not return — Graham's 2024 cell reads
            -29.1 while his actual 2024 return was -4.22%. heatmap_title
            alone says "Annual Returns", so the subtitle that names alpha
            is not decoration here. */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">{t("heatmap_title")}</h2>
          <span className="text-xs text-muted">{t("heatmap_subtitle")}</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-council-200 dark:border-council-800">
                <th className="py-2 pr-3 text-left font-medium">{t("col_agent")}</th>
                {years.map((y) => (
                  <th key={y} className="py-2 px-2 text-center text-xs font-medium tabular">{y}</th>
                ))}
                <th className="py-2 pl-3 text-right text-xs font-medium">{t("avg_alpha")}</th>
              </tr>
            </thead>
            <tbody>
              {overview.agents.map((a) => {
                const meta = metaLocalized(a.slug, locale);
                const byYear = new Map(a.annual_returns.map((r) => [r.year, r]));
                const alphas = a.annual_returns.map((r) => r.alpha_pct);
                const avg = alphas.reduce((s, v) => s + v, 0) / Math.max(alphas.length, 1);
                return (
                  <tr key={a.slug} className="border-b border-council-100 dark:border-council-800 last:border-b-0">
                    <td className="py-2 pr-3">
                      <div className="flex items-center gap-2">
                        <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: meta?.color ?? "#999" }} />
                        <span className="font-medium">{meta?.display ?? a.slug}</span>
                      </div>
                    </td>
                    {years.map((y) => {
                      const r = byYear.get(y);
                      if (!r) return <td key={y} className="py-2 px-2 text-center text-council-300 dark:text-council-700 tabular">—</td>;
                      return (
                        <td key={y} className={`py-2 px-2 text-center text-xs tabular ${alphaCellClass(r.alpha_pct)}`}>
                          {r.alpha_pct >= 0 ? "+" : ""}{r.alpha_pct.toFixed(1)}
                        </td>
                      );
                    })}
                    <td className="py-2 pl-3 text-right font-semibold tabular">
                      {avg >= 0 ? "+" : ""}{avg.toFixed(2)}%
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Crisis stress tests ---------------------------------------- */}
      <Card>
        <h2 className="text-lg font-semibold mb-2">{t("crisis_test_title")}</h2>
        <p className="text-xs text-muted mb-4">{t("crisis_test_subtitle")}</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {CRISIS_YEARS.map((c) => (
            <div
              key={c.year}
              className="border border-council-200 dark:border-council-800 rounded-lg p-4"
            >
              <div className="text-xs uppercase tracking-wider text-muted mb-3">
                {t(c.key)}
              </div>
              <div className="space-y-2">
                {overview.agents.map((a) => {
                  const r = findYear(a.annual_returns, c.year);
                  const meta = metaLocalized(a.slug, locale);
                  if (!r) {
                    return (
                      <div key={a.slug} className="flex items-baseline justify-between text-sm">
                        <span className="flex items-center gap-1.5">
                          <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ backgroundColor: meta?.color ?? "#999" }} />
                          <span>{meta?.display ?? a.slug}</span>
                        </span>
                        <span className="text-xs text-muted">{t("crisis_no_data")}</span>
                      </div>
                    );
                  }
                  return (
                    <div key={a.slug}>
                      <div className="flex items-baseline justify-between text-sm">
                        <span className="flex items-center gap-1.5">
                          <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ backgroundColor: meta?.color ?? "#999" }} />
                          <span>{meta?.display ?? a.slug}</span>
                        </span>
                        <span className="font-semibold">
                          <PctCell value={r.strategy_return_pct} />
                        </span>
                      </div>
                      <div className="flex items-baseline justify-between text-[11px] text-muted ml-3">
                        <span>{t("crisis_benchmark_return")}</span>
                        <span><PctCell value={r.benchmark_return_pct} /></span>
                      </div>
                      <div className="flex items-baseline justify-between text-[11px] ml-3">
                        <span className="text-muted">
                          <Term k="alpha">{t("crisis_alpha")}</Term>
                        </span>
                        <span className="font-semibold"><PctCell value={r.alpha_pct} /></span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}
