// One company, one card — the whole arc instead of a row per day.
//
// Two things are on it: what moved, and what the agent is waiting
// for. The daily verdict rows are neither. A position held 68
// trading days produced 68 of them, all saying the same thing, and
// they buried the two days that actually mattered.

import Link from "next/link";

import { Card, PctCell } from "@/components/Cards";
import {
  movements,
  parseConditions,
  type Evidence,
  type PositionStory,
} from "@/lib/positions";

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
  t: (key: string) => string;
}

export function PositionStoryCard({
  story,
  companyName,
  agentDisplay,
  agentColor,
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

  const conditions = parseConditions(story.criteriaMet);
  const events = movements(story);

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

          {/* What would make him sell, and how close it is.
              The criteria strings carry both the current reading and
              the level that breaks it, so this is the part a reader can
              learn from: not "Graham likes cheap stocks" but "this one
              goes if P/E doubles, and it is halfway there." */}
          {conditions.length > 0 && (
            <div className="mt-3">
              <div className="text-[11px] uppercase tracking-wider text-muted mb-1.5">
                {t("pos_watching")}
              </div>
              <div className="space-y-1">
                {conditions.map((c) =>
                  c.used === null ? (
                    <div key={c.raw} className="text-xs text-council-600 dark:text-muted">
                      · {c.label}
                    </div>
                  ) : (
                    <div key={c.raw} className="flex items-center gap-2 text-xs">
                      <span className="w-28 shrink-0 text-council-700 dark:text-council-300">
                        {c.label}
                      </span>
                      <span className="tabular font-mono w-14 shrink-0 text-end">
                        {c.value}
                      </span>
                      {/* Fill shows room consumed, so a nearly-full bar
                          is a position close to breaking its own rule. */}
                      <span className="flex-1 h-1.5 rounded-full bg-council-100 dark:bg-council-800 overflow-hidden min-w-[3rem]">
                        <span
                          className={`block h-full rounded-full ${
                            c.used >= 0.9
                              ? "bg-loss"
                              : c.used >= 0.7
                                ? "bg-amber-500"
                                : "bg-gain"
                          }`}
                          style={{ width: `${Math.min(100, c.used * 100)}%` }}
                        />
                      </span>
                      <span className="tabular text-muted w-20 shrink-0">
                        {c.op} {c.threshold}
                      </span>
                    </div>
                  ),
                )}
              </div>
              {story.exitTrigger && (
                <p className="mt-2 text-[11px] leading-relaxed text-muted">
                  <span className="font-semibold">{t("exit_label")}</span>{" "}
                  {story.exitTrigger}
                </p>
              )}
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

      {/* Movements only. The rows are one per run, so a position held
          68 trading days produced 68 of them — a list that said "still
          holding" sixty-eight times and buried the two days that
          mattered. An open position that has not changed has one
          entry, which is the truth about it. */}
      {events.length > 0 && (
        <ol className="mt-3 space-y-1.5 border-s-2 border-council-100 dark:border-council-800 ps-3">
          {events.map((m) => (
            <li key={`${m.date}-${m.kind}`} className="text-xs">
              <span className="tabular text-muted">{m.date}</span>{" "}
              <span
                className={`font-medium ${
                  m.kind === "exited"
                    ? "text-loss"
                    : "text-council-700 dark:text-council-300"
                }`}
              >
                {t(`pos_event_${m.kind}`)}
              </span>
              {m.note && (
                <p className="mt-0.5 text-council-600 dark:text-muted leading-relaxed">
                  {m.note}
                </p>
              )}
            </li>
          ))}
          {story.lifecycle === "held" && (
            <li className="text-xs text-muted">
              {t("pos_still_held").replace(
                "{days}",
                String(story.daysAffirmed),
              )}
            </li>
          )}
        </ol>
      )}
    </Card>
  );
}
