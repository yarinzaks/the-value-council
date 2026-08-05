import { Card, EmptyState, PageTitle } from "@/components/Cards";
import { loadCompanyNames, loadJournal } from "@/lib/data";
import { metaLocalized, AGENTS } from "@/lib/agents";
import type { AgentSlug } from "@/lib/types";
import { getServerI18n } from "@/lib/locale-server";
import { translateExitTrigger } from "@/lib/translate-dynamic";
import { decisionLabel, narrative } from "@/lib/narrative";

export const dynamic = "force-dynamic";

interface SearchParamsShape {
  agent?: string;
  decision?: string;
}

export default async function JournalPage({
  searchParams,
}: {
  searchParams: SearchParamsShape;
}) {
  const { locale, t } = getServerI18n();
  const agentSlug = searchParams.agent as AgentSlug | undefined;
  // FILL/EXIT are the runner's executions; BUY/SELL are the strategy's
  // intent. Both are real records and both belong in the journal.
  const decisionFilter = searchParams.decision as
    | "BUY"
    | "SELL"
    | "WATCH"
    | "HOLD"
    | "FILL"
    | "EXIT"
    | undefined;

  const [decisions, companyNames] = await Promise.all([
    loadJournal({
      agent: agentSlug,
      decisions: decisionFilter ? [decisionFilter] : undefined,
      limit: 500,
    }),
    loadCompanyNames(),
  ]);

  return (
    <>
      <PageTitle title={t("journal_title")} subtitle={t("journal_subtitle")} />

      <Card className="mb-4">
        <div className="flex flex-wrap gap-3 items-center text-sm">
          <span className="text-xs uppercase tracking-wider text-council-500">
            {t("filter_agent")}
          </span>
          <a
            href="/journal"
            className={`px-2.5 py-1 rounded-full text-xs ${
              !agentSlug
                ? "bg-council-100 dark:bg-council-800"
                : "text-council-500 hover:bg-council-50 dark:hover:bg-council-800/50"
            }`}
          >
            {t("filter_all")}
          </a>
          {AGENTS.map((a) => {
            const meta = metaLocalized(a.slug, locale);
            return (
              <a
                key={a.slug}
                href={`/journal?agent=${a.slug}`}
                className={`px-2.5 py-1 rounded-full text-xs ${
                  agentSlug === a.slug
                    ? "bg-council-100 dark:bg-council-800"
                    : "text-council-500 hover:bg-council-50 dark:hover:bg-council-800/50"
                }`}
              >
                {meta?.display ?? a.slug}
              </a>
            );
          })}
          <span className="mx-2 text-council-300">|</span>
          <span className="text-xs uppercase tracking-wider text-council-500">
            {t("filter_type")}
          </span>
          {(["BUY", "FILL", "WATCH", "SELL", "EXIT"] as const).map((d) => (
            <a
              key={d}
              href={`/journal?${agentSlug ? `agent=${agentSlug}&` : ""}decision=${d}`}
              className={`px-2.5 py-1 rounded-full text-xs ${
                decisionFilter === d
                  ? "bg-council-100 dark:bg-council-800"
                  : "text-council-500 hover:bg-council-50 dark:hover:bg-council-800/50"
              }`}
            >
              {decisionLabel(d, locale)}
            </a>
          ))}
        </div>
      </Card>

      {decisions.length === 0 ? (
        <EmptyState>{t("no_journal_match")}</EmptyState>
      ) : (
        <div className="space-y-3">
          {decisions.map((d, idx) => {
            const meta = metaLocalized(d.agent, locale);
            const companyName = companyNames[d.ticker] ?? "";
            const story = narrative(d, locale, { companyName });
            return (
              <Card key={`${d.ticker}-${d.timestamp}-${d.agent}-${idx}`}>
                <div className="flex items-start gap-3">
                  <span
                    className={`text-xs font-semibold px-2 py-0.5 rounded whitespace-nowrap ${
                      d.decision === "BUY" || d.decision === "FILL"
                        ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300"
                        : d.decision === "SELL" || d.decision === "EXIT"
                          ? "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300"
                          : "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300"
                    }`}
                  >
                    {decisionLabel(d.decision, locale)}
                  </span>
                  <div className="flex-1">
                    <div className="flex items-baseline gap-3 flex-wrap">
                      <span className="font-mono font-semibold tabular">{d.ticker}</span>
                      {companyName && (
                        <span className="text-xs text-council-600 dark:text-council-400">
                          {companyName}
                        </span>
                      )}
                      <span className="text-xs text-council-500">
                        {d.timestamp.split("T")[0]}
                      </span>
                      <span className="inline-flex items-center gap-1 text-xs text-council-500">
                        <span
                          className="inline-block w-1.5 h-1.5 rounded-full"
                          style={{ backgroundColor: meta?.color ?? "#999" }}
                        />
                        {meta?.display ?? d.agent}
                      </span>
                    </div>
                    <p className="mt-2 text-sm leading-relaxed text-council-700 dark:text-council-300">
                      {story}
                    </p>
                    {d.exit_trigger && (
                      <div className="mt-2 text-xs text-council-500">
                        <span className="font-semibold">{t("exit_label")}</span>{" "}
                        {translateExitTrigger(d.exit_trigger, locale)}
                      </div>
                    )}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
}
