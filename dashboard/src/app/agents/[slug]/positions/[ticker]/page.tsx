// Position detail page.
//
// Reached by clicking any position row in the agent drilldown. Shows
// the position-specific WHY (3 bullets distilled from the criteria
// the strategy used), entry price, current price, exit trigger, all
// in both languages.

import { notFound } from "next/navigation";
import Link from "next/link";
import {
  Card,
  Money,
  PctCell,
  PageTitle,
} from "@/components/Cards";
import { Term } from "@/components/Term";
import {
  loadCompanyNames,
  loadJournal,
  loadLivePortfolio,
  loadPriceSeries,
} from "@/lib/data";
import { PositionPriceChart } from "@/components/PositionPriceChart";
import { AGENTS, metaLocalized } from "@/lib/agents";
import type { AgentSlug } from "@/lib/types";
import { getServerI18n } from "@/lib/locale-server";
import {
  translateCriterion,
  translateExitTrigger,
  translateRationale,
} from "@/lib/translate-dynamic";

// Static in the two-language export, where "force-dynamic" is a hard
// error; dynamic under a live server (next dev). NEXT_PUBLIC_* is
// inlined at build time, so this folds to a constant.
export const dynamic = process.env.NEXT_PUBLIC_SITE_LOCALE
  ? "force-static"
  : "force-dynamic";

/**
 * Every (agent, ticker) pair that is currently held.
 *
 * A static export has to know its routes up front, and these are the
 * only ones anything links to — LivePositionsTable and
 * PositionStoryCard both build their hrefs from the same portfolios.
 *
 * It follows that this page covers open positions only. A name sold
 * last week has no page here and never did; the trade itself stays in
 * the journal and in the agent's history. `dynamicParams` is left at
 * its default so an unknown pair 404s rather than being generated on
 * demand, which is also what `output: "export"` requires.
 */
export async function generateStaticParams(): Promise<
  Array<{ slug: string; ticker: string }>
> {
  const params: Array<{ slug: string; ticker: string }> = [];
  for (const agent of AGENTS) {
    const portfolio = await loadLivePortfolio(agent.slug);
    if (!portfolio) continue;
    for (const position of portfolio.positions) {
      params.push({ slug: agent.slug, ticker: position.ticker });
    }
  }
  return params;
}

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

