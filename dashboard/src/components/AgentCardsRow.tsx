// 4-agent cards row for the Overview page.
//
// Server-rendered. Each card shows: NAV, cash, positions count,
// today's P&L (in $ and %), and watchlist count. Clicking a card
// takes the user to the agent drilldown.

import Link from "next/link";
import { Card, Money, PctCell } from "./Cards";
import type { AgentDailyDelta, AgentSlug, LivePortfolio } from "@/lib/types";
import { metaLocalized } from "@/lib/agents";
import type { Locale } from "@/lib/i18n";
import { t as translate } from "@/lib/i18n";
import { formatTimeOfDay, isStale } from "@/lib/timestamps";

interface CardData {
  portfolio: LivePortfolio;
  delta: AgentDailyDelta | null;
}

export function AgentCardsRow({
  cards,
  locale,
}: {
  cards: CardData[];
  locale: Locale;
}) {
  const t = (k: string) => translate(locale, k);
  if (cards.length === 0) return null;
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      {cards.map(({ portfolio, delta }) => {
        const meta = metaLocalized(portfolio.agent as AgentSlug, locale);
        const navChange = delta?.nav_change_usd ?? 0;
        const navChangePct = delta?.nav_change_pct ?? 0;
        const positivePnl = navChange > 0;
        const negativePnl = navChange < 0;
        const openHm = portfolio.last_open_run
          ? formatTimeOfDay(portfolio.last_open_run)
          : "";
        const closeHm = portfolio.last_close_run
          ? formatTimeOfDay(portfolio.last_close_run)
          : "";
        const agentStale = isStale(portfolio.last_updated, 24);
        return (
          <Link
            key={portfolio.agent}
            href={`/agents/${portfolio.agent}`}
            className="block"
          >
            <Card
              className={`transition-all hover:ring-2 hover:ring-council-300 dark:hover:ring-council-600 h-full ${
                agentStale
                  ? "ring-1 ring-loss/40"
                  : positivePnl
                    ? "ring-1 ring-gain/20"
                    : negativePnl
                      ? "ring-1 ring-loss/20"
                      : ""
              }`}
            >
              <div className="flex items-center gap-2 mb-2">
                <span
                  className="inline-block w-2.5 h-2.5 rounded-full"
                  style={{ backgroundColor: meta?.color ?? "#999" }}
                />
                <span className="font-semibold">
                  {meta?.display ?? portfolio.agent}
                </span>
              </div>
              <div className="text-xs text-muted mb-3">
                {meta?.school_label}
              </div>

              <div className="space-y-2 text-sm">
                <div className="flex items-baseline justify-between">
                  <span className="text-xs text-muted">{t("col_nav")}</span>
                  <span className="font-semibold">
                    <Money value={portfolio.total_nav} />
                  </span>
                </div>
                <div className="flex items-baseline justify-between">
                  <span className="text-xs text-muted">{t("col_cash")}</span>
                  <span>
                    <Money value={portfolio.cash} />
                  </span>
                </div>
                <div className="flex items-baseline justify-between">
                  <span className="text-xs text-muted">{t("col_positions")}</span>
                  <span className="tabular">{portfolio.positions.length}</span>
                </div>
                <div className="flex items-baseline justify-between">
                  <span className="text-xs text-muted">{t("col_watch")}</span>
                  <span className="tabular text-muted">
                    {portfolio.watchlist.length}
                  </span>
                </div>
                <div className="border-t border-council-100 dark:border-council-800 pt-2 mt-2">
                  <div className="flex items-baseline justify-between">
                    <span className="text-xs text-muted">
                      {t("card_pnl_today")}
                    </span>
                    <span className="text-right">
                      <span className="block">
                        <Money value={navChange} signed digits={2} />
                      </span>
                      <span className="block text-xs">
                        <PctCell value={navChangePct} />
                      </span>
                    </span>
                  </div>
                </div>
                {/* Per-agent timestamps — open + close. Empty when an
                    agent hasn't run that mode yet. */}
                {(openHm || closeHm) && (
                  <div className="border-t border-council-100 dark:border-council-800 pt-2 mt-2 flex items-baseline justify-between text-[11px] text-muted">
                    {openHm && (
                      <span>
                        {t("last_open_scan")}: {openHm}
                      </span>
                    )}
                    {closeHm && (
                      <span>
                        {t("last_close_scan")}: {closeHm}
                      </span>
                    )}
                  </div>
                )}
              </div>
            </Card>
          </Link>
        );
      })}
    </div>
  );
}
