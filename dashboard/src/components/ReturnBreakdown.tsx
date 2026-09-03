"use client";

// Where a book's return actually came from.
//
// Graham stood at +24.55% with four of his five open positions losing
// money, and the two facts read as a contradiction until the return is
// split: the open book was down $704 and closed trades had made $3,239.
// Nothing on the dashboard said so, and asked which stocks produced the
// gain the assistant could only answer that it did not know.
//
// Why a waterfall and not a donut
// -------------------------------
//
// Two of the parts are negative — the open loss and the transaction
// costs. A donut cannot draw a negative slice, so it would have had to
// drop them or show them as positive, and either one hides exactly the
// thing that confused the reader in the first place. A waterfall reads
// left to right as a story: this is what you started with, this is what
// each part added or took away, this is what you have now. The bars sum
// to the final NAV by construction, so the arithmetic is checkable by
// eye rather than taken on trust.

import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface ReturnParts {
  initial_cash: number;
  realized: number;
  unrealized: number;
  dividends: number;
  costs: number;
  nav: number;
  attributed_total: number;
  unattributed: number;
  ledger_is_reconstructed: boolean;
}

export interface Contributor {
  ticker: string;
  realized_usd: number;
}

const GAIN = "#16a34a";
const LOSS = "#dc2626";
const ANCHOR = "#64748b";

interface Step {
  key: string;
  label: string;
  /** Invisible pedestal the visible bar sits on. */
  base: number;
  /** Height of the visible bar. Always positive; sign lives in `delta`. */
  size: number;
  delta: number;
  anchor: boolean;
}

/** Turn the parts into bars whose tops trace the running total. */
export function toSteps(
  parts: ReturnParts,
  labels: Record<string, string>,
): Step[] {
  const steps: Step[] = [];
  let running = parts.initial_cash;

  steps.push({
    key: "initial",
    label: labels.initial,
    base: 0,
    size: parts.initial_cash,
    delta: parts.initial_cash,
    anchor: true,
  });

  // Costs are stored positive and spend money, so the sign is flipped
  // here rather than at the source — the book's own field means "what
  // was paid", and a negative number there would be a different claim.
  const moves: Array<[string, string, number]> = [
    ["realized", labels.realized, parts.realized],
    ["unrealized", labels.unrealized, parts.unrealized],
    ["dividends", labels.dividends, parts.dividends],
    ["costs", labels.costs, -parts.costs],
  ];

  for (const [key, label, delta] of moves) {
    // A bar that would be invisible is still kept: "dividends made no
    // difference" is information, and a gap in the sequence would read
    // as the part being missing rather than being zero.
    steps.push({
      key,
      label,
      base: delta >= 0 ? running : running + delta,
      size: Math.abs(delta),
      delta,
      anchor: false,
    });
    running += delta;
  }

  steps.push({
    key: "nav",
    label: labels.nav,
    base: 0,
    size: parts.nav,
    delta: parts.nav,
    anchor: true,
  });
  return steps;
}

const money = (n: number, locale: string) =>
  n.toLocaleString(locale === "he" ? "he-IL" : "en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });

const signedMoney = (n: number, locale: string) =>
  (n > 0 ? "+" : "") + money(n, locale);

export function ReturnBreakdown({
  parts,
  contributors,
  labels,
  locale = "en",
}: {
  parts: ReturnParts;
  contributors: Contributor[];
  labels: Record<string, string>;
  locale?: string;
}) {
  const steps = toSteps(parts, labels);
  const named = contributors.filter((c) => c.realized_usd !== 0);
  const share =
    parts.realized !== 0 ? parts.attributed_total / parts.realized : 0;

  return (
    <div className="space-y-4">
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={steps} margin={{ top: 24, right: 8, bottom: 4, left: 8 }}>
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11 }}
              interval={0}
              axisLine={false}
              tickLine={false}
            />
            <YAxis hide domain={["dataMin - 500", "dataMax + 500"]} />
            <ReferenceLine y={parts.initial_cash} strokeDasharray="3 3" stroke={ANCHOR} />
            <Tooltip
              cursor={{ fill: "transparent" }}
              formatter={(_v, _n, item) => {
                const step = item?.payload as Step | undefined;
                if (!step) return ["", ""];
                return [signedMoney(step.delta, locale), step.label];
              }}
              labelFormatter={() => ""}
            />
            {/* The pedestal. Present so each bar starts where the last
                one ended; never drawn. */}
            <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
            <Bar dataKey="size" stackId="w" isAnimationActive={false} radius={[2, 2, 0, 0]}>
              {steps.map((s) => (
                <Cell
                  key={s.key}
                  fill={s.anchor ? ANCHOR : s.delta >= 0 ? GAIN : LOSS}
                />
              ))}
              <LabelList
                dataKey="delta"
                position="top"
                fontSize={11}
                formatter={(v: unknown) => signedMoney(Number(v), locale)}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {named.length > 0 && (
        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <span className="text-xs font-medium">{labels.contributors}</span>
            <span className="text-[11px] text-muted">
              {labels.named_share.replace("{pct}", `${Math.round(share * 100)}%`)}
            </span>
          </div>
          <ul className="space-y-1">
            {named.slice(0, 8).map((c) => (
              <li
                key={c.ticker}
                className="flex items-baseline justify-between text-sm"
              >
                <span className="tabular">{c.ticker}</span>
                <span
                  className="tabular"
                  style={{ color: c.realized_usd >= 0 ? GAIN : LOSS }}
                >
                  {signedMoney(c.realized_usd, locale)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* The remainder, said out loud. A breakdown that quietly dropped
          it would look complete and be wrong by that amount. */}
      {Math.abs(parts.unattributed) >= 1 && (
        <p className="rounded border border-amber-500/40 bg-amber-500/5 p-2 text-[11px] leading-relaxed text-muted">
          {labels.unattributed_note
            .replace("{amount}", money(parts.unattributed, locale))
            .replace("{pct}", `${Math.round((1 - share) * 100)}%`)}
        </p>
      )}

      {parts.ledger_is_reconstructed && (
        <p className="text-[11px] leading-relaxed text-muted">
          {labels.reconstructed_note}
        </p>
      )}
    </div>
  );
}
