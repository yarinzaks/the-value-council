"use client";

// Where an agent's money actually sits.
//
// A position list says what is owned; it does not say what the investor
// is doing. Ten holdings in banks and ten spread across manufacturing,
// utilities and retail are the same list length and opposite stances,
// and that difference is the most legible thing about a value
// investor. Weighted by market value, not by name count — three 8%
// positions are a bigger bet than five 1% ones.

import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

export interface SectorSlice {
  key: string;
  label: string;
  /** Percent of invested value. */
  pct: number;
  count: number;
}

// Distinguishable at a glance and legible on both themes. Unknown is
// deliberately grey — it is an absence, and colouring it like a sector
// would make it look like one.
const PALETTE = [
  "#2563eb", "#0d9488", "#d97706", "#7c3aed", "#dc2626",
  "#0891b2", "#65a30d", "#c026d3", "#ea580c", "#475569",
];
const UNKNOWN_COLOR = "#94a3b8";

export function SectorDonut({
  slices,
  emptyLabel,
}: {
  slices: SectorSlice[];
  emptyLabel: string;
}) {
  if (slices.length === 0) {
    return <p className="text-xs text-muted">{emptyLabel}</p>;
  }
  const colorFor = (key: string, i: number) =>
    key === "unknown" ? UNKNOWN_COLOR : PALETTE[i % PALETTE.length];

  return (
    <div className="flex flex-wrap items-center gap-4" dir="ltr">
      <div className="h-44 w-44 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={slices}
              dataKey="pct"
              nameKey="label"
              innerRadius="58%"
              outerRadius="92%"
              paddingAngle={1.5}
              isAnimationActive={false}
              stroke="none"
            >
              {slices.map((s, i) => (
                <Cell key={s.key} fill={colorFor(s.key, i)} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                fontSize: 12,
                borderRadius: 8,
                border: "1px solid rgba(120,120,120,.25)",
              }}
              formatter={(v: number, n: string) => [`${v.toFixed(1)}%`, n]}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>

      {/* The legend carries the numbers. A donut alone tells you one
          slice is bigger; the reader wants to know by how much. */}
      <ul className="flex-1 min-w-[12rem] space-y-1">
        {slices.map((s, i) => (
          <li key={s.key} className="flex items-center gap-2 text-xs">
            <span
              className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
              style={{ backgroundColor: colorFor(s.key, i) }}
            />
            <span className="flex-1 text-council-700 dark:text-council-300">
              {s.label}
            </span>
            <span className="tabular font-medium">{s.pct.toFixed(1)}%</span>
            <span className="tabular text-muted w-8 text-end">{s.count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
