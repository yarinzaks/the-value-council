// The journal, rendered for one (agent, lifecycle) filter.
//
// Extracted from journal/page.tsx so the same rendering serves both the
// default view at /journal and the generated one at
// /journal/<agent>/<state>. Nothing about what it draws changed in the
// move; only where the two filter values come from.
//
// Why the filters became routes rather than a query string: a static
// export cannot read `searchParams` — there is no request to read it
// from — and the obvious alternative, shipping every card and hiding
// the unwanted ones in the browser, does not survive contact with the
// numbers. Unfiltered this page is 5.5 MB of HTML, because each card
// carries its full decision timeline. Filtered to "held" it is 1.6 MB.
// One page per combination keeps every page the size it is today.

import Link from "next/link";
import { Card, EmptyState, PageTitle } from "@/components/Cards";
import { PositionStoryCard } from "@/components/PositionStoryCard";
import { AGENTS, metaLocalized } from "@/lib/agents";
import { loadCompanyNames, loadJournal, loadLivePortfolio } from "@/lib/data";
import { getServerI18n } from "@/lib/locale-server";
import {
  TRADE_HISTORY_STARTS_AT,
  buildPositionStories,
  summarise,
  type Lifecycle,
  type PositionStory,
} from "@/lib/positions";
import type { AgentSlug } from "@/lib/types";

export const LIFECYCLES: Lifecycle[] = ["held", "closed", "never_opened"];

/** The value in a URL that means "do not filter on this axis". */
export const ANY = "all";

/**
 * Every filter combination the chips can reach.
 *
 * Twelve agent values (ten investors, market core, and "all") by four
 * lifecycle values. `generateStaticParams` in the route below returns
 * exactly this.
 */
export function filterCombinations(): Array<{ agent: string; state: string }> {
  const agents = [ANY, ...AGENTS.map((a) => a.slug)];
  const states = [ANY, ...LIFECYCLES];
  return agents.flatMap((agent) => states.map((state) => ({ agent, state })));
}

/** `/journal/all/held`, the path the default page also renders. */
export function journalPath(agent: string, state: string): string {
  return `/journal/${agent}/${state}`;
}

export async function JournalView({
  agent,
  state,
}: {
  /** An agent slug, or ANY. */
  agent: string;
  /** A lifecycle, or ANY. */
  state: string;
}) {
  const { locale, t } = getServerI18n();

  const agentSlug =
    agent !== ANY && AGENTS.some((a) => a.slug === agent)
      ? (agent as AgentSlug)
      : undefined;
  const stateFilter = LIFECYCLES.includes(state as Lifecycle)
    ? (state as Lifecycle)
    : undefined;

  const slugs: AgentSlug[] = agentSlug ? [agentSlug] : AGENTS.map((a) => a.slug);

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

  // Each chip changes one axis and preserves the other, which is what
  // the query-string version did.
  const to = (next: { agent?: string; state?: string }) =>
    journalPath(next.agent ?? agentSlug ?? ANY, next.state ?? stateFilter ?? ANY);

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
          {/* next/link, not a bare <a>. basePath only rewrites hrefs it
              owns, so a raw anchor here points at /journal/... and 404s
              on the static site, where every page lives under /he or
              /en. The query-string version had no basePath to miss. */}
          <Link href={to({ agent: ANY })} className={chip(!agentSlug)}>
            {t("filter_all")}
          </Link>
          {AGENTS.map((a) => (
            <Link
              key={a.slug}
              href={to({ agent: a.slug })}
              className={chip(agentSlug === a.slug)}
            >
              {metaLocalized(a.slug, locale)?.display ?? a.slug}
            </Link>
          ))}

          <span className="mx-2 text-council-300">|</span>
          <Link href={to({ state: ANY })} className={chip(!stateFilter)}>
            {t("filter_all")}
          </Link>
          {LIFECYCLES.map((s) => (
            <Link
              key={s}
              href={to({ state: s })}
              className={chip(stateFilter === s)}
            >
              {stateLabel[s]}{" "}
              <span className="tabular opacity-60">{stateCount[s]}</span>
            </Link>
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
