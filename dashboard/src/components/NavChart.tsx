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

export function NavChart({
  data,
  agentColor,
}: {
  data: NavRow[];
  agentColor: string;
}) {
  // Down-sample to ~250 points so the chart renders fast even for
  // 5-year daily series (~1260 points).
  const target = 260;
  const step = Math.max(1, Math.floor(data.length / target));
  const sampled = data.filter((_, i) => i % step === 0 || i === data.length - 1);

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={sampled} margin={{ top: 5, right: 20, bottom: 5, left: 5 }}>
          <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.2} />
          <XAxis
            dataKey="date"
            tickFormatter={(d: string) => d.slice(0, 7)}
            minTickGap={36}
            tick={{ fontSize: 11 }}
          />
          <YAxis
            tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
            tick={{ fontSize: 11 }}
            width={56}
          />
          <Tooltip
            formatter={(v: number) => `$${v.toFixed(0)}`}
            labelFormatter={(d: string) => d}
          />
          <Legend />
          <Line
            type="monotone"
            dataKey="nav"
            name="Strategy"
            stroke={agentColor}
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="benchmark_nav"
            name="S&P 500"
            stroke="#94a3b8"
            strokeWidth={1.5}
            strokeDasharray="4 4"
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
