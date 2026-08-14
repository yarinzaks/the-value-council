"use client";

// The donut's next question, answered beside it.
//
// The donut says a quarter of this book is in manufacturing. The reader
// immediately wants to know whether that was a good place to have it.
// One line per sector, each a fixed-weight index of the agent's own
// holdings there, rebased to 100 so sectors with different share prices
// are comparable on one axis.
//
// Colours come from the donut's own function, so a colour means the same
// sector in both charts. That is the whole reason the pair works.

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { sectorColor } from "./SectorDonut";
import type { SectorTrend } from "@/lib/sector-trend";

export function SectorTrendChart({
  trends,
  rows,
  emptyLabel,
  baseLabel,
}: {
  trends: SectorTrend[];
  rows: Record<string, string | number>[];
  emptyLabel: string;
  /** "= 100 at {date}" — the rebasing, stated on the axis. */
  baseLabel: string;
}) {
  if (trends.length === 0 || rows.length < 2) {
    return <p className="text-xs text-muted">{emptyLabel}</p>;
  }

  // Index order must match the donut's, since sectorColor keys off it.
  const colorOf = new Map(trends.map((t, i) => [t.key, sectorColor(t.key, i)]));

  return (
    <div dir="ltr">
      <div className="h-44">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(120,120,120,.18)" />
            <XAxis
              dataKey="d"
              tick={{ fontSize: 10 }}
              minTickGap={40}
              tickFormatter={(d: string) => d.slice(2, 7)}
            />
            <YAxis
              tick={{ fontSize: 10 }}
              domain={["auto", "auto"]}
              width={44}
            />
            <Tooltip
              contentStyle={{
                fontSize: 12,
                borderRadius: 8,
                border: "1px solid rgba(120,120,120,.25)",
              }}
              formatter={(v: number, key: string) => [
                `${v.toFixed(1)} (${v >= 100 ? "+" : "−"}${Math.abs(v - 100).toFixed(1)}%)`,
                trends.find((t) => t.key === key)?.label ?? key,
              ]}
            />
            <Legend
              wrapperStyle={{ fontSize: 11 }}
              formatter={(key: string) =>
                trends.find((t) => t.key === key)?.label ?? key
              }
            />
            {trends.map((t) => (
              <Line
                key={t.key}
                type="monotone"
                dataKey={t.key}
                stroke={colorOf.get(t.key)}
                strokeWidth={1.8}
                dot={false}
                // The entry animation leaves the path with a full `d`
                // and a stroke-dasharray of "0, L" — a chart that is
                // present in the DOM and invisible on screen. Every
                // other chart here carries the same flag for the same
                // reason.
                isAnimationActive={false}
                connectNulls={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* What each line did, and out of which names. The chart shows
          shape; the reader wants the number and the constituents. */}
      <ul className="mt-3 space-y-1">
        {trends.map((t) => (
          <li key={t.key} className="flex items-center gap-2 text-xs">
            <span
              className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
              style={{ backgroundColor: colorOf.get(t.key) }}
            />
            <span className="text-council-700 dark:text-council-300">
              {t.label}
            </span>
            <span className="text-muted truncate flex-1">
              {t.tickers.join(", ")}
            </span>
            <span
              className={`tabular font-medium shrink-0 ${
                t.changePct >= 0 ? "text-gain" : "text-loss"
              }`}
            >
              {t.changePct >= 0 ? "+" : "−"}
              {Math.abs(t.changePct).toFixed(1)}%
            </span>
          </li>
        ))}
      </ul>

      <p className="mt-2 text-[11px] text-muted">{baseLabel}</p>
    </div>
  );
}
