"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartTooltip, axisProps, useChartTheme } from "./ChartKit";

interface Series {
  key: string;
  color: string;
}

export function HistoryNavChart({
  data,
  series,
}: {
  data: Array<Record<string, number | string>>;
  series: Series[];
}) {
  const theme = useChartTheme();
  const axis = axisProps(theme);
  const pct = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;

  return (
    <div className="h-72 w-full" dir="ltr">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 5 }}>
          <CartesianGrid
            vertical={false}
            stroke={theme.grid}
            strokeDasharray="3 3"
          />
          <XAxis dataKey="date" minTickGap={20} {...axis} />
          <YAxis
            tickFormatter={(v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`}
            domain={["auto", "auto"]}
            width={56}
            {...axis}
          />
          {/* Zero is where every agent starts in this window, so the
              line separating gains from losses has to be visible —
              and has to be the one rule on the chart that reads as a
              statement rather than as background measurement. */}
          <ReferenceLine
            y={0}
            stroke={theme.tick}
            strokeOpacity={0.5}
            strokeDasharray="4 4"
          />
          <Tooltip
            cursor={{ stroke: theme.axis, strokeWidth: 1 }}
            content={<ChartTooltip formatValue={pct} />}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: theme.tick }}
            iconType="plainline"
          />
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={2}
              dot={{ r: 3 }}
              activeDot={{
                r: 5,
                strokeWidth: 2,
                stroke: theme.dark ? "#11182a" : "#fff",
              }}
              connectNulls
              // See NavChart: the entry animation leaves the path with a
              // "0, L" stroke-dasharray whenever its frames never run,
              // which renders a fully-populated line invisibly.
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
