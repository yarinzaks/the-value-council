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
import {
  ChartTooltip,
  axisProps,
  rowNumber,
  seriesPalette,
  seriesStyle,
  useChartTheme,
} from "./ChartKit";
import {
  buildCompareRows,
  drawableKeys,
  rebase,
  type SeriesPoint,
} from "@/lib/compare-series";
import { useUI } from "./Providers";
import type { DailySnapshot, NavRow } from "@/lib/types";

// ---- types --------------------------------------------------------------

type Period = "1W" | "1M" | "3M" | "ALL";

/** What the y-axis measures.
 *
 *  "nav" is the book in dollars — the answer to "how much do I have".
 *  "compare" rebases everything to 0% at the start of the window,
 *  which is the only way a $12,087 book and an $18.52 share can share
 *  an axis, and the answer to "did this beat the market, and which
 *  name carried it".
 */
type Mode = "nav" | "compare";

/** The book's own line in the comparison view. Not a ticker, so it
 *  cannot collide with one. */
const PORTFOLIO_KEY = "__portfolio__";

const BENCHMARK_KEYS = ["SPY", "QQQ"] as const;

const BENCHMARK_LABEL: Record<string, { en: string; he: string }> = {
  SPY: { en: "S&P 500", he: "S&P 500" },
  QQQ: { en: "Nasdaq 100", he: "נאסד\"ק 100" },
};

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
  /** A year of closes per ticker, for the comparison view: the held
   *  names plus SPY and QQQ, as published by core.live.price_export.
   *  Absent keys simply cannot be selected. */
  series?: Record<string, SeriesPoint[]>;
  /** Held tickers in book order, so the selector reads like the
   *  position table above it rather than alphabetically. */
  holdings?: string[];
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
  series = {},
  holdings = [],
}: Props) {
  const { locale, t } = useUI();
  const [period, setPeriod] = useState<Period>("1M");
  const [mode, setMode] = useState<Mode>("nav");
  // The market first. A reader opening the comparison wants to know
  // whether the book beat the index; which holding carried it is the
  // second question, and nine lines at once answers neither.
  const [selected, setSelected] = useState<string[]>(["SPY"]);

  const data = useMemo(() => {
    if (snapshots.length === 0) return { rows: [], hasBenchmark: false, stats: null };

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
    if (windowed.length === 0) return { rows: [], hasBenchmark: false, stats: null };

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

    // Whether the benchmark series overlaps this window at all.
    //
    // Load-bearing. `benchmark` is the *backtest* NAV series, which ends
    // 2024-12-31, while `snapshots` are live and start 2026-04-01. The
    // two date sets are disjoint by construction, so every lookup above
    // misses and `benchmarkNav` is undefined on every row. Recharts then
    // draws a <path> with an empty `d` — an invisible line claiming a
    // comparison that was never computed. Rendering the line only when
    // the data exists keeps the DOM honest; the stats row below already
    // reports the same absence as "—".
    //
    // The comparison comes back the day the live runner records a
    // benchmark NAV per snapshot. Nothing here needs to change then.
    const hasBenchmark = rows.some((r) => r.benchmarkNav !== undefined);

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
    // The backtest's benchmark first, for the windows it covers; then
    // the published SPY series, which is what the live era actually
    // has. Until this existed the stat read "—" on every live page,
    // promising a comparison the data could not make.
    const spy = rebase(
      rows.map((r) => r.date),
      series.SPY ?? [],
    );
    const spyEnd = spy.length > 0 ? spy[spy.length - 1] : null;
    const totalBenchPct =
      benchStart !== undefined && benchEnd !== undefined && benchStart > 0
        ? ((benchEnd - benchStart) / benchStart) * 100
        : spyEnd;
    const totalAgentUsd = windowed[windowed.length - 1].nav - windowed[0].nav;

    return {
      rows,
      hasBenchmark,
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
  }, [snapshots, benchmark, period, series]);

  // The comparison series, built from the same window the NAV chart
  // drew. The book becomes a series like any other so one function
  // rebases all of them — otherwise the portfolio and the tickers
  // would be normalised by two code paths that have to agree.
  const comparison = useMemo(() => {
    const dates = data.rows.map((r) => r.date);
    const withPortfolio: Record<string, SeriesPoint[]> = {
      ...series,
      [PORTFOLIO_KEY]: data.rows.map((r) => ({ d: r.date, c: r.agentNav })),
    };
    const wanted = [PORTFOLIO_KEY, ...selected];
    return {
      rows: buildCompareRows(dates, withPortfolio, wanted),
      keys: drawableKeys(dates, withPortfolio, wanted),
    };
  }, [data.rows, series, selected]);

  // What the selector can offer: a benchmark or a holding we actually
  // published a file for. Offering a chip that draws nothing is worse
  // than not offering it.
  const selectable = useMemo(
    () =>
      [...BENCHMARK_KEYS, ...holdings].filter(
        (k) => (series[k]?.length ?? 0) > 0,
      ),
    [series, holdings],
  );

  const labelFor = (key: string): string => {
    if (key === PORTFOLIO_KEY) return agentName;
    const bench = BENCHMARK_LABEL[key];
    return bench ? bench[locale === "he" ? "he" : "en"] : key;
  };

  // Before the empty-state return: a hook cannot sit behind a branch.
  const theme = useChartTheme();
  const axis = axisProps(theme);

  if (data.rows.length === 0) {
    return (
      <Card>
        <p className="text-sm text-muted text-center py-8">
          {t("no_history")}
        </p>
      </Card>
    );
  }

  // #16a34a is 3.28:1 on white. The token pair exists because of it.
  const lineColor =
    data.stats && data.stats.totalAgentPct >= 0 ? theme.gain : theme.loss;
  const periods: Period[] = ["1W", "1M", "3M", "ALL"];

  return (
    <Card>
      {/* Period selector */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <h3 className="text-sm font-semibold">
          {locale === "he" ? "ביצועי תיק" : "Portfolio performance"} · {agentName}
        </h3>
        <div className="flex items-center gap-2 flex-wrap">
        <div className="flex gap-1 bg-council-100 dark:bg-council-800 rounded-full p-1">
          {(["nav", "compare"] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              aria-pressed={mode === m}
              className={`pressable focus-ring px-3 py-1 text-xs font-medium rounded-full ${
                mode === m
                  ? "bg-white dark:bg-council-700 shadow-elev-1 text-council-900 dark:text-council-100"
                  : "text-muted hover:text-council-700 dark:hover:text-council-300"
              }`}
            >
              {m === "nav"
                ? locale === "he"
                  ? "שווי"
                  : "Value"
                : locale === "he"
                  ? "השוואה"
                  : "Compare"}
            </button>
          ))}
        </div>
        <div className="flex gap-1 bg-council-100 dark:bg-council-800 rounded-full p-1">
          {periods.map((p) => (
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
              {p === "ALL" ? (locale === "he" ? "הכל" : "ALL") : p}
            </button>
          ))}
        </div>
        </div>
      </div>

      {/* Which lines to draw. Only in the comparison view, because in
          dollars there is nothing to compare against — $12,087 of book
          and $18.52 of Smithfield do not share a scale. */}
      {mode === "compare" && selectable.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {selectable.map((key, i) => {
            const on = selected.includes(key);
            const palette = seriesPalette(theme, agentColor);
            // Position among the drawn lines, less the portfolio, so a
            // chip carries the colour of its own line.
            const drawn = comparison.keys.indexOf(key);
            const colour = seriesStyle(
              drawn > 0 ? drawn - 1 : i,
              palette,
            ).stroke;
            return (
              <button
                key={key}
                onClick={() =>
                  setSelected((prev) =>
                    prev.includes(key)
                      ? prev.filter((k) => k !== key)
                      : [...prev, key],
                  )
                }
                aria-pressed={on}
                className={`pressable focus-ring px-2.5 py-1 text-xs rounded-full border transition-colors ${
                  on
                    ? "border-transparent text-white dark:text-council-900"
                    : "border-council-200 dark:border-council-700 text-muted hover:text-council-700 dark:hover:text-council-300"
                }`}
                style={on ? { backgroundColor: colour } : undefined}
              >
                {labelFor(key)}
              </button>
            );
          })}
          {selected.length > 0 && (
            <button
              onClick={() => setSelected([])}
              className="pressable focus-ring px-2.5 py-1 text-xs rounded-full text-muted hover:text-council-700 dark:hover:text-council-300"
            >
              {locale === "he" ? "נקה" : "Clear"}
            </button>
          )}
          {selected.length < selectable.length && (
            <button
              onClick={() => setSelected(selectable)}
              className="pressable focus-ring px-2.5 py-1 text-xs rounded-full text-muted hover:text-council-700 dark:hover:text-council-300"
            >
              {locale === "he" ? "הכל" : "All"}
            </button>
          )}
        </div>
      )}

      {/* Chart */}
      {mode === "compare" ? (
        <div className="h-72 w-full" dir="ltr">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={comparison.rows}
              margin={{ top: 5, right: 10, bottom: 5, left: 5 }}
            >
              <CartesianGrid
                vertical={false}
                strokeDasharray="3 3"
                stroke={theme.grid}
              />
              <XAxis
                dataKey="date"
                minTickGap={30}
                tickFormatter={(d: string) => d.slice(5)}
                {...axis}
              />
              <YAxis
                tickFormatter={(v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(0)}%`}
                domain={["auto", "auto"]}
                width={60}
                {...axis}
              />
              {/* Zero is the start of the window, and the only line on
                  this chart that means something on its own: above it
                  is a gain since then, below it a loss. */}
              <ReferenceLine
                y={0}
                stroke={theme.benchmark}
                strokeDasharray="3 3"
                strokeOpacity={0.6}
              />
              <Tooltip
                cursor={{ stroke: theme.axis, strokeWidth: 1 }}
                content={
                  <ChartTooltip
                    formatName={(entry) => labelFor(String(entry.dataKey))}
                    formatValue={(value) =>
                      `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
                    }
                  />
                }
              />
              {comparison.keys.map((key, i) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={
                    key === PORTFOLIO_KEY
                      ? agentColor
                      : seriesStyle(i - 1, seriesPalette(theme, agentColor))
                          .stroke
                  }
                  strokeDasharray={
                    key === PORTFOLIO_KEY
                      ? undefined
                      : seriesStyle(i - 1, seriesPalette(theme, agentColor))
                          .strokeDasharray
                  }
                  // The book is the subject; the rest are the measure
                  // it is held against.
                  strokeWidth={key === PORTFOLIO_KEY ? 2.5 : 1.5}
                  dot={false}
                  activeDot={{ r: 3, strokeWidth: 2, stroke: theme.surface }}
                  isAnimationActive={false}
                  name={labelFor(key)}
                  // A name bought mid-window has no closes before it;
                  // the line should start late, not be stitched back
                  // to the axis.
                  connectNulls={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
      <div className="h-72 w-full" dir="ltr">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data.rows} margin={{ top: 5, right: 10, bottom: 5, left: 5 }}>
            <CartesianGrid
              vertical={false}
              strokeDasharray="3 3"
              stroke={theme.grid}
            />
            <XAxis
              dataKey="date"
              minTickGap={30}
              tickFormatter={(d: string) => d.slice(5)}
              {...axis}
            />
            <YAxis
              tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`}
              domain={["auto", "auto"]}
              width={60}
              {...axis}
            />
            {data.stats && (
              <ReferenceLine
                y={data.stats.startNav}
                stroke={theme.benchmark}
                strokeDasharray="3 3"
                strokeOpacity={0.5}
                label={{
                  value: `$${data.stats.startNav.toFixed(0)}`,
                  position: "right",
                  fill: theme.tick,
                  fontSize: 10,
                }}
              />
            )}
            <Tooltip
              cursor={{ stroke: theme.axis, strokeWidth: 1 }}
              content={
                <ChartTooltip
                  formatName={(entry) =>
                    entry.dataKey === "benchmarkNav"
                      ? locale === "he"
                        ? "מדד S&P 500"
                        : "S&P 500"
                      : agentName
                  }
                  formatValue={(value, entry) => {
                    const money = `$${value.toLocaleString("en-US", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}`;
                    // The NAV alone does not say whether the agent is
                    // up; the percent beside it is the thing the
                    // reader came for.
                    const pct = rowNumber(
                      entry,
                      entry.dataKey === "benchmarkNav"
                        ? "benchmarkPct"
                        : "agentPct",
                    );
                    if (pct === null) return money;
                    return `${money} (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)`;
                  }}
                />
              }
            />
            <Line
              type="monotone"
              dataKey="agentNav"
              stroke={lineColor}
              strokeWidth={2.5}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: theme.surface }}
              isAnimationActive={false}
              name={agentName}
            />
            {data.hasBenchmark && (
              <Line
                type="monotone"
                dataKey="benchmarkNav"
                stroke={theme.benchmark}
                strokeWidth={1.5}
                strokeDasharray="4 4"
                dot={false}
                activeDot={{ r: 3, strokeWidth: 2, stroke: theme.surface }}
                isAnimationActive={false}
                name="S&P 500"
                connectNulls
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
      )}

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
