"use client";

import Link from "next/link";
import { useUI } from "./Providers";
import { Money, PctCell } from "./Cards";
import { Term } from "./Term";
import type { LivePosition, MarkFreshness } from "@/lib/types";
import { MAX_MARK_AGE_DAYS } from "@/lib/types";
import { formatTimeOfDay } from "@/lib/timestamps";

const TERM_PATTERN = /\b(P\/E|P\/B|P\/CF|P\/NCAV|D\/E|EY|ROC|NCAV|EBIT|EV|yield)\b/g;
const TERM_TO_KEY: Record<string, string> = {
  "P/E": "pe",
  "P/B": "pb",
  "P/CF": "pcf",
  "P/NCAV": "p_ncav",
  "D/E": "de_ratio",
  EY: "ey",
  ROC: "roc",
  NCAV: "ncav",
  yield: "dividend_yield",
  EBIT: "ey",
  EV: "ey",
};

function decorate(text: string): JSX.Element {
  if (!text) return <>{text}</>;
  const parts: (string | JSX.Element)[] = [];
  let last = 0;
  for (const match of text.matchAll(TERM_PATTERN)) {
    const idx = match.index ?? 0;
    if (idx > last) parts.push(text.slice(last, idx));
    const key = TERM_TO_KEY[match[0]];
    if (key) {
      parts.push(
        <Term key={`${idx}-${match[0]}`} k={key}>
          {match[0]}
        </Term>,
      );
    } else {
      parts.push(match[0]);
    }
    last = idx + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

export function LivePositionsTable({
  positions,
  agentSlug,
  companyNames = {},
  priceMarkedAt = "",
  markFreshness = {},
}: {
  positions: LivePosition[];
  agentSlug: string;
  companyNames?: Record<string, string>;
  /** When the portfolio's prices were last marked. Shows as "מחיר
   *  מעודכן: HH:MM" / "Price updated: HH:MM" under each row. */
  priceMarkedAt?: string;
  /** Per-ticker: the date of the bar each mark actually came from.
   *
   *  Without it every row printed the portfolio's run time, so a
   *  position last traded seven weeks ago claimed to be priced minutes
   *  ago. A reader copying that number would be acting on a quote the
   *  market has moved past — and nothing on the page said so. */
  markFreshness?: Record<string, MarkFreshness>;
}) {
  const { locale, t } = useUI();
  if (positions.length === 0) {
    return (
      <p className="text-sm text-muted">{t("no_open_positions")}</p>
    );
  }
  const sorted = [...positions].sort((a, b) => b.weight_pct - a.weight_pct);
  const priceHm = formatTimeOfDay(priceMarkedAt);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-muted border-b border-council-200 dark:border-council-800">
            <th className="py-2 pr-3">{t("col_ticker")}</th>
            <th className="py-2 pr-3">{t("company_name")}</th>
            <th className="py-2 pr-3 text-right">{t("col_shares")}</th>
            <th className="py-2 pr-3 text-right">{t("col_entry")}</th>
            <th className="py-2 pr-3 text-right">{t("col_current")}</th>
            <th className="py-2 pr-3 text-right">{t("col_value")}</th>
            <th className="py-2 pr-3 text-right">{t("col_pnl_usd")}</th>
            <th className="py-2 pr-3 text-right">{t("col_pnl_pct")}</th>
            <th className="py-2 pl-3 text-right">{t("col_weight")}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => {
            const value = p.shares * p.current_price;
            const name = companyNames[p.ticker.toUpperCase()] ?? "";
            const mark = markFreshness[p.ticker.toUpperCase()];
            const stale = mark !== undefined && mark.days_stale > MAX_MARK_AGE_DAYS;
            // No published series means nobody can date this mark. That
            // is not the same as fresh, and printing the run's clock
            // beside it would assert exactly the thing we cannot check.
            const undated = mark === undefined;
            return (
              <tr
                key={p.ticker}
                className="border-b border-council-100 dark:border-council-800 last:border-b-0 hover:bg-council-50 dark:hover:bg-council-800/30 cursor-pointer"
              >
                <td className="py-2 pr-3 font-mono font-medium tabular">
                  <Link
                    href={`/agents/${agentSlug}/positions/${p.ticker}`}
                    className="block hover:underline"
                  >
                    {p.ticker}
                  </Link>
                </td>
                <td className="py-2 pr-3 text-xs text-council-700 dark:text-council-300">
                  <Link href={`/agents/${agentSlug}/positions/${p.ticker}`} className="block">
                    {name || "—"}
                  </Link>
                </td>
                <td className="py-2 pr-3 text-right tabular">{p.shares.toFixed(0)}</td>
                <td className="py-2 pr-3 text-right">
                  <Money value={p.entry_price} digits={2} />
                </td>
                <td className="py-2 pr-3 text-right">
                  <span className="block">
                    <Money value={p.current_price} digits={2} />
                  </span>
                  {/* A stale mark must never borrow the run's timestamp.
                      The run did happen at priceHm; this price did not
                      come from it, and saying otherwise is the one thing
                      that would make a reader act on a dead quote. */}
                  {stale && mark ? (
                    <span
                      className="block text-[10px] mt-0.5 text-amber-700 dark:text-amber-400"
                      title={t("mark_stale_help")}
                    >
                      ⚠ {t("mark_stale")}: {mark.bar_date} (
                      {t("days_ago").replace("{n}", String(mark.days_stale))})
                    </span>
                  ) : undated ? (
                    <span
                      className="block text-[10px] text-muted mt-0.5 italic"
                      title={t("mark_undated_help")}
                    >
                      {t("mark_undated")}
                    </span>
                  ) : (
                    priceHm && (
                      <span className="block text-[10px] text-muted mt-0.5">
                        {t("price_updated")}: {priceHm}
                      </span>
                    )
                  )}
                </td>
                <td className="py-2 pr-3 text-right">
                  <Money value={value} />
                </td>
                <td className="py-2 pr-3 text-right">
                  <Money value={p.pnl_usd} signed digits={2} />
                </td>
                <td className="py-2 pr-3 text-right">
                  <PctCell value={p.pnl_pct} />
                </td>
                <td className="py-2 pl-3 text-right tabular text-muted">
                  {p.weight_pct.toFixed(1)}%
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export { decorate as decorateTerms };
