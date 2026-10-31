// One agent's actions, in the order they happened.
//
// The page already had its last ten verdicts, which answers "what does
// it think now". This answers the other question: when did it happen,
// and what happened before it. A name is noticed, waited for, bought,
// trimmed, sold — and those five events had never sat in one order on
// any page.

import Link from "next/link";

import { byDay, type ActionKind, type AgentAction } from "@/lib/action-log";
import type { Locale } from "@/lib/i18n";
import { translateRationale } from "@/lib/translate-dynamic";

/** Colour by what the action does to the book, not by sentiment: money
 *  going in is green, coming out is red, and merely watching is neither. */
const CHIP: Record<ActionKind, string> = {
  buy: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  add: "bg-green-50 text-green-700 dark:bg-green-900/25 dark:text-green-400",
  sell: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  trim: "bg-red-50 text-red-700 dark:bg-red-900/25 dark:text-red-400",
  watch: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
  unwatch: "bg-council-100 text-council-600 dark:bg-council-800 dark:text-council-300",
};

const LABEL_KEY: Record<ActionKind, string> = {
  buy: "action_buy",
  add: "action_add",
  sell: "action_sell",
  trim: "action_trim",
  watch: "action_watch",
  unwatch: "action_unwatch",
};

function money(value: number): string {
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** A price at the precision it filled at.
 *
 *  Two decimals at least, because a price is written with cents, and
 *  four at most — a third decimal is real (a midpoint or a
 *  split-adjusted close, which 17% of the fills carry) and rounding it
 *  away left shares x price not matching the amount beside it. */
function price(value: number): string {
  const text = value.toFixed(4).replace(/0+$/, "");
  const [whole, fraction = ""] = text.split(".");
  return `$${Number(whole).toLocaleString("en-US")}.${fraction.padEnd(2, "0")}`;
}

/** Whole shares, without the decimals they do not have. */
function shares(value: number): string {
  return Math.abs(value - Math.round(value)) < 5e-5
    ? Math.round(value).toLocaleString("en-US")
    : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

/** dd/mm/yyyy — how the reader of this dashboard writes a date. */
function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-");
  return y && m && d ? `${d}/${m}/${y}` : iso;
}

export function ActionLog({
  actions,
  agentSlug,
  locale,
  t,
}: {
  actions: AgentAction[];
  agentSlug: string;
  locale: Locale;
  t: (key: string) => string;
}) {
  if (actions.length === 0) {
    return <p className="text-sm text-muted">{t("action_log_empty")}</p>;
  }

  return (
    <ol className="space-y-4">
      {byDay(actions).map(([date, rows]) => (
        <li key={date}>
          <div className="text-xs font-medium text-muted tabular mb-1.5">
            {dayLabel(date)}
          </div>
          <ul className="space-y-1.5 border-s-2 border-council-100 dark:border-council-800 ps-3">
            {rows.map((action, i) => (
              <li
                key={`${action.ticker}-${action.kind}-${i}`}
                className="flex items-start gap-2.5 flex-wrap text-sm"
              >
                <span
                  className={`text-xs font-semibold px-1.5 py-0.5 rounded whitespace-nowrap ${
                    CHIP[action.kind]
                  }`}
                >
                  {t(LABEL_KEY[action.kind])}
                </span>
                <Link
                  href={`/agents/${agentSlug}/positions/${action.ticker}`}
                  className="font-medium tabular hover:underline"
                >
                  {action.ticker}
                </Link>
                {action.shares !== null && action.price !== null && (
                  <span className="text-xs text-council-600 dark:text-muted tabular">
                    {shares(action.shares)} × {price(action.price)}
                  </span>
                )}
                {action.shares === null && action.price !== null && (
                  <span className="text-xs text-council-600 dark:text-muted tabular">
                    {t("action_waiting_for")} {price(action.price)}
                  </span>
                )}
                {action.realized !== null && (
                  <span
                    className={`text-xs font-medium tabular ${
                      action.realized >= 0 ? "text-gain" : "text-loss"
                    }`}
                  >
                    {action.realized >= 0 ? t("action_gain") : t("action_loss")}{" "}
                    {money(Math.abs(action.realized))}
                  </span>
                )}
                {action.why && (
                  <span className="text-xs text-muted basis-full leading-relaxed">
                    {translateRationale(action.why, locale)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  );
}
