import { Card, EmptyState, MetricCard, PageTitle } from "@/components/Cards";
import { loadBrainMemos, loadCompanyNames } from "@/lib/data";
import { metaLocalized } from "@/lib/agents";
import { getServerI18n } from "@/lib/locale-server";
import type { Locale } from "@/lib/i18n";
import type { BrainMemo } from "@/lib/types";
import {
  memoDigest,
  memoFieldLabel,
  memoRest,
  memoValue,
} from "@/lib/translate-dynamic";

// Static in the two-language export, where "force-dynamic" is a hard
// error; dynamic under a live server. Same pattern as every other page.
/** Every input and output token a call was billed for.
 *
 *  Cached input is reported in its own fields rather than folded into
 *  `input_tokens`, so a sum that reads only the latter misses the
 *  playbook entirely — which is most of what each call costs.
 */
function billedTokens(memo: BrainMemo): number {
  return (
    (memo.input_tokens ?? 0) +
    (memo.output_tokens ?? 0) +
    (memo.cache_write_tokens ?? 0) +
    (memo.cache_read_tokens ?? 0)
  );
}

export const dynamic = process.env.NEXT_PUBLIC_SITE_LOCALE
  ? "force-static"
  : "force-dynamic";

/** The memo, rendered without claiming to know its shape.
 *
 * Each analyst answers in its own schema — Fisher returns fifteen point
 * verdicts, Klarman a downside scenario — so this walks whatever keys
 * are there rather than naming them. A field added to a memo class
 * shows up here with no change to this page, which is the only way a
 * page like this stays honest about "everything it said".
 */
/** Fields whose zero means the snapshot carried nothing.
 *
 *  Kept to the fields where zero is impossible as a measurement: a
 *  company is never worth $0, retention is never 0%, and an industry
 *  average operating margin of exactly 0 is not an industry. A score of
 *  0 out of 15 is deliberately absent — that one is a verdict.
 */
const ZERO_IS_UNREPORTED = new Set([
  "intrinsic_value_estimate_usd",
  "intrinsic_value_estimate_usd_per_share",
  "margin_of_safety_pct",
  "expected_growth_rate_5yr_pct",
  "industry_avg_operating_margin_pct",
  "customer_retention_pct",
  "employee_turnover_pct",
  "owner_earnings_5yr_avg_usd",
  "market_temperature_score",
]);

