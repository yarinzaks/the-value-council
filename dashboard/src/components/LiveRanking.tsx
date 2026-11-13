"use client";

// The live performance ranking, over a window the reader picks.
//
// The table ranked by return since seed and nothing else, so "who had
// the best week" and "who is ahead since the August fix" had no answer
// on the page. The window math is lib/ranking-window.ts; this is the
// pills and the table.

import Link from "next/link";
import { useMemo, useState } from "react";
import { Card, Money, PctCell } from "./Cards";
import { useUI } from "./Providers";
import { metaLocalized } from "@/lib/agents";
import {
  RANKING_PERIODS,
  rankOverWindow,
  type RankingPeriod,
} from "@/lib/ranking-window";

const MEDALS = ["🥇", "🥈", "🥉"];

interface BookNow {
  agent: string;
  nav: number;
  cash: number;
  positions: number;
  /** The cash the book opened with; the ALL window measures from it. */
  seed: number;
}

interface Props {
  /** `[date, nav]` per session, per non-control book. */
  navHistory: Record<string, [string, number][]>;
  /** The books as they stand, for the columns a window does not change. */
  books: BookNow[];
}

export function LiveRanking({ navHistory, books }: Props) {
  const { locale, t } = useUI();
  const [period, setPeriod] = useState<RankingPeriod>("ALL");

  const rows = useMemo(() => {
    const by = new Map(
      Object.entries(navHistory).map(([agent, pts]) => [
        agent,
        pts.map(([date, nav]) => ({ date, nav })),
      ]),
    );
    const seeds = new Map(books.map((b) => [b.agent, b.seed]));
    return rankOverWindow(by, period, () => false, (a) => seeds.get(a));
  }, [navHistory, period, books]);

  const now = useMemo(() => new Map(books.map((b) => [b.agent, b])), [books]);

  // The window as the table actually measured it: the earliest start
  // and the latest end across the books, so a reader sees the dates
  // the numbers come from rather than the dates they asked for.
  const from = rows.length ? rows.map((r) => r.startDate).sort()[0] : null;
  const to = rows.length ? rows.map((r) => r.endDate).sort().at(-1) : null;
  const clipped = rows.filter((r) => r.clipped).length;

  return (
    <Card className="mb-6">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-3">
        <h2 className="text-lg font-semibold">{t("ranking_title")}</h2>
        <div className="flex gap-1 bg-council-100 dark:bg-council-800 rounded-full p-1">
          {RANKING_PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              aria-pressed={period === p}
              className={`pressable focus-ring px-3 py-1 text-xs font-medium rounded-full ${
                period === p
                  ? "bg-white dark:bg-council-700 shadow-elev-1 text-council-900 dark:text-council-100"
                  : "text-muted hover:text-council-700 dark:hover:text-council-300"
              }`}
            >
              {t(`ranking_period_${p}`)}
            </button>
          ))}
        </div>
      </div>
      <p className="text-xs text-muted mb-4">
        {period === "ALL" ? (
          t("ranking_subtitle")
        ) : (
          <>
            {t("ranking_window_from")}
            {locale === "he" ? "" : " "}
            <span className="tabular" dir="ltr">{from}</span> {t("ranking_window_to")}{" "}
            <span className="tabular" dir="ltr">{to}</span>
            {clipped > 0 && (
              <>
                {" · "}
                {clipped} {t("ranking_clipped")}
              </>
            )}
          </>
        )}
        {period === "FIX" && (
          <>
            <br />
            {t("ranking_fix_note")}
          </>
        )}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-muted border-b border-council-200 dark:border-council-800">
              <th className="py-2 pr-3 w-16">{t("col_rank")}</th>
              <th className="py-2 pr-3 w-12 text-center">{t("col_medal")}</th>
              <th className="py-2 pr-3">{t("col_agent")}</th>
              <th className="py-2 pr-3 text-right">{t("col_nav")}</th>
              <th className="py-2 pr-3 text-right">
                {period === "ALL" ? t("col_pnl_usd") : t("col_window_pnl")}
              </th>
              <th className="py-2 pr-3 text-right">
                {period === "ALL" ? t("col_pnl_pct") : t("col_window_pct")}
              </th>
              <th className="py-2 pr-3 text-right">{t("col_positions")}</th>
              <th className="py-2 pl-3 text-right">{t("col_cash")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const meta = metaLocalized(r.agent, locale);
              const book = now.get(r.agent);
              const medal = MEDALS[i] ?? "";
              const rankClass =
                i === 0 ? "font-semibold" : i === 3 ? "text-muted" : "";
              return (
                <tr
                  key={r.agent}
                  className={`border-b border-council-100 dark:border-council-800 last:border-b-0 hover:bg-council-50 dark:hover:bg-council-800/30 ${rankClass}`}
                >
                  <td className="py-2.5 pr-3 tabular text-muted">#{i + 1}</td>
                  <td className="py-2.5 pr-3 text-center text-2xl leading-none">
                    {medal}
                  </td>
                  <td className="py-2.5 pr-3">
                    <Link
                      href={`/agents/${r.agent}`}
                      className="flex items-center gap-2 hover:underline"
                    >
                      <span
                        className="inline-block w-2 h-2 rounded-full"
                        style={{ backgroundColor: meta?.color ?? "#999" }}
                      />
                      <span>{meta?.display ?? r.agent}</span>
                      {r.clipped && (
                        <span className="text-xs text-muted">
                          ({t("ranking_clipped")}{" "}
                          <span className="tabular" dir="ltr">{r.startDate}</span>)
                        </span>
                      )}
                    </Link>
                  </td>
                  <td className="py-2.5 pr-3 text-right">
                    <Money value={book?.nav ?? r.endNav} />
                  </td>
                  <td className="py-2.5 pr-3 text-right">
                    <Money value={r.pnlUsd} signed digits={2} />
                  </td>
                  <td className="py-2.5 pr-3 text-right font-semibold">
                    <PctCell value={r.returnPct} />
                  </td>
                  <td className="py-2.5 pr-3 text-right tabular">
                    {book?.positions ?? "—"}
                  </td>
                  <td className="py-2.5 pl-3 text-right">
                    {book ? <Money value={book.cash} /> : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
