// One company, one card — the whole arc instead of a row per day.
//
// The timeline is a native <details>, so expanding costs no client
// JavaScript and the page still works with scripts disabled.

import Link from "next/link";

import { Card, PctCell } from "@/components/Cards";
import type { Locale } from "@/lib/i18n";
import { decisionLabel, narrative } from "@/lib/narrative";
import type { Evidence, PositionStory } from "@/lib/positions";

const LIFECYCLE_STYLE = {
  held: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  closed: "bg-council-100 text-council-700 dark:bg-council-800 dark:text-council-300",
  never_opened:
    "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
} as const;

function money(v: number | null): string {
  return v === null ? "—" : `$${v.toFixed(2)}`;
}

interface Props {
  story: PositionStory;
  companyName: string;
  agentDisplay: string;
  agentColor: string;
  locale: Locale;
  t: (key: string) => string;
}

export function PositionStoryCard({
  story,
  companyName,
  agentDisplay,
  agentColor,
  locale,
  t,
}: Props) {
  const lifecycleLabel =
    story.lifecycle === "held"
      ? t("pos_held")
      : story.lifecycle === "closed"
        ? t("pos_closed")
        : t("pos_never");

  const evidenceLabel: Record<Evidence, string> = {
    position: t("pos_evidence_position"),
    trade: t("pos_evidence_trade"),
    first_flagged: t("pos_evidence_flagged"),
  };

  return (
    <Card>
      <div className="flex items-start gap-3 flex-wrap">
        <span
          className={`text-xs font-semibold px-2 py-0.5 rounded whitespace-nowrap ${LIFECYCLE_STYLE[story.lifecycle]}`}
        >
          {lifecycleLabel}
        </span>

        <div className="flex-1 min-w-[16rem]">
          <div className="flex items-baseline gap-3 flex-wrap">
            {/* The detail page already carries the per-position "why"
                and the exit trigger; the card is the index into it. */}
            <Link
              href={`/agents/${story.agent}/positions/${story.ticker}`}
              className="font-mono font-semibold tabular text-base hover:underline underline-offset-2"
            >
              {story.ticker}
            </Link>
            {companyName && (
              <span className="text-xs text-council-600 dark:text-muted">
                {companyName}
              </span>
            )}
            <span className="inline-flex items-center gap-1 text-xs text-muted">
              <span
                className="inline-block w-1.5 h-1.5 rounded-full"
                style={{ backgroundColor: agentColor }}
              />
              {agentDisplay}
            </span>
          </div>

          {/* The headline reframe: 68 rows is not 68 purchases, it is
              68 days of the same answer. */}
          <p className="mt-1.5 text-sm text-council-700 dark:text-council-300">
            <span className="font-semibold tabular">{story.daysAffirmed}</span>{" "}
            {t("pos_days_affirmed")}
            {story.firstFlagged && (
              <>
                {" · "}
                {t("pos_since")}{" "}
                <span className="tabular">{story.firstFlagged}</span>
              </>
            )}
          </p>

          {story.criteriaMet.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {story.criteriaMet.slice(0, 6).map((c) => (
                <span
                  key={c}
                  className="text-[11px] px-1.5 py-0.5 rounded bg-council-50 dark:bg-council-800/60 text-council-600 dark:text-muted"
                >
                  {c}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Numbers, right-aligned and tabular so columns line up down
            the page even though each card is independent. */}
        <div className="text-xs text-council-600 dark:text-muted text-right tabular min-w-[9rem] space-y-0.5">
          <div>
            {t("pos_entry")}{" "}
            <span className="font-mono">{money(story.entryPrice)}</span>
          </div>
          {story.currentPrice !== null && (
            <div>
              {t("pos_now")}{" "}
              <span className="font-mono">{money(story.currentPrice)}</span>
            </div>
          )}
          {story.pnlPct !== null && (
            <div className="text-sm">
              <PctCell value={story.pnlPct} />
            </div>
          )}
          {story.weightPct !== null && (
            <div className="text-muted">
              {t("pos_weight")} {story.weightPct.toFixed(1)}%
            </div>
          )}
          {story.openedAt && story.openedEvidence && (
            <div
              className="text-[11px] text-muted"
              title={evidenceLabel[story.openedEvidence]}
            >
              {story.openedAt}
              {story.openedEvidence !== "position" && " ~"}
            </div>
          )}
        </div>
      </div>

      {story.timeline.length > 0 && (
        <details className="mt-3 group">
          <summary className="cursor-pointer text-xs text-muted hover:text-council-700 dark:hover:text-council-300 select-none">
            {t("pos_show_timeline")} ({story.timeline.length})
          </summary>
          <p className="mt-2 text-[11px] text-muted leading-relaxed">
            {t("pos_timeline_note")}
          </p>
          <ol className="mt-2 space-y-1.5 border-s-2 border-council-100 dark:border-council-800 ps-3">
            {story.timeline.map((d, i) => (
              <li
                key={`${d.timestamp}-${i}`}
                className="text-xs text-council-600 dark:text-muted"
              >
                <span className="tabular text-muted">
                  {d.timestamp.slice(0, 10)}
                </span>{" "}
                <span className="font-medium">
                  {decisionLabel(d.decision, locale)}
                </span>
                {i === 0 && (
                  <span className="ms-2 text-muted">
                    {narrative(d, locale, { companyName })}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}
    </Card>
  );
}