export default async function PositionDetailPage({
  params,
}: {
  params: { slug: string; ticker: string };
}) {
  const { locale, t } = getServerI18n();
  const slug = params.slug as AgentSlug;
  const ticker = params.ticker.toUpperCase();
  const meta = metaLocalized(slug, locale);
  if (!meta) return notFound();

  const [portfolio, names, decisionsAll, priceSeries] = await Promise.all([
    loadLivePortfolio(slug),
    loadCompanyNames(),
    loadJournal({ agent: slug, decisions: ["BUY"], limit: 5000 }),
    loadPriceSeries(ticker),
  ]);
  const position = portfolio?.positions.find((p) => p.ticker === ticker);
  if (!position) {
    return (
      <>
        <PageTitle title={ticker} subtitle={meta.display} />
        <Card>
          <p className="text-sm text-muted mb-4">{t("no_position_found")}</p>
          <Link
            href={`/agents/${slug}`}
            className="text-sm text-council-600 dark:text-muted hover:underline"
          >
            {t("back_to_drilldown")}
          </Link>
        </Card>
      </>
    );
  }

  // Find the most recent BUY decision for this ticker — that's the
  // best source of "why", criteria_met, and exit_trigger.
  const decisions = decisionsAll.filter((d) => d.ticker === ticker);
  const latestBuy = decisions[0]; // already sorted newest-first
  const value = position.shares * position.current_price;
  const companyName = names[ticker] ?? "";
  const why = locale === "he" ? position.why_he : position.why_en;

  return (
    <>
      <Link
        href={`/agents/${slug}`}
        className="inline-block text-sm text-council-600 dark:text-muted hover:underline mb-3"
      >
        {t("back_to_drilldown")}
      </Link>
      <PageTitle
        title={`${ticker}${companyName ? ` · ${companyName}` : ""}`}
        subtitle={`${meta.display} · ${meta.school_label}`}
      />

      {/* The path between entry and today. Without it a position that
          round-tripped 30% and came back looks identical to one that
          never moved. */}
      {priceSeries && priceSeries.points.length > 1 && (
        <Card className="mb-6">
          <div className="text-xs text-muted mb-2">
            {t("price_chart_title")}
          </div>
          <PositionPriceChart
            points={priceSeries.points}
            entryPrice={position.entry_price}
            entryDate={position.entry_date}
            color={meta.color}
            labels={{ entry: t("col_entry"), price: t("col_current") }}
          />
          <p className="mt-2 text-[11px] text-muted">
            {t("price_chart_note")}
          </p>
        </Card>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <Card>
          <div className="text-xs text-muted">{t("col_entry")}</div>
          <div className="text-2xl font-semibold">
            <Money value={position.entry_price} digits={2} />
          </div>
          <div className="text-xs text-muted mt-1">
            {position.entry_date}
          </div>
        </Card>
        <Card>
          <div className="text-xs text-muted">{t("col_current")}</div>
          <div className="text-2xl font-semibold">
            <Money value={position.current_price} digits={2} />
          </div>
          <div className="text-xs text-muted mt-1">
            {position.shares.toFixed(0)} {t("col_shares").toLowerCase()}
          </div>
        </Card>
        <Card>
          <div className="text-xs text-muted">{t("col_value")}</div>
          <div className="text-2xl font-semibold">
            <Money value={value} />
          </div>
          <div className="text-xs text-muted mt-1">
            <Term k="weight">{t("col_weight")}</Term> {position.weight_pct.toFixed(1)}%
          </div>
        </Card>
        <Card
          className={
            position.pnl_usd > 0
              ? "ring-1 ring-gain/30"
              : position.pnl_usd < 0
                ? "ring-1 ring-loss/30"
                : ""
          }
        >
          <div className="text-xs text-muted">{t("col_pnl_usd")}</div>
          <div className="text-2xl font-semibold">
            <Money value={position.pnl_usd} signed digits={2} />
          </div>
          <div className="text-xs mt-1">
            <PctCell value={position.pnl_pct} />
          </div>
        </Card>
      </div>

      <Card className="mb-6">
        <h3 className="text-sm font-semibold mb-3">{t("why_bought")}</h3>
        <p className="text-sm text-council-700 dark:text-council-300 mb-3">
          {decorate(why || "—")}
        </p>
        {latestBuy && latestBuy.criteria_met.length > 0 && (
          <ul className="space-y-1.5">
            {latestBuy.criteria_met.slice(0, 5).map((c, i) => (
              <li
                key={i}
                className="text-sm text-council-600 dark:text-council-300 flex items-start gap-2"
              >
                <span className="text-muted mt-0.5">•</span>
                <span>{decorate(translateCriterion(c, locale))}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {latestBuy?.exit_trigger && (
        <Card className="mb-6">
          <h3 className="text-sm font-semibold mb-2">{t("exit_trigger")}</h3>
          <p className="text-sm text-council-700 dark:text-council-300">
            {decorate(translateExitTrigger(latestBuy.exit_trigger, locale))}
          </p>
        </Card>
      )}

      {latestBuy && Object.keys(latestBuy.criteria_values).length > 0 && (
        <Card>
          <h3 className="text-sm font-semibold mb-3">{t("position_metrics")}</h3>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
            {Object.entries(latestBuy.criteria_values)
              .filter(([, v]) => v !== null && v !== undefined)
              .map(([k, v]) => (
                <div key={k}>
                  <div className="text-xs text-muted uppercase tracking-wider">
                    {k.replace(/_/g, " ")}
                  </div>
                  <div className="font-mono tabular">
                    {typeof v === "number" ? v.toLocaleString() : String(v)}
                  </div>
                </div>
              ))}
          </div>
        </Card>
      )}
    </>
  );
}
