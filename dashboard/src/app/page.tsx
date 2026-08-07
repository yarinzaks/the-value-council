import Link from "next/link";
import {
  Card,
  EmptyState,
  MetricCard,
  Money,
  NumCell,
  PctCell,
  PageTitle,
} from "@/components/Cards";
import { AgentCardsRow } from "@/components/AgentCardsRow";
import { TodaysActivity } from "@/components/TodaysActivity";
import { Term } from "@/components/Term";
import {
  loadAgentDelta,
  loadCouncilLive,
  loadCouncilOverview,
} from "@/lib/data";
import { AGENTS, metaLocalized } from "@/lib/agents";
import type { AgentDailyDelta, AgentSlug } from "@/lib/types";
import { getServerI18n } from "@/lib/locale-server";

export const dynamic = "force-dynamic";

const MEDALS = ["🥇", "🥈", "🥉"];

export default async function OverviewPage() {
  const { locale, t } = getServerI18n();
  const [live, backtest, deltas] = await Promise.all([
    loadCouncilLive(),
    loadCouncilOverview(),
    Promise.all(AGENTS.map(async (a) => ({ slug: a.slug, delta: await loadAgentDelta(a.slug) }))),
  ]);
  const deltaBySlug = new Map<AgentSlug, AgentDailyDelta>();
  for (const d of deltas) if (d.delta) deltaBySlug.set(d.slug, d.delta);

  const haveLive = live.portfolios.length > 0;

  if (!haveLive && backtest.agents.length === 0) {
    return (
      <>
        <PageTitle title={t("nav_overview")} subtitle={t("overview_subtitle")} />
        <EmptyState>{t("no_live_data")}</EmptyState>
      </>
    );
  }

  // Sort live portfolios by NAV descending — most successful first
  // (used by the Live portfolios table).
  const livePortfolios = [...live.portfolios].sort((a, b) => b.total_nav - a.total_nav);

  // Build the ranking by total return % (cumulative_return_pct already
  // accounts for all P&L since seed). Best first.
  const ranking = [...live.portfolios].sort(
    (a, b) => b.cumulative_return_pct - a.cumulative_return_pct,
  );

  // Format USD with no fractional cents above $100.
  const fmtMoney = (v: number) =>
    `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", {
      minimumFractionDigits: Math.abs(v) < 100 ? 2 : 0,
      maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0,
    })}`;
  const fmtMoneyInt = (v: number) =>
    `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    })}`;

  return (
    <>
      <PageTitle title={t("app_title")} subtitle={t("overview_subtitle")} />

      {haveLive && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <MetricCard
              label={t("council_nav")}
              value={`$${live.council_nav.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
              delta={live.council_return_pct}
              deltaLabel={t("since_seed")}
              emphasis={live.council_pnl_usd >= 0}
            />
            <MetricCard
              label={t("council_cash")}
              value={fmtMoneyInt(live.council_cash)}
            />
            <MetricCard
              label={t("council_invested")}
              value={fmtMoneyInt(live.council_invested)}
            />
            <MetricCard
              label={t("council_pnl")}
              value={`${live.council_pnl_usd >= 0 ? "+" : "−"}$${Math.abs(live.council_pnl_usd).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
              emphasis={live.council_pnl_usd > 0}
            />
          </div>

          {/* 4 agent cards — NAV / cash / positions / today's P&L / watchlist. */}
          <AgentCardsRow
            cards={livePortfolios.map((p) => ({
              portfolio: p,
              delta: deltaBySlug.get(p.agent) ?? null,
            }))}
            locale={locale}
          />

          {/* Today's activity — what changed since yesterday per agent. */}
          <TodaysActivity
            rows={livePortfolios
              .map((p) => ({ slug: p.agent, delta: deltaBySlug.get(p.agent) }))
              .filter((r): r is { slug: AgentSlug; delta: AgentDailyDelta } => !!r.delta)}
            locale={locale}
          />

          {/* Live performance ranking — agents sorted by P&L %. */}
          <Card className="mb-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{t("ranking_title")}</h2>
              <span className="text-xs text-council-500">{t("ranking_subtitle")}</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wider text-council-500 border-b border-council-200 dark:border-council-800">
                    <th className="py-2 pr-3 w-16">{t("col_rank")}</th>
                    <th className="py-2 pr-3 w-12 text-center">{t("col_medal")}</th>
                    <th className="py-2 pr-3">{t("col_agent")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_nav")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_pnl_usd")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_pnl_pct")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_positions")}</th>
                    <th className="py-2 pl-3 text-right">{t("col_cash")}</th>
                  </tr>
                </thead>
                <tbody>
                  {ranking.map((p, i) => {
                    const meta = metaLocalized(p.agent, locale);
                    const pnl = p.total_nav - p.initial_cash;
                    const medal = MEDALS[i] ?? "";
                    const rankClass =
                      i === 0
                        ? "font-semibold"
                        : i === 3
                          ? "text-council-500"
                          : "";
                    return (
                      <tr
                        key={p.agent}
                        className={`border-b border-council-100 dark:border-council-800 last:border-b-0 hover:bg-council-50 dark:hover:bg-council-800/30 ${rankClass}`}
                      >
                        <td className="py-2.5 pr-3 tabular text-council-500">
                          #{i + 1}
                        </td>
                        <td className="py-2.5 pr-3 text-center text-2xl leading-none">
                          {medal}
                        </td>
                        <td className="py-2.5 pr-3">
                          <Link
                            href={`/agents/${p.agent}`}
                            className="flex items-center gap-2 hover:underline"
                          >
                            <span
                              className="inline-block w-2 h-2 rounded-full"
                              style={{ backgroundColor: meta?.color ?? "#999" }}
                            />
                            <span>{meta?.display ?? p.agent}</span>
                          </Link>
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <Money value={p.total_nav} />
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <Money value={pnl} signed digits={2} />
                        </td>
                        <td className="py-2.5 pr-3 text-right font-semibold">
                          <PctCell value={p.cumulative_return_pct} />
                        </td>
                        <td className="py-2.5 pr-3 text-right tabular">
                          {p.positions.length}
                        </td>
                        <td className="py-2.5 pl-3 text-right">
                          <Money value={p.cash} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          <Card className="mb-8">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{t("live_portfolios")}</h2>
              <span className="text-xs text-council-500">
                {t("live_portfolios_caption")}
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wider text-council-500 border-b border-council-200 dark:border-council-800">
                    <th className="py-2 pr-3">{t("col_agent")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_nav")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_cash")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_invested")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_pnl_usd")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_pnl_pct")}</th>
                    <th className="py-2 pr-3 text-right">{t("col_positions_short")}</th>
                    <th className="py-2 pl-3 text-right">{t("col_watch")}</th>
                  </tr>
                </thead>
                <tbody>
                  {livePortfolios.map((p) => {
                    const meta = metaLocalized(p.agent, locale);
                    const pnl = p.total_nav - p.initial_cash;
                    return (
                      <tr
                        key={p.agent}
                        className="border-b border-council-100 dark:border-council-800 last:border-b-0 hover:bg-council-50 dark:hover:bg-council-800/30"
                      >
                        <td className="py-2.5 pr-3">
                          <Link
                            href={`/agents/${p.agent}`}
                            className="flex items-center gap-2 hover:underline"
                          >
                            <span
                              className="inline-block w-2 h-2 rounded-full"
                              style={{ backgroundColor: meta?.color ?? "#999" }}
                            />
                            <span className="font-medium">
                              {meta?.display ?? p.agent}
                            </span>
                          </Link>
                          <div className="text-xs text-council-500 mt-0.5">
                            {meta?.school_label}
                          </div>
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <Money value={p.total_nav} />
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <Money value={p.cash} />
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <Money value={p.invested} />
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <Money value={pnl} signed />
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <PctCell value={p.cumulative_return_pct} />
                        </td>
                        <td className="py-2.5 pr-3 text-right tabular">
                          {p.positions.length}
                        </td>
                        <td className="py-2.5 pl-3 text-right tabular text-council-500">
                          {p.watchlist.length}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {livePortfolios[0]?.last_updated && (
              <div className="mt-3 text-xs text-council-500">
                {t("last_updated")}: {livePortfolios[0].last_updated.replace("T", " ").slice(0, 19)} UTC
              </div>
            )}
          </Card>
        </>
      )}

      {backtest.agents.length > 0 && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">{t("backtest_leaderboard")}</h2>
            <span className="text-xs text-council-500">
              {t("ranked_by_alpha")}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wider text-council-500 border-b border-council-200 dark:border-council-800">
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
                {backtest.agents
                  .slice()
                  .sort(
                    (a, b) =>
                      b.summary.strategy_metrics.cagr_pct -
                      b.summary.benchmark_metrics.cagr_pct -
                      (a.summary.strategy_metrics.cagr_pct -
                        a.summary.benchmark_metrics.cagr_pct),
                  )
                  .map((a) => {
                    const m = a.summary.strategy_metrics;
                    const b = a.summary.benchmark_metrics;
                    const alpha = m.cagr_pct - b.cagr_pct;
                    const meta = metaLocalized(a.slug, locale);
                    return (
                      <tr
                        key={a.slug}
                        className="border-b border-council-100 dark:border-council-800 last:border-b-0 hover:bg-council-50 dark:hover:bg-council-800/30"
                      >
                        <td className="py-2.5 pr-3">
                          <Link
                            href={`/agents/${a.slug}`}
                            className="flex items-center gap-2 hover:underline"
                          >
                            <span
                              className="inline-block w-2 h-2 rounded-full"
                              style={{ backgroundColor: meta?.color ?? "#999" }}
                            />
                            <span className="font-medium">{meta?.display ?? a.slug}</span>
                          </Link>
                        </td>
                        <td className="py-2.5 pr-3 text-right text-xs text-council-500">
                          {a.summary.config.start_date} → {a.summary.config.end_date}
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <PctCell value={m.cagr_pct} />
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <PctCell value={b.cagr_pct} />
                        </td>
                        <td className="py-2.5 pr-3 text-right font-semibold">
                          <PctCell value={alpha} />
                        </td>
                        <td className="py-2.5 pr-3 text-right">
                          <NumCell value={m.sharpe} />
                        </td>
                        <td className="py-2.5 pl-3 text-right">
                          <PctCell value={m.max_drawdown_pct} />
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
  );
}
