"use client";

// The line from entry to today, with the entry marked on it.
//
// The position page could show what the agent paid and what the mark is
// now, and nothing in between — so a holding that round-tripped 30% and
// came back looked identical to one that never moved. This draws the
// path.
//
// Shaded from the entry price rather than from zero: what matters on a
// position is the distance from cost, not the absolute level, and a
// baseline at zero compresses every real move into the top of the frame.

import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { PricePoint } from "@/lib/data";

interface Props {
  points: PricePoint[];
  entryPrice: number | null;
  entryDate: string | null;
  /** Agent colour, so the chart belongs to the agent whose page it is. */
  color: string;
  labels: {
    entry: string;
    price: string;
  };
}

export function PositionPriceChart({
  points,
  entryPrice,
  entryDate,
  color,
  labels,
}: Props) {
  if (points.length < 2) return null;

  // Only draw the marker if the entry date is actually inside the
  // window — an older position would otherwise pin it to the left edge
  // and imply the agent bought on a day this chart does not cover.
  const entryPoint =
    entryDate && points.some((p) => p.d === entryDate)
      ? points.find((p) => p.d === entryDate)
      : null;

  const closes = points.map((p) => p.c);
  const lo = Math.min(...closes, entryPrice ?? Infinity);
  const hi = Math.max(...closes, entryPrice ?? -Infinity);
  const pad = (hi - lo) * 0.08 || hi * 0.02;

  const up = entryPrice !== null && points[points.length - 1].c >= entryPrice;
  const stroke = color;

  return (
    <div className="h-56 w-full" dir="ltr">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="posFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.22} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="2 4" className="stroke-council-200 dark:stroke-council-800" vertical={false} />
          <XAxis
            dataKey="d"
            tick={{ fontSize: 11 }}
            className="fill-council-400"
            minTickGap={48}
            tickFormatter={(d: string) => d.slice(5)}
          />
          <YAxis
            domain={[lo - pad, hi + pad]}
            tick={{ fontSize: 11 }}
            className="fill-council-400"
            width={52}
            tickFormatter={(v: number) => `$${v.toFixed(0)}`}
          />
          <Tooltip
            contentStyle={{
              fontSize: 12,
              borderRadius: 8,
              border: "1px solid rgba(120,120,120,.25)",
            }}
            labelFormatter={(d) => String(d)}
            formatter={(v: number) => [`$${v.toFixed(2)}`, labels.price]}
          />
          {entryPrice !== null && (
            <ReferenceLine
              y={entryPrice}
              stroke={up ? "#16a34a" : "#dc2626"}
              strokeDasharray="4 4"
              strokeOpacity={0.7}
              label={{
                value: `${labels.entry} $${entryPrice.toFixed(2)}`,
                position: "insideTopLeft",
                fontSize: 11,
                fill: up ? "#16a34a" : "#dc2626",
              }}
            />
          )}
          <Area
            type="monotone"
            dataKey="c"
            stroke={stroke}
            strokeWidth={2}
            fill="url(#posFill)"
            dot={false}
            isAnimationActive={false}
          />
          {entryPoint && (
            <ReferenceDot
              x={entryPoint.d}
              y={entryPoint.c}
              r={4}
              fill={stroke}
              stroke="#fff"
              strokeWidth={1.5}
              isFront
            />
          )}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
