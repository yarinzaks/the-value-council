import { Card, EmptyState, PageTitle, PctCell } from "@/components/Cards";
import { loadCouncilOverview, loadJournal } from "@/lib/data";
import { metaLocalized, AGENTS } from "@/lib/agents";
import type { AgentSlug } from "@/lib/types";
import { isBuy } from "@/lib/types";
import { getServerI18n } from "@/lib/locale-server";

export const dynamic = "force-dynamic";

interface ConsensusEntry {
  ticker: string;
  buy_agents: AgentSlug[];
  watch_agents: AgentSlug[];
  total: number;
}

interface DivergenceEntry {
  ticker: string;
  buyer: AgentSlug;
  passers: AgentSlug[];
}

export default async function InsightsPage() {
  const { locale, t } = getServerI18n();
  const [overview, allDecisions] = await Promise.all([
    loadCouncilOverview(),
    // No limit. The tiles below print allDecisions.length as
    // "Decisions logged", so a cap rendered the cap itself as the
    // count: the page read exactly 5,000 against a real 28,533, and
    // "unique tickers" was computed from the same truncated slice.
    // Reading every file is what the journal page already does.
    loadJournal(),
  ]);

  if (overview.agents.length === 0) {
    return (
      <>
        <PageTitle title={t("insights_title")} subtitle={t("insights_subtitle")} />
        <EmptyState>{t("no_backtest_data")}</EmptyState>
      </>
    );
  }

  const latestDateByAgent = new Map<string, string>();
  for (const d of allDecisions) {
    const cur = latestDateByAgent.get(d.agent);
    const day = d.timestamp.split("T")[0];
    if (!cur || day > cur) latestDateByAgent.set(d.agent, day);
  }
  const currentDecisions = allDecisions.filter(
    (d) => d.timestamp.split("T")[0] === latestDateByAgent.get(d.agent),
  );

  const byTicker = new Map<string, { buy: Set<string>; watch: Set<string> }>();
  for (const d of currentDecisions) {
    const e = byTicker.get(d.ticker) ?? { buy: new Set(), watch: new Set() };
    if (isBuy(d.decision)) e.buy.add(d.agent);
    else if (d.decision === "WATCH") e.watch.add(d.agent);
    byTicker.set(d.ticker, e);
  }
  const consensus: ConsensusEntry[] = [];
  for (const [ticker, e] of byTicker.entries()) {
    const total = e.buy.size + e.watch.size;
    if (total >= 2) {
      consensus.push({
        ticker,
        buy_agents: Array.from(e.buy) as AgentSlug[],
        watch_agents: Array.from(e.watch) as AgentSlug[],
        total,
      });
    }
  }
  consensus.sort((a, b) => b.total - a.total || b.buy_agents.length - a.buy_agents.length);

  const divergence: DivergenceEntry[] = [];
  for (const [ticker, e] of byTicker.entries()) {
    if (e.buy.size === 1 && e.watch.size >= 1) {
      const buyer = Array.from(e.buy)[0] as AgentSlug;
      divergence.push({
        ticker,
        buyer,
        passers: Array.from(e.watch) as AgentSlug[],
      });
    }
  }
  divergence.sort((a, b) => b.passers.length - a.passers.length);

  const totalDecisions = allDecisions.length;
  const totalBuys = allDecisions.filter((d) => isBuy(d.decision)).length;
  const uniqueTickers = new Set(allDecisions.map((d) => d.ticker)).size;

  return (
    <>
      <PageTitle title={t("insights_title")} subtitle={t("insights_subtitle")} />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <Card>
          <div className="text-xs text-muted">{t("insights_decisions_logged")}</div>
          <div className="text-3xl font-semibold tabular">{totalDecisions.toLocaleString()}</div>
          <div className="text-xs text-muted mt-1">{totalBuys} {t("decision_buy")}</div>
        </Card>
        <Card>
          <div className="text-xs text-muted">{t("insights_unique_tickers")}</div>
          <div className="text-3xl font-semibold tabular">{uniqueTickers}</div>
        </Card>
        <Card>
          <div className="text-xs text-muted">{t("insights_council_alpha")}</div>
          <div className="text-3xl font-semibold">
            <PctCell value={overview.council_alpha_pct} />
          </div>
          <div className="text-xs text-muted mt-1">{t("vs_sp")} 500</div>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <h3 className="text-sm font-semibold mb-3">{t("insights_consensus_title")}</h3>
          {consensus.length === 0 ? (
            <p className="text-sm text-muted">{t("insights_consensus_empty")}</p>
          ) : (
            <ul className="divide-y divide-council-100 dark:divide-council-800">
              {consensus.slice(0, 12).map((c) => (
                <li key={c.ticker} className="py-2 flex items-center gap-3">
                  <span className="font-mono font-semibold tabular w-16">{c.ticker}</span>
                  <div className="flex flex-wrap gap-1">
                    {c.buy_agents.map((slug) => {
                      const m = metaLocalized(slug, locale);
                      return (
                        <span
                          key={`buy-${slug}`}
                          className="text-[11px] px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300 inline-flex items-center gap-1"
                        >
                          <span
                            className="inline-block w-1.5 h-1.5 rounded-full"
                            style={{ backgroundColor: m?.color ?? "#999" }}
                          />
                          {t("decision_buy")} · {m?.display ?? slug}
                        </span>
                      );
                    })}
                    {c.watch_agents.map((slug) => {
                      const m = metaLocalized(slug, locale);
                      return (
                        <span
                          key={`w-${slug}`}
                          className="text-[11px] px-2 py-0.5 rounded-full bg-yellow-100 dark:bg-yellow-900/40 text-yellow-800 dark:text-yellow-300 inline-flex items-center gap-1"
                        >
                          <span
                            className="inline-block w-1.5 h-1.5 rounded-full"
                            style={{ backgroundColor: m?.color ?? "#999" }}
                          />
                          {t("decision_watch")} · {m?.display ?? slug}
                        </span>
                      );
                    })}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <h3 className="text-sm font-semibold mb-3">{t("insights_divergence_title")}</h3>
          {divergence.length === 0 ? (
            <p className="text-sm text-muted">{t("insights_divergence_empty")}</p>
          ) : (
            <ul className="divide-y divide-council-100 dark:divide-council-800">
              {divergence.slice(0, 12).map((d) => {
                const buyerMeta = metaLocalized(d.buyer, locale);
                return (
                  <li key={d.ticker} className="py-2 flex items-center gap-3 flex-wrap">
                    <span className="font-mono font-semibold tabular w-16">{d.ticker}</span>
                    <span
                      className="text-[11px] px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300 inline-flex items-center gap-1"
                    >
                      <span
                        className="inline-block w-1.5 h-1.5 rounded-full"
                        style={{ backgroundColor: buyerMeta?.color ?? "#999" }}
                      />
                      {t("decision_buy")} · {buyerMeta?.display ?? d.buyer}
                    </span>
                    <span className="text-xs text-muted">{t("passed_label")}</span>
                    {d.passers.map((slug) => {
                      const m = metaLocalized(slug, locale);
                      return (
                        <span key={slug} className="text-[11px] text-muted">
                          {m?.display ?? slug}
                        </span>
                      );
                    })}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>

      <div className="mt-6">
        <Card>
          <h3 className="text-sm font-semibold mb-3">{t("insights_per_school")}</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            {AGENTS.map((a) => {
              const meta = metaLocalized(a.slug, locale);
              const buys = allDecisions.filter(
                (d) => d.agent === a.slug && isBuy(d.decision),
              ).length;
              const watches = allDecisions.filter(
                (d) => d.agent === a.slug && d.decision === "WATCH",
              ).length;
              return (
                <div key={a.slug}>
                  <div className="flex items-center gap-2 mb-1">
                    <span
                      className="inline-block w-2 h-2 rounded-full"
                      style={{ backgroundColor: a.color }}
                    />
                    <span className="font-medium">{meta?.display ?? a.slug}</span>
                  </div>
                  <div className="text-xs text-muted">
                    {t("insights_per_school_line")
                      .replace("{buys}", String(buys))
                      .replace("{watches}", String(watches))}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </div>
    </>
  );
}