/** A number at the precision it was measured to, not computed to. */
function formatNumber(value: number): string {
  if (Number.isInteger(value)) return value.toLocaleString("en-US");
  // Two decimals for a price or a ratio, four for a figure small
  // enough that two would round it away.
  const digits = Math.abs(value) >= 0.1 ? 2 : 4;
  return value.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

/** Colour by what the verdict does, not by sentiment. */
const VERDICT_STYLE: Record<string, string> = {
  BUY: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  ADD: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  HOLD: "bg-council-100 text-council-700 dark:bg-council-800 dark:text-council-200",
  WATCH: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
  TRIM: "bg-red-50 text-red-700 dark:bg-red-900/25 dark:text-red-400",
  SELL: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  RARE_SELL: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  REJECT: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  DEFAULT: "bg-council-100 text-council-700 dark:bg-council-800 dark:text-council-200",
};

function MemoBody({
  entries,
  locale,
  t,
}: {
  entries: [string, unknown][];
  locale: Locale;
  t: (key: string) => string;
}) {
  if (entries.length === 0) return null;

  return (
    <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="text-xs tracking-wider text-muted">
            {memoFieldLabel(key, locale)}
          </dt>
          <dd className="mt-0.5 text-sm break-words">
            {renderValue(value, key, locale, t)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function renderValue(
  value: unknown,
  path: string,
  locale: Locale,
  t: (key: string) => string,
): React.ReactNode {
  if (value === null || value === undefined) return <span className="text-muted">—</span>;
  if (typeof value === "boolean") return value ? t("yes") : t("no");
  if (typeof value === "number") {
    // Raw floats arrived as the analyst computed them: a current price
    // of 229.1699981689453 and a P/E of 29.38076899601863. Fifteen
    // digits of a quantity nobody measures past two.
    //
    // And a zero in a valuation field is not a zero. The analyst says
    // so itself, in the rationale of the memo that prompted this —
    // "read 0 as 'not reported'" — because the schema requires a
    // number and the snapshot gave it nothing. 72 of 120 intrinsic
    // values are zero that way. Printed as "0" it reads as a
    // measurement; named, it reads as the gap it is.
    if (value === 0 && ZERO_IS_UNREPORTED.has(path.split(".").pop() ?? "")) {
      return <span className="text-muted">{t("brain_not_reported")}</span>;
    }
    return <span className="tabular">{formatNumber(value)}</span>;
  }
  // Through the phrase table: a memo's short string values are the
  // agents' own vocabulary — "Fast Grower", "Tier A", "INSIDE" — and
  // the same table that renders them in the Telegram message renders
  // them here.
  if (typeof value === "string") return memoValue(value, locale);
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-muted">—</span>;
    return (
      <ul className="list-disc ms-4 space-y-0.5">
        {value.map((v, i) => (
          <li key={i}>{renderValue(v, path, locale, t)}</li>
        ))}
      </ul>
    );
  }
  // A nested object — Fisher's fifteen points arrive this way. The
  // label is looked up on the dotted path, so `scaling_out_plan.
  // full_exit_price` is labelled as itself rather than as whatever
  // `full_exit_price` means elsewhere.
  return (
    <dl className="space-y-0.5">
      {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
        <div key={k} className="flex gap-2">
          <dt className="text-muted shrink-0">
            {memoFieldLabel(`${path}.${k}`, locale)}:
          </dt>
          <dd className="min-w-0 break-words">
            {renderValue(v, `${path}.${k}`, locale, t)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default async function BrainPage() {
  const { locale, t } = getServerI18n();
  const [memos, companyNames] = await Promise.all([
    loadBrainMemos(),
    loadCompanyNames(),
  ]);

  if (memos.length === 0) {
    return (
      <>
        <PageTitle title={t("brain_title")} subtitle={t("brain_subtitle")} />
        <EmptyState>{t("brain_empty")}</EmptyState>
      </>
    );
  }

  // Every token the calls were billed for, cached or not. The API
  // reports cached input in its own fields, so input_tokens is the
  // uncached remainder alone — 30 of them against a 14,622-token
  // playbook. Summing only that reported a thousandth of the bill.
  const tokens = memos.reduce((sum, m) => sum + billedTokens(m), 0);
  const books = new Set(memos.map((m) => m.agent)).size;

  return (
    <>
      <PageTitle title={t("brain_title")} subtitle={t("brain_subtitle")} />

      <div className="grid gap-4 sm:grid-cols-3 mb-6">
        <MetricCard label={t("brain_total_calls")} value={String(memos.length)} />
        <MetricCard
          label={t("brain_total_tokens")}
          value={tokens.toLocaleString()}
        />
        <MetricCard label={t("brain_books")} value={String(books)} />
      </div>

      <div className="space-y-4">
        {memos.map((m, i) => (
          <MemoCard
            key={`${m.agent}-${m.timestamp}-${m.ticker}-${i}`}
            memo={m}
            companyName={companyNames[m.ticker]}
            locale={locale}
            t={t}
          />
        ))}
      </div>
    </>
  );
}

function MemoCard({
  memo,
  companyName,
  locale,
  t,
}: {
  memo: BrainMemo;
  companyName: string | undefined;
  locale: Locale;
  t: (k: string) => string;
}) {
  const meta = metaLocalized(memo.agent, locale);
  const when = memo.timestamp?.slice(0, 10) ?? "";
  const body = (memo.memo ?? {}) as Record<string, unknown>;
  // Verdict, thesis, three figures. Everything else folds away: the
  // page printed all 123 fields of every memo as a grid, and a reader
  // looking for the verdict had to find it among them.
  const { decision, thesis, figures } = memoDigest(body, locale);
  const rest = memoRest(body, locale);

  return (
    <Card>
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <div className="flex items-baseline gap-2 flex-wrap min-w-0">
          {decision && (
            <span
              className={`text-xs font-semibold px-1.5 py-0.5 rounded whitespace-nowrap ${
                VERDICT_STYLE[decision.toUpperCase()] ?? VERDICT_STYLE.DEFAULT
              }`}
            >
              {memoValue(decision, locale)}
            </span>
          )}
          <span className="font-mono font-medium tabular">{memo.ticker}</span>
          {companyName && (
            <span className="text-xs text-muted truncate">{companyName}</span>
          )}
          <span className="text-xs text-muted">
            · {meta?.display ?? memo.agent}
          </span>
        </div>
        <div className="text-xs text-muted tabular whitespace-nowrap">{when}</div>
      </div>

      {thesis && (
        <p className="mt-2 text-sm leading-relaxed text-council-700 dark:text-council-200">
          {thesis}
        </p>
      )}

      {figures.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
          {figures.map(([key, value]) => (
            <div key={key}>
              <div className="text-[11px] text-muted">
                {memoFieldLabel(key, locale)}
              </div>
              <div className="text-sm font-medium tabular">
                {renderValue(value, key, locale, t)}
              </div>
            </div>
          ))}
        </div>
      )}

      {rest.length > 0 && (
        // <details>, so the fold costs no JavaScript and survives a
        // reader who prints the page.
        <details className="mt-3 group">
          <summary className="text-xs text-muted cursor-pointer hover:underline list-none">
            {t("brain_show_all")} ({rest.length})
          </summary>
          <MemoBody entries={rest} locale={locale} t={t} />
          <div className="mt-3 pt-2 border-t border-council-100 dark:border-council-800 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
            <span>
              {t("brain_col_model")}:{" "}
              <span className="font-mono">{memo.model}</span>
            </span>
            {billedTokens(memo) > 0 && (
              <span className="tabular">
                {t("brain_col_tokens")}: {billedTokens(memo).toLocaleString()}
              </span>
            )}
            {memo.prompt_sha256 && (
              <span className="font-mono">
                {memo.prompt_sha256.slice(0, 12)}
              </span>
            )}
            <span className="font-mono">{memo.schema}</span>
          </div>
        </details>
      )}
    </Card>
  );
}
