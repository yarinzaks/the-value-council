import { Card, EmptyState, MetricCard, PageTitle } from "@/components/Cards";
import { loadBrainMemos, loadCompanyNames } from "@/lib/data";
import { metaLocalized } from "@/lib/agents";
import { getServerI18n } from "@/lib/locale-server";
import type { Locale } from "@/lib/i18n";
import type { BrainMemo } from "@/lib/types";
import {
  memoEntries,
  memoFieldLabel,
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
function MemoBody({
  memo,
  locale,
  t,
}: {
  memo: Record<string, unknown>;
  locale: Locale;
  t: (key: string) => string;
}) {
  // memoEntries drops the language twin the reader did not ask for:
  // five schemas write `thesis_en` and `thesis_he`, and this page used
  // to print both.
  const entries = memoEntries(memo ?? {}, locale);
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
  if (typeof value === "number") return <span className="tabular">{value}</span>;
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
  const when = memo.timestamp?.slice(0, 16).replace("T", " ") ?? "";
  const spent = billedTokens(memo) || null;
  const cached = (memo.cache_write_tokens ?? 0) + (memo.cache_read_tokens ?? 0);

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <span className="font-medium">{meta?.display ?? memo.agent}</span>
          {memo.ticker && memo.ticker !== "?" && (
            <>
              <span className="mx-2 text-muted">·</span>
              <span className="font-mono font-medium tabular">
                {memo.ticker}
              </span>
              {companyName && (
                <span className="ms-2 text-xs text-muted">{companyName}</span>
              )}
            </>
          )}
        </div>
        <div className="text-xs text-muted tabular whitespace-nowrap">
          {when}
        </div>
      </div>

      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <span>
          {t("brain_col_model")}: <span className="font-mono">{memo.model}</span>
        </span>
        {spent !== null && (
          <span>
            {t("brain_col_tokens")}:{" "}
            <span className="tabular">{spent.toLocaleString()}</span>
            {memo.input_tokens !== null && memo.output_tokens !== null && (
              <span className="tabular">
                {" "}
                ({memo.input_tokens.toLocaleString()} →{" "}
                {memo.output_tokens.toLocaleString()})
              </span>
            )}
            {cached > 0 && (
              <span className="tabular">
                {" "}
                · {t("brain_cached")} {cached.toLocaleString()}
              </span>
            )}
          </span>
        )}
        {memo.prompt_chars !== null && (
          <span>
            {t("brain_prompt_digest")}:{" "}
            <span className="tabular">
              {memo.prompt_chars.toLocaleString()} {t("brain_chars")}
            </span>
            {memo.prompt_sha256 && (
              <span className="font-mono ms-1">
                {memo.prompt_sha256.slice(0, 12)}
              </span>
            )}
          </span>
        )}
        <span className="font-mono">{memo.schema}</span>
      </div>

      <MemoBody memo={memo.memo} locale={locale} t={t} />
    </Card>
  );
}
