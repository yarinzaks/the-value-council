"use client";

// Per-agent performance chart, Yahoo Finance / Robinhood style.
//
//   * Time period selector: 1W / 1M / 3M / ALL (pill buttons)
//   * Agent NAV line + S&P 500 benchmark line (rebased to same start)
//   * Hover tooltip with date + NAV + % since start
//   * Line color: gain or loss vs starting NAV
//   * Statistics row: best day, worst day, since-start, vs S&P
//
// All time-series math happens client-side from the snapshots props.

import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, Money, PctCell } from "./Cards";
import { useUI } from "./Providers";
import type { DailySnapshot, NavRow } from "@/lib/types";

// ---- types --------------------------------------------------------------

type Period = "1W" | "1M" | "3M" | "ALL";

interface ChartRow {
  date: string;
  agentNav: number;
  /** Benchmark NAV rebased to start = agent's start NAV. Optional. */
  benchmarkNav?: number;
  /** % change vs the first row in this window. */
  agentPct: number;
  benchmarkPct?: number;
}

interface Props {
  /** Daily snapshots, sorted oldest-first. */
  snapshots: DailySnapshot[];
  /** Optional benchmark NAV history (NavRow[].nav not used, only date+benchmark_nav) */
  benchmark: NavRow[];
  /** Color for the agent line (e.g. agent's brand color). */
  agentColor: string;
  /** Display name (used in chart legend tooltip). */
  agentName: string;
}

// ---- helpers ------------------------------------------------------------

function periodDays(p: Period): number {
  switch (p) {
    case "1W":
      return 7;
    case "1M":
      return 30;
    case "3M":
      return 90;
    case "ALL":
      return Number.POSITIVE_INFINITY;
  }
}

// ---- component ----------------------------------------------------------

