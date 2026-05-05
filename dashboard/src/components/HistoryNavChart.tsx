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
            tickFormatter={(v: number) =>
              `$${(v / 1000).toFixed(1)}k`
            }
            tick={{ fontSize: 11 }}
            domain={["auto", "auto"]}
            width={56}
          />
          <Tooltip
            formatter={(v: number) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
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
