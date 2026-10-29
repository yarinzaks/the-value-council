"use client";

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
import type { NavRow } from "@/lib/types";
import { ChartTooltip, axisProps, useChartTheme } from "./ChartKit";

export function NavChart({
  data,
  agentColor,
}: {
  data: NavRow[];
  agentColor: string;
}) {
  const theme = useChartTheme();
  const axis = axisProps(theme);

  // Down-sample to ~250 points so the chart renders fast even for
  // 5-year daily series (~1260 points).
  const target = 260;
  const step = Math.max(1, Math.floor(data.length / target));
  const sampled = data.filter((_, i) => i % step === 0 || i === data.length - 1);

  return (
    <div className="h-72 w-full" dir="ltr">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={sampled} margin={{ top: 5, right: 20, bottom: 5, left: 5 }}>
          {/* Horizontal only. The vertical rules measured nothing a
              reader of a NAV series asks for, and two grids crossing
              under a line is most of what makes a chart look busy. */}
          <CartesianGrid
            vertical={false}
            stroke={theme.grid}
            strokeDasharray="3 3"
          />
          <XAxis
            dataKey="date"
            tickFormatter={(d: string) => d.slice(0, 7)}
            minTickGap={36}
            {...axis}
          />
          <YAxis
            tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
            width={56}
            {...axis}
          />
          <Tooltip
            cursor={{ stroke: theme.axis, strokeWidth: 1 }}
            content={
              <ChartTooltip
                formatValue={(v) => `$${v.toLocaleString("en-US", {
                  maximumFractionDigits: 0,
                })}`}
              />
            }
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: theme.tick }}
            iconType="plainline"
          />
          {/* isAnimationActive={false} is load-bearing, not a preference.
              Recharts draws its entry animation by animating
              stroke-dasharray from "0, L" (nothing visible) to "L, 0"
              (the whole line), driven by requestAnimationFrame. A tab
              that is backgrounded, or restored from bfcache, never runs
              those frames, so the path keeps a fully populated `d` and a
              dasharray of "0px 1477px" — present in the DOM, invisible
              on screen. That is the empty chart box. The charts that
              never had the bug (SectorDonut, AgentPerformanceChart,
              PositionPriceChart) are exactly the ones already opting
              out. */}
          <Line
            type="monotone"
            dataKey="nav"
            name="Strategy"
            stroke={agentColor}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, strokeWidth: 2, stroke: theme.dark ? "#11182a" : "#fff" }}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="benchmark_nav"
            name="S&P 500"
            stroke={theme.benchmark}
            strokeWidth={1.5}
            strokeDasharray="4 4"
            dot={false}
            activeDot={{ r: 3, strokeWidth: 2, stroke: theme.dark ? "#11182a" : "#fff" }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