export function AgentPerformanceChart({
  snapshots,
  benchmark,
  agentColor,
  agentName,
}: Props) {
  const { locale, t } = useUI();
  const [period, setPeriod] = useState<Period>("1M");

  const data = useMemo(() => {
    if (snapshots.length === 0) return { rows: [], stats: null };

    // Apply period window from the most recent snapshot backwards.
    const all = [...snapshots].sort((a, b) => a.date.localeCompare(b.date));
    const days = periodDays(period);
    const lastDate = new Date(all[all.length - 1].date);
    const cutoff = new Date(lastDate);
    cutoff.setDate(cutoff.getDate() - days);
    const windowed =
      days === Number.POSITIVE_INFINITY
        ? all
        : all.filter((s) => new Date(s.date) >= cutoff);
    if (windowed.length === 0) return { rows: [], stats: null };

    // Rebase benchmark to the starting NAV of the agent in this window.
    const startNav = windowed[0].nav;
    const benchByDate = new Map<string, number>();
    for (const b of benchmark) benchByDate.set(b.date, b.benchmark_nav);
    const benchStart = benchByDate.get(windowed[0].date);

    const rows: ChartRow[] = windowed.map((s) => {
      const bRaw = benchByDate.get(s.date);
      const benchmarkNav =
        bRaw !== undefined && benchStart !== undefined && benchStart > 0
          ? (bRaw / benchStart) * startNav
          : undefined;
      return {
        date: s.date,
        agentNav: s.nav,
        benchmarkNav,
        agentPct: ((s.nav - startNav) / startNav) * 100,
        benchmarkPct:
          bRaw !== undefined && benchStart !== undefined && benchStart > 0
            ? ((bRaw - benchStart) / benchStart) * 100
            : undefined,
      };
    });

    // Stats (best / worst day + cumulative).
    let best = { date: "", pct: -Infinity };
    let worst = { date: "", pct: Infinity };
    for (let i = 1; i < windowed.length; i++) {
      const prev = windowed[i - 1];
      const cur = windowed[i];
      const dayPct = ((cur.nav - prev.nav) / prev.nav) * 100;
      if (dayPct > best.pct) best = { date: cur.date, pct: dayPct };
      if (dayPct < worst.pct) worst = { date: cur.date, pct: dayPct };
    }
    const totalAgentPct =
      ((windowed[windowed.length - 1].nav - windowed[0].nav) / windowed[0].nav) * 100;
    const benchEnd = benchByDate.get(windowed[windowed.length - 1].date);
    const totalBenchPct =
      benchStart !== undefined && benchEnd !== undefined && benchStart > 0
        ? ((benchEnd - benchStart) / benchStart) * 100
        : null;
    const totalAgentUsd = windowed[windowed.length - 1].nav - windowed[0].nav;

    return {
      rows,
      stats: {
        startNav,
        endNav: windowed[windowed.length - 1].nav,
        totalAgentPct,
        totalAgentUsd,
        totalBenchPct,
        best: best.pct === -Infinity ? null : best,
        worst: worst.pct === Infinity ? null : worst,
      },
    };
  }, [snapshots, benchmark, period]);

  if (data.rows.length === 0) {
    return (
      <Card>
        <p className="text-sm text-muted text-center py-8">
          {t("no_history")}
        </p>
      </Card>
    );
  }

  const lineColor =
    data.stats && data.stats.totalAgentPct >= 0 ? "#16a34a" : "#dc2626";
  const periods: Period[] = ["1W", "1M", "3M", "ALL"];

  return (
    <Card>
      {/* Period selector */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <h3 className="text-sm font-semibold">
          {locale === "he" ? "ביצועי תיק" : "Portfolio performance"} · {agentName}
        </h3>
        <div className="flex gap-1 bg-council-100 dark:bg-council-800 rounded-md p-1">
          {periods.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1 text-xs font-medium rounded transition-colors ${
                period === p
                  ? "bg-white dark:bg-council-700 shadow-sm"
                  : "text-muted hover:text-council-700 dark:hover:text-council-300"
              }`}
            >
              {p === "ALL" ? (locale === "he" ? "הכל" : "ALL") : p}
            </button>
          ))}
        </div>
      </div>

      {/* Chart */}
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data.rows} margin={{ top: 5, right: 10, bottom: 5, left: 5 }}>
            <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.2} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11 }}
              minTickGap={30}
              tickFormatter={(d: string) => d.slice(5)}
            />
            <YAxis
              tick={{ fontSize: 11 }}
              tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`}
              domain={["auto", "auto"]}
              width={60}
            />
            {data.stats && (
              <ReferenceLine
                y={data.stats.startNav}
                stroke="#94a3b8"
                strokeDasharray="3 3"
                strokeOpacity={0.5}
                label={{
                  value: `$${data.stats.startNav.toFixed(0)}`,
                  position: "right",
                  fill: "#64748b",
                  fontSize: 10,
                }}
              />
            )}
            <Tooltip
              labelFormatter={(d: string) => d}
              formatter={(value: number, name: string, props: { payload?: ChartRow }) => {
                const row = props.payload;
                if (!row) return [`$${value.toFixed(2)}`, name];
                if (name === "agentNav") {
                  return [
                    `$${value.toLocaleString("en-US", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}  (${row.agentPct >= 0 ? "+" : ""}${row.agentPct.toFixed(2)}%)`,
                    agentName,
                  ];
                }
                if (name === "benchmarkNav") {
                  return [
                    `$${value.toLocaleString("en-US", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}  (${row.benchmarkPct !== undefined && row.benchmarkPct >= 0 ? "+" : ""}${row.benchmarkPct?.toFixed(2) ?? "—"}%)`,
                    locale === "he" ? "מדד S&P 500" : "S&P 500",
                  ];
                }
                return [`${value}`, name];
              }}
            />
            <Line
              type="monotone"
              dataKey="agentNav"
              stroke={lineColor}
              strokeWidth={2.5}
              dot={false}
              isAnimationActive={false}
              name={agentName}
            />
            <Line
              type="monotone"
              dataKey="benchmarkNav"
              stroke="#64748b"
              strokeWidth={1.5}
              strokeDasharray="4 4"
              dot={false}
              isAnimationActive={false}
              name="S&P 500"
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Stats row */}
      {data.stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4 pt-4 border-t border-council-100 dark:border-council-800">
          <div>
            <div className="text-xs text-muted">
              {locale === "he" ? "מאז ההתחלה" : "Since start"}
            </div>
            <div className="font-semibold text-base">
              <PctCell value={data.stats.totalAgentPct} />
            </div>
            <div className="text-xs text-muted tabular">
              <Money value={data.stats.totalAgentUsd} signed digits={2} />
            </div>
          </div>
          <div>
            <div className="text-xs text-muted">
              {locale === "he" ? "מול S&P 500" : "vs S&P 500"}
            </div>
            <div className="font-semibold text-base">
              {data.stats.totalBenchPct !== null ? (
                <PctCell
                  value={data.stats.totalAgentPct - data.stats.totalBenchPct}
                />
              ) : (
                <span className="text-muted">—</span>
              )}
            </div>
            <div className="text-xs text-muted">
              {data.stats.totalBenchPct !== null
                ? (locale === "he" ? "אלפא" : "alpha")
                : ""}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted">
              {locale === "he" ? "היום הטוב ביותר" : "Best day"}
            </div>
            <div className="font-semibold text-base">
              {data.stats.best ? (
                <PctCell value={data.stats.best.pct} />
              ) : (
                <span className="text-muted">—</span>
              )}
            </div>
            <div className="text-xs text-muted tabular">
              {data.stats.best?.date ?? ""}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted">
              {locale === "he" ? "היום הגרוע ביותר" : "Worst day"}
            </div>
            <div className="font-semibold text-base">
              {data.stats.worst ? (
                <PctCell value={data.stats.worst.pct} />
              ) : (
                <span className="text-muted">—</span>
              )}
            </div>
            <div className="text-xs text-muted tabular">
              {data.stats.worst?.date ?? ""}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
