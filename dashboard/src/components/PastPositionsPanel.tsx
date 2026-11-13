"use client";

// The positions an agent used to hold: entry, exit, and what came after.
//
// A name the agent sold left the page the day it left the book, so the
// first question a reader asks about a sale — was it right? — had no
// answer. This draws one closed position at a time from its entry,
// through its exit, to the newest published close, with the book and
// the index over the same days, and lists every closing underneath.
//
// The lines are rebased to 0% at the entry date, so the exit marker
// sits where the holding ended and everything to its right is what
// the agent gave up or avoided by leaving.

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
import { Money, PctCell } from "./Cards";
import {
  ChartTooltip,
  axisProps,
  readableOn,
  seriesPalette,
  seriesStyle,
  useChartTheme,
} from "./ChartKit";
import { useUI } from "./Providers";
import { buildCompareRows, drawableKeys, type SeriesPoint } from "@/lib/compare-series";
import type { ClosedPosition } from "@/lib/past-positions";
import type { DailySnapshot } from "@/lib/types";

const PORTFOLIO_KEY = "__portfolio__";
const SPY_KEY = "SPY";

/** How many closings get a chip. Graham has sold a hundred lines; a
 *  hundred chips is a wall, and the table below lists every one and
 *  picks on click. */
const MAX_CHIPS = 24;

/** How many days before an unknown entry the chart opens. A row written
 *  before the entry fields existed has an exit and nothing else; a month
 *  of context behind it is better than a line that starts at the sale. */
const UNKNOWN_ENTRY_LEAD_DAYS = 30;

function minusDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

interface Props {
  closed: ClosedPosition[];
  series: Readonly<Record<string, SeriesPoint[]>>;
  snapshots: DailySnapshot[];
  agentColor: string;
  agentName: string;
}

