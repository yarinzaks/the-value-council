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
  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 5 }}>
          <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.2} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11 }}
            minTickGap={20}
          />
          <YAxis
            tickFormatter={(v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`}
            tick={{ fontSize: 11 }}
            domain={["auto", "auto"]}
            width={56}
          />
          {/* Zero is where every agent starts in this window, so the
              line separating gains from losses has to be visible. */}
          <ReferenceLine y={0} strokeOpacity={0.45} strokeDasharray="4 4" />
          <Tooltip
            formatter={(v: number) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%`}
          />
          <Legend />
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={2}
              dot={{ r: 3 }}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
