// Decision journal, grouped by company.
//
// This was a flat feed of every decision row ever written — 1,240 of
// them for Benjamin Graham alone, in which ASGN appears 68 times, three
// times on one date. Read as transactions that is incomprehensible.
// They were never transactions: each row is the agent's verdict for
// that day, re-emitted on every run because the thesis does not change
// daily, and each carries shares=null and price=null.
//
// Grouped by ticker the same data reads as a short, honest story —
// "Graham flagged ASGN on 2026-05-06 and has reaffirmed it every
// trading day since" — and 1,240 rows become 41 cards. The raw feed is
// still one click away inside each card, where it belongs: as evidence,
// not as the front page.

import { Card, EmptyState, PageTitle } from "@/components/Cards";
import { PositionStoryCard } from "@/components/PositionStoryCard";
import { AGENTS, metaLocalized } from "@/lib/agents";
import {
  loadCompanyNames,
  loadJournal,
  loadLivePortfolio,
} from "@/lib/data";
import { getServerI18n } from "@/lib/locale-server";
import {
  TRADE_HISTORY_STARTS_AT,
  buildPositionStories,
  summarise,
  type Lifecycle,
  type PositionStory,
} from "@/lib/positions";
import type { AgentSlug } from "@/lib/types";

export const dynamic = "force-dynamic";

const LIFECYCLES: Lifecycle[] = ["held", "closed", "never_opened"];

interface SearchParamsShape {
  agent?: string;
  state?: string;
}

export default async function JournalPage({
  searchParams,
}: {
  searchParams: SearchParamsShape;
}) {
  const { locale, t } = getServerI18n();
  const agentSlug = searchParams.agent as AgentSlug | undefined;
  // Default to what is actually owned. Unfiltered this is 576 cards
  // across ten agents, which trades one kind of overwhelm for another;
  // "held" is 210 and every one of them is a live commitment. `?state=`
  // with any other value, including "all", turns the filter off.
  const stateParam = searchParams.state ?? "held";
  const stateFilter = LIFECYCLES.includes(stateParam as Lifecycle)
    ? (stateParam as Lifecycle)
    : undefined;

  const slugs: AgentSlug[] = agentSlug
    ? [agentSlug]
    : AGENTS.map((a) => a.slug);

  const [companyNames, ...perAgent] = await Promise.all([
    loadCompanyNames(),
    ...slugs.map(async (slug) => {
      const [decisions, portfolio] = await Promise.all([
        loadJournal({ agent: slug }),
        loadLivePortfolio(slug),
      ]);
      return buildPositionStories(slug, decisions, portfolio?.positions ?? []);
    }),
  ]);

  const all: PositionStory[] = perAgent.flat();
  const totals = summarise(all);
  const shown = stateFilter
    ? all.filter((s) => s.lifecycle === stateFilter)
    : all;

  const stateLabel: Record<Lifecycle, string> = {
    held: t("pos_held"),
    closed: t("pos_closed"),
    never_opened: t("pos_never"),
  };
  const stateCount: Record<Lifecycle, number> = {
    held: totals.held,
    closed: totals.closed,
    never_opened: totals.neverOpened,
  };

  const chip = (active: boolean) =>
    `px-2.5 py-1 rounded-full text-xs transition-colors ${
      active
        ? "bg-council-100 dark:bg-council-800"
        : "text-muted hover:bg-council-50 dark:hover:bg-council-800/50"
    }`;

  const qs = (next: { agent?: string | null; state?: string | null }) => {
    const p = new URLSearchParams();
    const agent = next.agent === undefined ? agentSlug : next.agent;
    const state = next.state === undefined ? stateFilter : next.state;
    if (agent) p.set("agent", agent);
    // "all" has to be explicit: an absent state now means "held".
    p.set("state", state ?? "all");
    return `/journal?${p.toString()}`;
  };

  return (
    <>
      <PageTitle title={t("pos_title")} subtitle={t("pos_subtitle")} />

      {/* What the grouping actually did, stated in numbers so the
          change is legible rather than just felt. */}
      <Card className="mb-4">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2 text-sm">
          <span className="text-council-700 dark:text-council-300">
            <span className="font-semibold tabular">
              {totals.decisionRows.toLocaleString()}
            </span>{" "}
            {t("pos_collapsed_from")}{" "}
            <span className="font-semibold tabular">{all.length}</span>{" "}
            {t("pos_cards")}
          </span>
          {totals.longestHeld && totals.longestHeld.days > 0 && (
            <span className="text-muted text-xs">
              {t("pos_longest")}:{" "}
              <span className="font-mono">{totals.longestHeld.ticker}</span>{" "}
              <span className="tabular">{totals.longestHeld.days}</span>{" "}
              {t("pos_days_affirmed")}
            </span>
          )}
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-muted">
          {t("pos_trades_from").replace("{date}", TRADE_HISTORY_STARTS_AT)}
        </p>
      </Card>

      <Card className="mb-4">
        <div className="flex flex-wrap gap-2 items-center text-sm">
          <span className="text-xs uppercase tracking-wider text-muted">
            {t("filter_agent")}
          </span>
          <a href={qs({ agent: null })} className={chip(!agentSlug)}>
            {t("filter_all")}
          </a>
          {AGENTS.map((a) => (
            <a
              key={a.slug}
              href={qs({ agent: a.slug })}
              className={chip(agentSlug === a.slug)}
            >
              {metaLocalized(a.slug, locale)?.display ?? a.slug}
            </a>
          ))}

          <span className="mx-2 text-council-300">|</span>
          <a href={qs({ state: null })} className={chip(!stateFilter)}>
            {t("filter_all")}
          </a>
          {LIFECYCLES.map((s) => (
            <a
              key={s}
              href={qs({ state: s })}
              className={chip(stateFilter === s)}
            >
              {stateLabel[s]}{" "}
              <span className="tabular opacity-60">{stateCount[s]}</span>
            </a>
          ))}
        </div>
      </Card>

      {shown.length === 0 ? (
        <EmptyState>{t("no_journal_match")}</EmptyState>
      ) : (
        <div className="space-y-3">
          {shown.map((story) => {
            const meta = metaLocalized(story.agent, locale);
            return (
              <PositionStoryCard
                key={`${story.agent}-${story.ticker}`}
                story={story}
                companyName={companyNames[story.ticker] ?? ""}
                agentDisplay={meta?.display ?? story.agent}
                agentColor={meta?.color ?? "#999"}
                t={t}
              />
            );
          })}
        </div>
      )}
    </>
  );
}