export function PastPositionsPanel({ closed, series, snapshots, agentColor, agentName }: Props) {
  const { locale, t } = useUI();
  const theme = useChartTheme();
  const axis = axisProps(theme);
  const [picked, setPicked] = useState<string | null>(closed[0]?.key ?? null);
  const row = closed.find((c) => c.key === picked) ?? closed[0] ?? null;

  const chart = useMemo(() => {
    if (!row) return { rows: [], keys: [] as string[] };
    const points = series[row.ticker] ?? [];
    const from = row.entryDate ?? minusDays(row.exitDate, UNKNOWN_ENTRY_LEAD_DAYS);
    const dates = points
      .map((p) => p.d)
      .filter((d) => d >= from)
      .sort();
    const withBook: Record<string, SeriesPoint[]> = {
      ...series,
      [PORTFOLIO_KEY]: snapshots.map((s) => ({ d: s.date, c: s.nav })),
    };
    const wanted = [row.ticker, PORTFOLIO_KEY, SPY_KEY];
    return {
      rows: buildCompareRows(dates, withBook, wanted),
      keys: drawableKeys(dates, withBook, wanted),
    };
  }, [row, series, snapshots]);

  const labelFor = (key: string) =>
    key === PORTFOLIO_KEY ? agentName : key === SPY_KEY ? "S&P 500" : key;
  const strokeFor = (key: string, i: number) =>
    key === PORTFOLIO_KEY
      ? agentColor
      : key === SPY_KEY
        ? theme.benchmark
        : seriesStyle(i, seriesPalette(theme, agentColor)).stroke;

  if (closed.length === 0) {
    return (
      <p className="text-sm text-muted text-center py-8">{t("past_none")}</p>
    );
  }

  const palette = seriesPalette(theme, agentColor);

  return (
    <div>
      {/* One position at a time: the chips are the closings, newest first. */}
      <div className="flex flex-wrap gap-1.5 mb-3">
        {closed.slice(0, MAX_CHIPS).map((c, i) => {
          const on = c.key === row?.key;
          const colour = seriesStyle(i, palette).stroke;
          return (
            <button
              key={c.key}
              onClick={() => setPicked(c.key)}
              aria-pressed={on}
              className={`pressable focus-ring inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-full border transition-colors ${
                on
                  ? "border-transparent"
                  : "border-council-200 dark:border-council-700 text-muted hover:text-council-700 dark:hover:text-council-300"
              }`}
              style={on ? { backgroundColor: colour, color: readableOn(colour) } : undefined}
            >
              <span dir="ltr">{c.ticker}</span>
              <span className="tabular opacity-80" dir="ltr">{c.exitDate.slice(2)}</span>
            </button>
          );
        })}
        {closed.length > MAX_CHIPS && (
          <span className="self-center text-xs text-muted">
            +{closed.length - MAX_CHIPS} {t("past_more_in_table")}
          </span>
        )}
      </div>

      {row && (
        <p className="text-xs text-muted mb-3">
          {row.entryDate ? (
            <>
              {t("past_window_from")} <span className="tabular" dir="ltr">{row.entryDate}</span>{" "}
              {t("past_window_to")} <span className="tabular" dir="ltr">{row.exitDate}</span>
              {row.heldDays !== null && (
                <>
                  {" · "}
                  {row.heldDays} {t("past_days")}
                </>
              )}
            </>
          ) : (
            t("past_entry_unknown")
          )}
          {row.reconstructed && <> · {t("past_reconstructed")}</>}
          {row.sinceExitDate === null && <> · {t("past_no_series_after")}</>}
        </p>
      )}

      {chart.rows.length > 0 ? (
        <div className="h-72 w-full" dir="ltr">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chart.rows} margin={{ top: 5, right: 10, bottom: 5, left: 5 }}>
              <CartesianGrid vertical={false} strokeDasharray="3 3" stroke={theme.grid} />
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
              <ReferenceLine y={0} stroke={theme.benchmark} strokeDasharray="3 3" strokeOpacity={0.6} />
              {row && chart.rows.some((r) => r.date === row.exitDate) && (
                <ReferenceLine
                  x={row.exitDate}
                  stroke={theme.loss}
                  strokeDasharray="4 2"
                  label={{
                    value: t("past_exit_marker"),
                    position: "insideTopRight",
                    fill: theme.tick,
                    fontSize: 10,
                  }}
                />
              )}
              <Tooltip
                cursor={{ stroke: theme.axis, strokeWidth: 1 }}
                content={
                  <ChartTooltip
                    formatName={(entry) => labelFor(String(entry.dataKey))}
                    formatValue={(value) => `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`}
                  />
                }
              />
              {chart.keys.map((key, i) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={strokeFor(key, i)}
                  strokeDasharray={key === SPY_KEY ? "4 2" : undefined}
                  strokeWidth={key === row?.ticker ? 2.5 : 1.5}
                  dot={false}
                  activeDot={{ r: 3, strokeWidth: 2, stroke: theme.surface }}
                  isAnimationActive={false}
                  name={labelFor(key)}
                  connectNulls={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="text-sm text-muted text-center py-8">{t("past_no_chart")}</p>
      )}

      {/* Every closing, newest first. */}
      <div className="overflow-x-auto mt-4">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-muted border-b border-council-200 dark:border-council-800">
              <th className="py-2 pr-3">{t("col_ticker")}</th>
              <th className="py-2 pr-3">{t("col_entry")}</th>
              <th className="py-2 pr-3">{t("past_col_exit")}</th>
              <th className="py-2 pr-3 text-right">{t("past_col_held")}</th>
              <th className="py-2 pr-3 text-right">{t("past_col_realized")}</th>
              <th className="py-2 pr-3 text-right">{t("past_col_since_exit")}</th>
              <th className="py-2 pl-3 text-right">{t("past_col_spy_since_exit")}</th>
            </tr>
          </thead>
          <tbody>
            {closed.map((c) => (
              <tr
                key={c.key}
                onClick={() => setPicked(c.key)}
                className={`cursor-pointer border-b border-council-100 dark:border-council-800 last:border-b-0 hover:bg-council-50 dark:hover:bg-council-800/30 ${
                  c.key === row?.key ? "font-semibold" : ""
                }`}
              >
                <td className="py-2 pr-3" dir="ltr">
                  {c.ticker}
                  {c.reconstructed && (
                    <span className="ms-1 text-xs text-muted" title={t("past_reconstructed")}>
                      ≈
                    </span>
                  )}
                </td>
                <td className="py-2 pr-3 tabular" dir="ltr">
                  {c.entryDate ?? "—"}
                  {c.entryPrice !== null && (
                    <span className="text-muted"> · ${c.entryPrice.toFixed(2)}</span>
                  )}
                </td>
                <td className="py-2 pr-3 tabular" dir="ltr">
                  {c.exitDate}
                  <span className="text-muted"> · ${c.exitPrice.toFixed(2)}</span>
                </td>
                <td className="py-2 pr-3 text-right">
                  <PctCell value={c.heldPct} />
                </td>
                <td className="py-2 pr-3 text-right">
                  <Money value={c.realizedPnlUsd} signed digits={2} />
                </td>
                <td className="py-2 pr-3 text-right">
                  <PctCell value={c.sinceExitPct} />
                </td>
                <td className="py-2 pl-3 text-right">
                  <PctCell value={c.spySinceExitPct} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-xs text-muted mt-2">{t("past_table_note")}</p>
      </div>
      <span className="sr-only">{locale}</span>
    </div>
  );
}
