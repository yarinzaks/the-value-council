import { Card, EmptyState, MetricCard, PageTitle } from "@/components/Cards";
import { loadBrainMemos, loadCompanyNames } from "@/lib/data";
import { metaLocalized } from "@/lib/agents";
import { getServerI18n } from "@/lib/locale-server";
import type { Locale } from "@/lib/i18n";
import type { BrainMemo } from "@/lib/types";

// Static in the two-language export, where "force-dynamic" is a hard
// error; dynamic under a live server. Same pattern as every other page.
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
function MemoBody({ memo }: { memo: Record<string, unknown> }) {
  const entries = Object.entries(memo ?? {});
  if (entries.length === 0) return null;

  return (
    <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="text-xs uppercase tracking-wider text-muted">
            {key.replace(/_/g, " ")}
          </dt>
          <dd className="mt-0.5 text-sm break-words">
            {renderValue(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function renderValue(value: unknown): React.ReactNode {
  if (value === null || value === undefined) return <span className="text-muted">—</span>;
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return <span className="tabular">{value}</span>;
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-muted">—</span>;
    return (
      <ul className="list-disc ms-4 space-y-0.5">
        {value.map((v, i) => (
          <li key={i}>{renderValue(v)}</li>
        ))}
      </ul>
    );
  }
  // A nested object — Fisher's fifteen points arrive this way.
  return (
    <dl className="space-y-0.5">
      {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
        <div key={k} className="flex gap-2">
          <dt className="text-muted shrink-0">{k.replace(/_/g, " ")}:</dt>
          <dd className="min-w-0 break-words">{renderValue(v)}</dd>
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

  const tokens = memos.reduce(
    (sum, m) => sum + (m.input_tokens ?? 0) + (m.output_tokens ?? 0),
    0,
  );
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
  const spent =
    (memo.input_tokens ?? 0) + (memo.output_tokens ?? 0) || null;

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

      <MemoBody memo={memo.memo} />
    </Card>
  );
}
